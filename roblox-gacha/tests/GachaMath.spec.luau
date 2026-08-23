--!strict
local testkit = require("./testkit")
local GachaMath = require("../src/shared/GachaCore/GachaMath")
local Config = require("../src/shared/GachaCore/Config")

local test = testkit.test
local assertTrue = testkit.assert
local equal = testkit.equal
local near = testkit.near

type Banner = GachaMath.Banner

local featuredBanner: Banner = (Config.Banners.solar_ascendant :: any) :: Banner
local standardBanner: Banner = (Config.Banners.standard :: any) :: Banner

local function copyBanner(banner: Banner): any
	local function deepCopy(value: any): any
		if type(value) ~= "table" then
			return value
		end
		local out = {}
		for k, v in value do
			out[k] = deepCopy(v)
		end
		return out
	end
	return deepCopy(banner)
end

--============================================================================
testkit.suite("validate")
--============================================================================

test("accepts the shipped banners", function()
	for id, banner in Config.Banners do
		local ok, err = GachaMath.validate((banner :: any) :: Banner, Config.Items)
		assertTrue(ok, `banner {id} should validate but got: {tostring(err)}`)
	end
end)

test("rejects rates that do not sum to 1", function()
	local banner = copyBanner(standardBanner)
	banner.rates.Legendary = 0.02
	local ok, err = GachaMath.validate(banner, Config.Items)
	equal(ok, false, "should reject rates that do not sum to 1")
	assertTrue(string.find(tostring(err), "sum") ~= nil, `error should mention the sum, got: {tostring(err)}`)
end)

test("rejects a featured unit that is also in the off-rate pool", function()
	local banner = copyBanner(featuredBanner)
	table.insert(banner.pool.Legendary, "sol_seraph")
	local ok, err = GachaMath.validate(banner, Config.Items)
	equal(ok, false, "should reject a unit that is both featured and in the pool")
	assertTrue(string.find(tostring(err), "sol_seraph") ~= nil, `error should name the unit, got: {tostring(err)}`)
end)

test("rejects an item id that does not exist", function()
	local banner = copyBanner(standardBanner)
	table.insert(banner.pool.Rare, "ghost_unit")
	local ok = GachaMath.validate(banner, Config.Items)
	equal(ok, false, "should reject an unknown item id")
end)

test("rejects an item filed under the wrong rarity", function()
	local banner = copyBanner(standardBanner)
	table.insert(banner.pool.Rare, "sol_seraph")
	local ok = GachaMath.validate(banner, Config.Items)
	equal(ok, false, "should reject a Legendary sitting in the Rare pool")
end)

test("rejects an empty pool with no featured fallback", function()
	local banner = copyBanner(standardBanner)
	banner.pool.Epic = {}
	local ok = GachaMath.validate(banner, Config.Items)
	equal(ok, false, "should reject an empty pool")
end)

--============================================================================
testkit.suite("chance curve")
--============================================================================

test("is the flat base rate before soft pity", function()
	for pull = 1, 73 do
		equal(
			GachaMath.legendaryChance(featuredBanner, pull),
			featuredBanner.rates.Legendary,
			`pull {pull} should still be at the base rate`
		)
	end
end)

test("climbs through soft pity and reaches certainty at the hard floor", function()
	near(GachaMath.legendaryChance(featuredBanner, 74), 0.009 + 0.06, 1e-12, "first soft pity pull")
	near(GachaMath.legendaryChance(featuredBanner, 75), 0.009 + 0.12, 1e-12, "second soft pity pull")
	equal(GachaMath.legendaryChance(featuredBanner, 90), 1, "hard pity must be certain")
	equal(GachaMath.legendaryChance(featuredBanner, 200), 1, "past hard pity must stay certain")
end)

test("never decreases", function()
	local previous = 0
	for pull = 1, 90 do
		local chance = GachaMath.legendaryChance(featuredBanner, pull)
		assertTrue(chance >= previous, `chance dropped at pull {pull}`)
		previous = chance
	end
end)

--============================================================================
testkit.suite("rolling")
--============================================================================

test("never exceeds the hard pity floor, over 200k pulls", function()
	local rng = testkit.rng(11)
	local state = GachaMath.newState()
	local sinceLegendary = 0
	local worst = 0
	for _ = 1, 200000 do
		local outcome = GachaMath.roll(featuredBanner, state, rng)
		sinceLegendary += 1
		if outcome.rarity == "Legendary" then
			worst = math.max(worst, sinceLegendary)
			sinceLegendary = 0
		end
	end
	assertTrue(worst <= 90, `went {worst} pulls without a Legendary; the floor is 90`)
	assertTrue(worst >= 80, `worst drought was only {worst}, which suggests pity is firing early`)
end)

