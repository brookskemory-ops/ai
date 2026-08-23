--!strict
--[[
	Prints what a banner actually costs, before you ship it.

		luau tools/simulate.luau [trials]

	Rates come from the exact solver; the percentiles come from simulation,
	because "what does the unlucky player pay" is a question about the tail, and
	the tail is where players quit. Run this after every change to Config.
]]

local GachaMath = require("../src/shared/GachaCore/GachaMath")
local Config = require("../src/shared/GachaCore/Config")

type Banner = GachaMath.Banner

-- luau passes script arguments after `-a`: luau tools/simulate.luau -a 50000
local trials = tonumber((...)) or 200000

local function percent(value: number): string
	return string.format("%6.3f%%", value * 100)
end

-- string.format pads by bytes, and the odds labels contain an em dash, so pad
-- by character count instead or the columns drift.
local function pad(text: string, width: number): string
	local length = utf8.len(text) or #text
	return text .. string.rep(" ", math.max(0, width - length))
end

local function commas(value: number): string
	local text = string.format("%d", math.floor(value + 0.5))
	local out = text:reverse():gsub("(%d%d%d)", "%1,"):reverse()
	return (out:gsub("^,", ""))
end

--[[
	Luau's math.random is seeded per run; seeding it explicitly keeps the
	numbers below reproducible between runs of this script.
]]
math.randomseed(20260823)
local function rng(): number
	return math.random()
end

local function simulate(banner: Banner)
	local pullsToFeatured = table.create(trials)
	local pullsToAnyLegendary = table.create(trials)
	for _ = 1, trials do
		local state = GachaMath.newState()
		local pulls = 0
		local firstLegendary: number? = nil
		while true do
			pulls += 1
			local outcome = GachaMath.roll(banner, state, rng)
			if outcome.rarity == "Legendary" then
				if not firstLegendary then
					firstLegendary = pulls
				end
				if outcome.featured or not banner.featured then
					break
				end
			end
		end
		table.insert(pullsToFeatured, pulls)
		table.insert(pullsToAnyLegendary, firstLegendary or pulls)
	end
	table.sort(pullsToFeatured)
	table.sort(pullsToAnyLegendary)
	return pullsToFeatured, pullsToAnyLegendary
end

local function quantile(sorted: { number }, q: number): number
	return sorted[math.clamp(math.floor(q * #sorted) + 1, 1, #sorted)]
end

local bannerIds = {}
for id in Config.Banners do
	table.insert(bannerIds, id)
end
table.sort(bannerIds)

for _, id in bannerIds do
	local banner = (Config.Banners[id] :: any) :: Banner

	local ok, err = GachaMath.validate(banner, Config.Items)
	print("")
	print(string.rep("=", 68))
	print(`{banner.displayName}  ({banner.id})`)
	print(string.rep("=", 68))
	if not ok then
		print(`  INVALID: {tostring(err)}`)
		continue
	end

	print("")
	print("  rarity                       advertised    with pity")
	print("  " .. string.rep("-", 60))
	for _, row in GachaMath.Rates.published(banner) do
		print(`  {pad(row.label, 28)} {percent(row.base)}     {percent(row.consolidated)}`)
	end

	local perLegendary = GachaMath.Rates.expectedRollsPerLegendary(banner)
	local perFeatured = GachaMath.Rates.expectedRollsPerFeaturedLegendary(banner)
	local cost = banner.multi.cost / banner.multi.count

	print("")
	print(string.format("  average pulls per Legendary          %8.1f   (%s gems)", perLegendary, commas(perLegendary * cost)))
	if banner.featured then
		print(string.format("  average pulls per featured unit      %8.1f   (%s gems)", perFeatured, commas(perFeatured * cost)))
	end

	local featuredPulls, legendaryPulls = simulate(banner)
	print("")
	print(`  from a fresh account, {commas(trials)} simulated players:`)
	print("")
	print("                        median      75th      90th      99th")
	print("  " .. string.rep("-", 60))
	print(string.format(
		"  pulls to Legendary %9d %9d %9d %9d",
		quantile(legendaryPulls, 0.5),
		quantile(legendaryPulls, 0.75),
		quantile(legendaryPulls, 0.9),
		quantile(legendaryPulls, 0.99)
	))
	if banner.featured then
		print(string.format(
			"  pulls to featured  %9d %9d %9d %9d",
			quantile(featuredPulls, 0.5),
			quantile(featuredPulls, 0.75),
			quantile(featuredPulls, 0.9),
			quantile(featuredPulls, 0.99)
		))
		print(string.format(
			"  gems to featured   %9s %9s %9s %9s",
			commas(quantile(featuredPulls, 0.5) * cost),
			commas(quantile(featuredPulls, 0.75) * cost),
			commas(quantile(featuredPulls, 0.9) * cost),
			commas(quantile(featuredPulls, 0.99) * cost)
		))
	end

	-- The number that decides whether a two-week banner feels fair: what share
	-- of players clear it on the budget the event actually hands out.
	local budgets = { 10, 30, 60, 90, 180 }
	print("")
	print(if banner.featured
		then "  chance of landing the featured unit within N pulls:"
		else "  chance of landing any Legendary within N pulls:")
	local parts = {}
	for _, budget in budgets do
		local hits = 0
		for _, pulls in featuredPulls do
			if pulls <= budget then
				hits += 1
			end
		end
		table.insert(parts, `{budget}: {percent(hits / trials)}`)
	end
	print("    " .. table.concat(parts, "   "))
end

print("")
