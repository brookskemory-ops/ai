--!strict
--[[
	Config — every tunable number for the gacha lives here and nowhere else.

	This module is pure data: it requires nothing, touches no Roblox API, and is
	safe to read from both the server and the client. The client needs it to
	render the published odds table; the server needs it to actually roll.

	Nothing here is authoritative on its own. The server re-reads this table on
	every roll, so a client that edits its own copy changes only what it sees.
]]

local Config = {}

-- Ordered worst -> best. Order matters: the roll falls through these in order
-- and the UI sorts by it.
Config.Rarities = { "Common", "Rare", "Epic", "Legendary" }

Config.RarityRank = {
	Common = 1,
	Rare = 2,
	Epic = 3,
	Legendary = 4,
}

-- Card tints, as hex the client turns into Color3. Kept as plain numbers so
-- this module stays free of Roblox types and can run under the CLI test suite.
Config.RarityColor = {
	Common = 0xB0B6C2,
	Rare = 0x54A0FF,
	Epic = 0xB26CFF,
	Legendary = 0xFFBD4A,
}

-- Pulling a unit you already own converts to shards instead. Shard costs for
-- upgrades live in your own progression system; this kit only grants them.
Config.DuplicateShards = {
	Common = 1,
	Rare = 5,
	Epic = 25,
	Legendary = 150,
}

-- A duplicate raises the unit's star level until this cap, then pays shards.
Config.MaxStars = 5

Config.Currency = {
	id = "Gems",
	displayName = "Gems",
	startingBalance = 1600,
}

--[[
	Items. `id` is the key; ids are what the server stores in a player's
	inventory, so never renumber or reuse one after release.
]]
Config.Items = {
	-- Legendary
	sol_seraph = { name = "Sol, the Seraph", rarity = "Legendary" },
	umbral_wyrm = { name = "Umbral Wyrm", rarity = "Legendary" },
	glacier_king = { name = "Glacier King", rarity = "Legendary" },
	-- Epic
	ember_knight = { name = "Ember Knight", rarity = "Epic" },
	tide_caller = { name = "Tide Caller", rarity = "Epic" },
	gale_scout = { name = "Gale Scout", rarity = "Epic" },
	stone_warden = { name = "Stone Warden", rarity = "Epic" },
	dusk_archer = { name = "Dusk Archer", rarity = "Epic" },
	-- Rare
	iron_recruit = { name = "Iron Recruit", rarity = "Rare" },
	spark_adept = { name = "Spark Adept", rarity = "Rare" },
	brine_sailor = { name = "Brine Sailor", rarity = "Rare" },
	thorn_ranger = { name = "Thorn Ranger", rarity = "Rare" },
	-- Common
	militia_pike = { name = "Militia Pike", rarity = "Common" },
	field_medic = { name = "Field Medic", rarity = "Common" },
	scrap_golem = { name = "Scrap Golem", rarity = "Common" },
	dust_imp = { name = "Dust Imp", rarity = "Common" },
}

--[[
	Banners.

	rates          Base per-pull probabilities with no pity applied. Must sum to 1.
	pity.Legendary hard        guaranteed Legendary on this pull of the cycle
	               softStart   pull at which the rate starts climbing
	               softStep    added probability per pull from softStart on
	pity.Epic      hard        guaranteed Epic-or-better on this pull of the cycle
	featured       chance      probability a hit of that rarity is a featured unit
	               guaranteeAfterLoss  a non-featured hit makes the next one featured
	               ids         the rate-up units, which must NOT also be in `pool`
	pool           the off-rate-up units for each rarity

	A Legendary resets both the Legendary and the Epic counters. An Epic resets
	only the Epic counter. This matches what players expect from the genre, and
	the odds solver in GachaMath models it exactly.
]]
Config.Banners = {
	standard = {
		id = "standard",
		displayName = "Standard Summon",
		costPerRoll = 160,
		multi = { count = 10, cost = 1600 },
		rates = { Common = 0.79, Rare = 0.15, Epic = 0.051, Legendary = 0.009 },
		pity = {
			Legendary = { hard = 90, softStart = 74, softStep = 0.06 },
			Epic = { hard = 10 },
		},
		featured = nil,
		pool = {
			Common = { "militia_pike", "field_medic", "scrap_golem", "dust_imp" },
			Rare = { "iron_recruit", "spark_adept", "brine_sailor", "thorn_ranger" },
			Epic = { "ember_knight", "tide_caller", "gale_scout", "stone_warden", "dusk_archer" },
			Legendary = { "sol_seraph", "umbral_wyrm", "glacier_king" },
		},
	},

	solar_ascendant = {
		id = "solar_ascendant",
		displayName = "Solar Ascendant",
		costPerRoll = 160,
		multi = { count = 10, cost = 1600 },
		rates = { Common = 0.79, Rare = 0.15, Epic = 0.051, Legendary = 0.009 },
		pity = {
			Legendary = { hard = 90, softStart = 74, softStep = 0.06 },
			Epic = { hard = 10 },
		},
		featured = {
			Legendary = { chance = 0.5, guaranteeAfterLoss = true, ids = { "sol_seraph" } },
			Epic = { chance = 0.5, guaranteeAfterLoss = true, ids = { "ember_knight", "tide_caller" } },
		},
		pool = {
			Common = { "militia_pike", "field_medic", "scrap_golem", "dust_imp" },
			Rare = { "iron_recruit", "spark_adept", "brine_sailor", "thorn_ranger" },
			Epic = { "gale_scout", "stone_warden", "dusk_archer" },
			Legendary = { "umbral_wyrm", "glacier_king" },
		},
	},
}

--[[
	Developer products that sell the roll currency.

	Replace these ids with your own from the Creator Dashboard; the numbers here
	are placeholders and will not resolve to a real product. A product id that
	is not listed here is never granted — the receipt is left unprocessed so
	Roblox retries it after you fix the table, rather than charging a player for
	nothing.

	If you sell currency that buys random items, Roblox's paid random item
	policy applies: the odds have to be disclosed in-experience before the
	purchase. The rates panel this kit ships is that disclosure — do not remove
	it and keep it reachable from the banner itself.
]]
Config.Products = {
	[111111111] = { gems = 500, label = "Handful of Gems" },
	[222222222] = { gems = 2800, label = "Pouch of Gems" },
	[333333333] = { gems = 8400, label = "Chest of Gems" },
}

-- Requests above this rate are dropped and logged. A legitimate client cannot
-- exceed it; an exploiting one will.
Config.RateLimit = {
	burst = 6,
	refillPerSecond = 2,
}

return Config