test("an unlucky player still gets a Legendary on pull 90", function()
	-- An rng that always returns just under 1 loses every roll it is allowed to
	-- lose, so only the guaranteed floors can fire.
	local rng = function(): number
		return 0.9999999
	end
	local state = GachaMath.newState()
	for pull = 1, 89 do
		local outcome = GachaMath.roll(featuredBanner, state, rng)
		assertTrue(outcome.rarity ~= "Legendary", `pull {pull} should not have been a Legendary`)
	end
	local outcome = GachaMath.roll(featuredBanner, state, rng)
	equal(outcome.rarity, "Legendary", "pull 90 must be a Legendary")
	equal(outcome.hardPity, true, "pull 90 should be flagged as hard pity")
	equal(state.pity.Legendary, 0, "the counter should reset")
	equal(state.pity.Epic, 0, "a Legendary should reset the Epic counter too")
end)

test("the worst possible ten-pull still contains an Epic", function()
	local rng = function(): number
		return 0.9999999
	end
	local state = GachaMath.newState()
	local outcomes = GachaMath.rollMany(featuredBanner, state, rng, 10)
	local best = "Common"
	for _, outcome in outcomes do
		if Config.RarityRank[outcome.rarity] > Config.RarityRank[best] then
			best = outcome.rarity
		end
	end
	equal(best, "Epic", "the tenth pull should be an Epic floor")
	equal(outcomes[10].hardPity, true, "the tenth pull should be flagged as hard pity")
end)

test("only ever returns items that exist at the rarity it claims", function()
	local rng = testkit.rng(29)
	local state = GachaMath.newState()
	for _ = 1, 50000 do
		local outcome = GachaMath.roll(featuredBanner, state, rng)
		local item = Config.Items[outcome.itemId]
		assertTrue(item ~= nil, `rolled an unknown item id: {tostring(outcome.itemId)}`)
		equal(item.rarity, outcome.rarity, `{outcome.itemId} was returned as {outcome.rarity}`)
	end
end)

test("a featured pull always comes from the rate-up list", function()
	local rng = testkit.rng(4)
	local state = GachaMath.newState()
	local featuredIds = { sol_seraph = true, ember_knight = true, tide_caller = true }
	local sawFeatured = false
	for _ = 1, 50000 do
		local outcome = GachaMath.roll(featuredBanner, state, rng)
		if outcome.featured then
			sawFeatured = true
			assertTrue(featuredIds[outcome.itemId] == true, `{outcome.itemId} was flagged featured but is not on rate-up`)
		else
			assertTrue(featuredIds[outcome.itemId] ~= true, `{outcome.itemId} is on rate-up but was not flagged featured`)
		end
	end
	assertTrue(sawFeatured, "50k pulls produced no featured unit at all")
end)

test("two non-featured Legendaries never happen in a row", function()
	local rng = testkit.rng(1337)
	local state = GachaMath.newState()
	local lastLegendaryWasLoss = false
	local losses, wins = 0, 0
	for _ = 1, 400000 do
		local outcome = GachaMath.roll(featuredBanner, state, rng)
		if outcome.rarity == "Legendary" then
			if outcome.featured then
				wins += 1
				lastLegendaryWasLoss = false
			else
				assertTrue(not lastLegendaryWasLoss, "lost the 50/50 twice in a row; the guarantee did not fire")
				losses += 1
				lastLegendaryWasLoss = true
			end
		end
	end
	assertTrue(wins + losses > 4000, `only saw {wins + losses} Legendaries, too few to judge`)
	-- Half the wins are guarantees, so wins should run roughly 2:1 over losses.
	near(wins / (wins + losses), 2 / 3, 0.03, "featured share of Legendaries")
end)

test("a banner with no rate-up never reports a featured pull", function()
	local rng = testkit.rng(88)
	local state = GachaMath.newState()
	for _ = 1, 20000 do
		local outcome = GachaMath.roll(standardBanner, state, rng)
		equal(outcome.featured, false, "the standard banner has no featured units")
	end
end)

test("is reproducible for a given seed", function()
	local function sequence(): { string }
		local rng = testkit.rng(2024)
		local state = GachaMath.newState()
		local ids = {}
		for _ = 1, 500 do
			table.insert(ids, GachaMath.roll(featuredBanner, state, rng).itemId)
		end
		return ids
	end
	local first, second = sequence(), sequence()
	for i, id in first do
		equal(second[i], id, `roll {i} differed between two runs with the same seed`)
	end
end)

test("rollMany advances the lifetime counter once per pull", function()
	local rng = testkit.rng(5)
	local state = GachaMath.newState()
	GachaMath.rollMany(featuredBanner, state, rng, 10)
	equal(state.totalRolls, 10, "ten pulls should count as ten")
	local outcomes = GachaMath.rollMany(featuredBanner, state, rng, 10)
	equal(outcomes[10].pullNumber, 20, "the twentieth pull should say so")
end)

--============================================================================
testkit.suite("state repair")
--============================================================================

