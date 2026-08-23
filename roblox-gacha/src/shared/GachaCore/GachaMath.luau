--!strict
--[[
	GachaMath — the entire probability model, as pure functions.

	Nothing in here touches a Roblox API, a DataStore, or a player. That is
	deliberate: it means the part of the game that decides what you pulled can
	be run millions of times under a plain Luau interpreter and checked against
	its own closed-form math (see ../../../tests).

	The server calls `roll`. The client calls `Rates.published` to draw the odds
	panel. Both read the same banner table, so the number on screen and the
	number the server rolls against cannot drift apart.
]]

local GachaMath = {}

--============================================================================
-- Types
--============================================================================

export type FeaturedRule = {
	chance: number,
	guaranteeAfterLoss: boolean,
	ids: { string },
}

export type PityRule = {
	hard: number,
	softStart: number?,
	softStep: number?,
}

export type Banner = {
	id: string,
	displayName: string,
	costPerRoll: number,
	multi: { count: number, cost: number },
	rates: { [string]: number },
	pity: { [string]: PityRule },
	featured: { [string]: FeaturedRule }?,
	pool: { [string]: { string } },
}

-- Per-player, per-banner. Persisted verbatim in the player's profile.
export type PityState = {
	pity: { [string]: number },
	guaranteed: { [string]: boolean },
	totalRolls: number,
}

export type Outcome = {
	itemId: string,
	rarity: string,
	featured: boolean,
	hardPity: boolean, -- this pull was forced by the hard-pity floor
	pullNumber: number, -- lifetime pulls on this banner, including this one
	legendaryPity: number, -- counters as they stand after this pull
	epicPity: number,
}

-- Must return a float in [0, 1). `Random.new():NextNumber()` qualifies; see
-- RollService for why the server uses a dedicated Random object rather than
-- the shared `math.random` state.
export type Rng = () -> number

--============================================================================
-- State
--============================================================================

function GachaMath.newState(): PityState
	return {
		pity = { Legendary = 0, Epic = 0 },
		guaranteed = { Legendary = false, Epic = false },
		totalRolls = 0,
	}
end

--[[
	Repairs state loaded from a DataStore that predates a config change or was
	written by an older build. Always run loaded state through this before
	rolling with it — a nil counter would otherwise error mid-roll, after the
	currency has already been spent.
]]
function GachaMath.normalizeState(raw: any): PityState
	local state = GachaMath.newState()
	if type(raw) ~= "table" then
		return state
	end
	if type(raw.pity) == "table" then
		for _, rarity in { "Legendary", "Epic" } do
			local value = raw.pity[rarity]
			if type(value) == "number" and value >= 0 then
				state.pity[rarity] = math.floor(value)
			end
		end
	end
	if type(raw.guaranteed) == "table" then
		for _, rarity in { "Legendary", "Epic" } do
			state.guaranteed[rarity] = raw.guaranteed[rarity] == true
		end
	end
	if type(raw.totalRolls) == "number" and raw.totalRolls >= 0 then
		state.totalRolls = math.floor(raw.totalRolls)
	end
	return state
end

--============================================================================
-- The chance curve
--============================================================================

--[[
	Probability of a Legendary on `pull`, where `pull` is 1-based within the
	current pity cycle (1 = the first pull after the last Legendary).

	Below softStart it is the flat base rate. From softStart it climbs by
	softStep per pull. At `hard` it is 1. This is the shape every large gacha
	uses, and it is why the advertised base rate is always well below the rate
	players actually experience.
]]
function GachaMath.legendaryChance(banner: Banner, pull: number): number
	local rule = banner.pity.Legendary
	if pull >= rule.hard then
		return 1
	end
	local softStart = rule.softStart
	if softStart and pull >= softStart then
		local step = rule.softStep or 0
		return math.min(1, banner.rates.Legendary + step * (pull - softStart + 1))
	end
	return banner.rates.Legendary
end

-- Chance of an Epic given the pull was not a Legendary. Rescaled so that at
-- zero pity the unconditional Epic rate equals the advertised base rate.
local function epicChanceGivenNotLegendary(banner: Banner, pull: number): number
	if pull >= banner.pity.Epic.hard then
		return 1
	end
	return banner.rates.Epic / (1 - banner.rates.Legendary)
end

--============================================================================
-- Rolling
--============================================================================

local function pick(rng: Rng, list: { string }): string
	local index = math.floor(rng() * #list) + 1
	-- rng() is specified as [0, 1); clamp anyway so a sloppy Rng cannot
	-- produce a nil item id.
	return list[math.clamp(index, 1, #list)]
end

local function resolveFeatured(banner: Banner, state: PityState, rarity: string, rng: Rng): (boolean, FeaturedRule?)
	local featuredTable = banner.featured
	local rule = featuredTable and featuredTable[rarity]
	if not rule then
		return false, nil
	end
	if state.guaranteed[rarity] then
		state.guaranteed[rarity] = false
		return true, rule
	end
	local won = rng() < rule.chance
	if not won and rule.guaranteeAfterLoss then
		state.guaranteed[rarity] = true
	end
	return won, rule
end

--[[
	Rolls once. Mutates `state` in place — counters, guarantee flags and the
	lifetime total — and returns what was pulled.

	The caller is responsible for having already taken payment. Payment and roll
	must happen inside the same server-side critical section; see RollService.
]]
function GachaMath.roll(banner: Banner, state: PityState, rng: Rng): Outcome
	local legendaryPull = state.pity.Legendary + 1
	local epicPull = state.pity.Epic + 1

	local rarity: string
	local hardPity = false

	if rng() < GachaMath.legendaryChance(banner, legendaryPull) then
		rarity = "Legendary"
		hardPity = legendaryPull >= banner.pity.Legendary.hard
	elseif rng() < epicChanceGivenNotLegendary(banner, epicPull) then
		rarity = "Epic"
		hardPity = epicPull >= banner.pity.Epic.hard
	else
		local rare, common = banner.rates.Rare, banner.rates.Common
		rarity = if rng() < rare / (rare + common) then "Rare" else "Common"
	end

	-- A Legendary resets both counters; an Epic resets only its own.
	if rarity == "Legendary" then
		state.pity.Legendary = 0
		state.pity.Epic = 0
	elseif rarity == "Epic" then
		state.pity.Legendary = legendaryPull
		state.pity.Epic = 0
	else
		state.pity.Legendary = legendaryPull
		state.pity.Epic = epicPull
	end

	local featured, rule = resolveFeatured(banner, state, rarity, rng)
	local candidates = if featured and rule then rule.ids else banner.pool[rarity]

	state.totalRolls += 1

	return {
		itemId = pick(rng, candidates),
		rarity = rarity,
		featured = featured,
		hardPity = hardPity,
		pullNumber = state.totalRolls,
		legendaryPity = state.pity.Legendary,
		epicPity = state.pity.Epic,
	}
end

-- Rolls `count` times against the same evolving state. A ten-pull is exactly
-- ten single pulls, which is what makes the Epic floor land inside it.
function GachaMath.rollMany(banner: Banner, state: PityState, rng: Rng, count: number): { Outcome }
	local outcomes = table.create(count)
	for _ = 1, count do
		table.insert(outcomes, GachaMath.roll(banner, state, rng))
	end
	return outcomes
end

--============================================================================
-- Published odds
--
-- Roblox requires a paid random item to disclose its odds, and players will
-- work out the real numbers regardless. These are derived from the same banner
-- table the server rolls against, so the disclosure cannot go stale.
--============================================================================

local Rates = {}
GachaMath.Rates = Rates

export type Consolidated = {
	Common: number,
	Rare: number,
	Epic: number,
	Legendary: number,
	LegendaryFeatured: number,
	EpicFeatured: number,
}

local consolidatedCache: { [any]: Consolidated } = setmetatable({}, { __mode = "k" }) :: any

--[[
	Expected number of Legendaries needed to land one featured Legendary.

	With a 50/50 and a guarantee on the next hit after a loss: winning now
	(chance c) costs one, losing costs two. So c + 2(1 - c) = 2 - c. Without the
	guarantee it is the plain 1/c.
]]
local function legendariesPerFeatured(banner: Banner): number
	local featuredTable = banner.featured
	local rule = featuredTable and featuredTable.Legendary
	if not rule then
		return math.huge
	end
	if rule.chance >= 1 then
		return 1
	end
	if rule.guaranteeAfterLoss then
		return 2 - rule.chance
	end
	return 1 / rule.chance
end

--[[
	The rate a player actually experiences, averaged over the whole pity cycle.

	Solved, not simulated. The pair (legendary counter, epic counter) is a
	Markov chain with `hard * epicHard` states — 900 on the default banner — so
	we power-iterate to its stationary distribution and average each rarity's
	per-state probability against it. Deterministic, and fast enough that the
	client can call it to draw the rates panel.
]]
function Rates.consolidated(banner: Banner): Consolidated
	local cached = consolidatedCache[banner]
	if cached then
		return cached
	end

	local legendaryHard = banner.pity.Legendary.hard
	local epicHard = banner.pity.Epic.hard
	local size = legendaryHard * epicHard
	local function index(l: number, e: number): number
		return l * epicHard + e + 1
	end

	-- Per-state transition probabilities, hoisted out of the iteration loop.
	local pLegendaryAt = table.create(legendaryHard, 0)
	for l = 0, legendaryHard - 1 do
		pLegendaryAt[l + 1] = GachaMath.legendaryChance(banner, l + 1)
	end
	local pEpicAt = table.create(epicHard, 0)
	for e = 0, epicHard - 1 do
		pEpicAt[e + 1] = epicChanceGivenNotLegendary(banner, e + 1)
	end

	local dist = table.create(size, 0)
	dist[index(0, 0)] = 1
	local nextDist = table.create(size, 0)

	for _ = 1, 4000 do
		for i = 1, size do
			nextDist[i] = 0
		end
		for l = 0, legendaryHard - 1 do
			for e = 0, epicHard - 1 do
				local mass = dist[index(l, e)]
				if mass > 0 then
					local pLegendary = pLegendaryAt[l + 1]
					local pEpic = (1 - pLegendary) * pEpicAt[e + 1]
					local pOther = 1 - pLegendary - pEpic
					nextDist[index(0, 0)] += mass * pLegendary
					if pEpic > 0 then
						nextDist[index(math.min(l + 1, legendaryHard - 1), 0)] += mass * pEpic
					end
					if pOther > 1e-15 then
						nextDist[index(math.min(l + 1, legendaryHard - 1), math.min(e + 1, epicHard - 1))] += mass
							* pOther
					end
				end
			end
		end
		local delta = 0
		for i = 1, size do
			delta += math.abs(nextDist[i] - dist[i])
			dist[i] = nextDist[i]
		end
		if delta < 1e-14 then
			break
		end
	end

	local legendary, epic = 0, 0
	for l = 0, legendaryHard - 1 do
		for e = 0, epicHard - 1 do
			local mass = dist[index(l, e)]
			if mass > 0 then
				local pLegendary = pLegendaryAt[l + 1]
				legendary += mass * pLegendary
				epic += mass * (1 - pLegendary) * pEpicAt[e + 1]
			end
		end
	end

	local remainder = 1 - legendary - epic
	local rareShare = banner.rates.Rare / (banner.rates.Rare + banner.rates.Common)
	local featuredTable = banner.featured
	local epicRule = featuredTable and featuredTable.Epic

	local result: Consolidated = {
		Legendary = legendary,
		Epic = epic,
		Rare = remainder * rareShare,
		Common = remainder * (1 - rareShare),
		LegendaryFeatured = legendary / legendariesPerFeatured(banner),
		EpicFeatured = if epicRule then epic * epicRule.chance else 0,
	}
	consolidatedCache[banner] = result
	return result
end

function Rates.expectedRollsPerLegendary(banner: Banner): number
	return 1 / Rates.consolidated(banner).Legendary
end

function Rates.expectedRollsPerFeaturedLegendary(banner: Banner): number
	local rate = Rates.consolidated(banner).LegendaryFeatured
	return if rate > 0 then 1 / rate else math.huge
end

--[[
	Rows for the odds panel, in display order. `base` is the advertised no-pity
	rate; `consolidated` is what pity actually delivers. Publish both. Showing
	only the base rate understates the odds and reads as a lie the moment
	players datamine it; showing only the consolidated rate hides the mechanic.
]]
export type OddsRow = {
	label: string,
	rarity: string,
	base: number,
	consolidated: number,
}

function Rates.published(banner: Banner): { OddsRow }
	local consolidated = Rates.consolidated(banner)
	local rows: { OddsRow } = {}
	for _, rarity in { "Legendary", "Epic", "Rare", "Common" } do
		table.insert(rows, {
			label = rarity,
			rarity = rarity,
			base = banner.rates[rarity],
			consolidated = (consolidated :: any)[rarity],
		})
	end
	local featuredTable = banner.featured
	if featuredTable and featuredTable.Legendary then
		table.insert(rows, 2, {
			label = "Legendary — featured unit",
			rarity = "Legendary",
			base = banner.rates.Legendary * featuredTable.Legendary.chance,
			consolidated = consolidated.LegendaryFeatured,
		})
	end
	return rows
end

--============================================================================
-- Validation
--
-- Run at server start. A banner that fails here would otherwise fail during a
-- roll, after the player has already been charged.
--============================================================================

local RARITIES = { "Common", "Rare", "Epic", "Legendary" }

function GachaMath.validate(banner: Banner, items: { [string]: any }?): (boolean, string?)
	local function fail(message: string): (boolean, string)
		return false, `banner "{banner.id}": {message}`
	end

	if type(banner.id) ~= "string" or banner.id == "" then
		return false, "banner is missing an id"
	end
	if type(banner.costPerRoll) ~= "number" or banner.costPerRoll <= 0 then
		return fail("costPerRoll must be a positive number")
	end
	if type(banner.multi) ~= "table" or banner.multi.count < 1 or banner.multi.cost <= 0 then
		return fail("multi must have a positive count and cost")
	end

	local sum = 0
	for _, rarity in RARITIES do
		local rate = banner.rates[rarity]
		if type(rate) ~= "number" or rate < 0 then
			return fail(`rates.{rarity} must be a non-negative number`)
		end
		sum += rate
	end
	if math.abs(sum - 1) > 1e-9 then
		return fail(`rates sum to {string.format("%.6f", sum)}, expected 1`)
	end

	for _, rarity in { "Legendary", "Epic" } do
		local rule = banner.pity[rarity]
		if type(rule) ~= "table" or type(rule.hard) ~= "number" or rule.hard < 1 or rule.hard % 1 ~= 0 then
			return fail(`pity.{rarity}.hard must be a whole number >= 1`)
		end
		local softStart = rule.softStart
		if softStart then
			if softStart < 1 or softStart > rule.hard then
				return fail(`pity.{rarity}.softStart must be between 1 and hard`)
			end
			if (rule.softStep or 0) < 0 then
				return fail(`pity.{rarity}.softStep must not be negative`)
			end
		end
	end

	local featuredTable = banner.featured
	for _, rarity in RARITIES do
		local pool = banner.pool[rarity]
		if type(pool) ~= "table" then
			return fail(`pool.{rarity} is missing`)
		end
		local rule = featuredTable and featuredTable[rarity]
		if #pool == 0 and not (rule and rule.chance >= 1) then
			return fail(`pool.{rarity} is empty but featured units are not guaranteed`)
		end
		local seen: { [string]: boolean } = {}
		for _, id in pool do
			if seen[id] then
				return fail(`pool.{rarity} lists {id} twice`)
			end
			seen[id] = true
			if items then
				local item = items[id]
				if not item then
					return fail(`pool.{rarity} references unknown item {id}`)
				end
				if item.rarity ~= rarity then
					return fail(`{id} is in pool.{rarity} but is defined as {tostring(item.rarity)}`)
				end
			end
		end
		if rule then
			if rule.chance < 0 or rule.chance > 1 then
				return fail(`featured.{rarity}.chance must be between 0 and 1`)
			end
			if #rule.ids == 0 then
				return fail(`featured.{rarity} has no ids`)
			end
			for _, id in rule.ids do
				if seen[id] then
					return fail(`{id} appears in both featured.{rarity} and pool.{rarity}`)
				end
				if items then
					local item = items[id]
					if not item then
						return fail(`featured.{rarity} references unknown item {id}`)
					end
					if item.rarity ~= rarity then
						return fail(`{id} is featured as {rarity} but is defined as {tostring(item.rarity)}`)
					end
				end
			end
		end
	end

	return true, nil
end

return GachaMath