test("turns junk from an old save into a usable state", function()
	local state = GachaMath.normalizeState({ pity = { Legendary = "twelve" }, totalRolls = -4 })
	equal(state.pity.Legendary, 0, "a non-numeric counter should reset")
	equal(state.pity.Epic, 0, "a missing counter should default")
	equal(state.guaranteed.Legendary, false, "a missing flag should default to false")
	equal(state.totalRolls, 0, "a negative total should reset")

	local kept = GachaMath.normalizeState({
		pity = { Legendary = 73.9, Epic = 3 },
		guaranteed = { Legendary = true },
		totalRolls = 812,
	})
	equal(kept.pity.Legendary, 73, "a real counter should survive, floored")
	equal(kept.guaranteed.Legendary, true, "a real guarantee flag should survive")
	equal(kept.totalRolls, 812, "a real total should survive")
end)

test("survives being handed nothing at all", function()
	local state = GachaMath.normalizeState(nil)
	equal(state.pity.Legendary, 0, "nil should produce a fresh state")
	local rng = testkit.rng(3)
	local outcome = GachaMath.roll(featuredBanner, state, rng)
	assertTrue(outcome.itemId ~= nil, "a repaired state should be rollable")
end)

--============================================================================
testkit.suite("published odds")
--============================================================================

--[[
	The Legendary rate has a closed form the Markov solver should agree with.
	A pity cycle ends on pull k with probability (product of the misses before
	it) times the chance on pull k, and the long-run rate is one over the mean
	cycle length. If these two disagree, the odds panel is lying to players.
]]
local function closedFormLegendaryRate(banner: Banner): number
	local survive = 1
	local expectedLength = 0
	for pull = 1, banner.pity.Legendary.hard do
		local chance = GachaMath.legendaryChance(banner, pull)
		expectedLength += pull * survive * chance
		survive *= 1 - chance
	end
	return 1 / expectedLength
end

test("the solver agrees with the closed-form Legendary rate", function()
	for _, banner in Config.Banners do
		local typed = (banner :: any) :: Banner
		near(
			GachaMath.Rates.consolidated(typed).Legendary,
			closedFormLegendaryRate(typed),
			1e-10,
			`{typed.id}: solved rate should match the renewal formula`
		)
	end
end)

test("the consolidated rates form a distribution", function()
	local rates = GachaMath.Rates.consolidated(featuredBanner)
	local sum = rates.Common + rates.Rare + rates.Epic + rates.Legendary
	near(sum, 1, 1e-9, "consolidated rarities should sum to 1")
	assertTrue(rates.Legendary > featuredBanner.rates.Legendary, "pity should raise the Legendary rate above base")
	assertTrue(rates.Epic > featuredBanner.rates.Epic, "the ten-pull floor should raise the Epic rate above base")
	assertTrue(rates.LegendaryFeatured < rates.Legendary, "featured rate must be below the overall Legendary rate")
end)

test("the consolidated rates match what 2 million pulls actually produce", function()
	local rng = testkit.rng(777)
	local state = GachaMath.newState()
	local counts = { Common = 0, Rare = 0, Epic = 0, Legendary = 0 }
	local featuredLegendary = 0
	local total = 2000000
	for _ = 1, total do
		local outcome = GachaMath.roll(featuredBanner, state, rng)
		counts[outcome.rarity] += 1
		if outcome.rarity == "Legendary" and outcome.featured then
			featuredLegendary += 1
		end
	end
	local solved = GachaMath.Rates.consolidated(featuredBanner)
	near(counts.Legendary / total, solved.Legendary, 0.0006, "simulated Legendary rate")
	near(counts.Epic / total, solved.Epic, 0.002, "simulated Epic rate")
	near(counts.Rare / total, solved.Rare, 0.003, "simulated Rare rate")
	near(counts.Common / total, solved.Common, 0.003, "simulated Common rate")
	near(featuredLegendary / total, solved.LegendaryFeatured, 0.0006, "simulated featured Legendary rate")
end)

test("reports a believable price for a featured Legendary", function()
	local pulls = GachaMath.Rates.expectedRollsPerFeaturedLegendary(featuredBanner)
	-- ~84 pulls, or about 13,500 gems. If a config change moves this a long way
	-- the banner has been repriced, whether or not anyone meant to reprice it.
	assertTrue(pulls > 75 and pulls < 95, `expected ~84 pulls per featured Legendary, got {string.format("%.1f", pulls)}`)
	local perLegendary = GachaMath.Rates.expectedRollsPerLegendary(featuredBanner)
	near(pulls, perLegendary * 1.5, 1e-9, "the 50/50 with a guarantee costs 1.5 Legendaries per featured")
end)

test("the published table shows both the base and the lived rate", function()
	local rows = GachaMath.Rates.published(featuredBanner)
	assertTrue(#rows == 5, `expected 5 rows on a rate-up banner, got {#rows}`)
	equal(rows[1].label, "Legendary", "the top row should be the Legendary rate")
	for _, row in rows do
		assertTrue(row.base >= 0 and row.base <= 1, `{row.label}: base rate out of range`)
		assertTrue(row.consolidated >= 0 and row.consolidated <= 1, `{row.label}: consolidated rate out of range`)
	end
	local standardRows = GachaMath.Rates.published(standardBanner)
	equal(#standardRows, 4, "a banner with no rate-up has no featured row")
end)

return true
