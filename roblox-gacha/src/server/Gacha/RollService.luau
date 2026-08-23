--!strict
--[[
	The only place in the game where a pull can happen.

	Everything the client sends is treated as a suggestion. The client says
	which banner and how many pulls; the server decides whether that is legal,
	what it costs, whether the player can afford it, what came out, and what the
	player now owns. The client is told the result afterwards.

	The rules that keep this honest:

	  * The client never sends an item, a rarity, or a price. If it could, it
	    would send "Legendary" every time.
	  * One roll per player at a time. Two overlapping requests would both read
	    the same balance and both pass the affordability check — the classic way
	    to spend 160 gems twice for 320 gems of value.
	  * Payment is deducted before the roll, and the roll cannot fail after
	    payment: the banner was validated at startup, so there is no pool that
	    can turn out to be empty here.
	  * Rate limited, because a remote a client can call is a remote a modified
	    client can call ten thousand times.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local Config = require(GachaCore:WaitForChild("Config"))
local GachaMath = require(GachaCore:WaitForChild("GachaMath"))
local Remotes = require(ReplicatedStorage:WaitForChild("GachaRemotes"))

local Profile = require(script.Parent.Profile)
local Inventory = require(script.Parent.Inventory)
local Sync = require(script.Parent.Sync)
local RateLimiter = require(script.Parent.RateLimiter)

local RollService = {}

--[[
	One generator for the whole server, seeded by the engine.

	Not `math.random`: that shares its state with every other script in the
	game, so anything that reseeds it — a plugin, a module you did not write —
	silently reaches into your drop rates.
]]
local random = Random.new()
local function rng(): number
	return random:NextNumber()
end

local limiter = RateLimiter.new(Config.RateLimit.burst, Config.RateLimit.refillPerSecond)
local rolling: { [Player]: boolean } = {}

local function respond(player: Player, payload: any)
	Remotes.get(Remotes.Names.RollResponse):FireClient(player, payload)
end

local function refuse(player: Player, reason: string)
	respond(player, { ok = false, reason = reason })
end

--[[
	Works out what this request costs, or why it is not a request at all.
	Returns the banner, the pull count and the price.
]]
local function priceOf(bannerId: unknown, count: unknown): (GachaMath.Banner?, number, number, string?)
	if type(bannerId) ~= "string" then
		return nil, 0, 0, "bad banner"
	end
	local banner = (Config.Banners :: any)[bannerId]
	if not banner then
		return nil, 0, 0, "no such banner"
	end
	if type(count) ~= "number" or count ~= math.floor(count) then
		return nil, 0, 0, "bad count"
	end
	if count == 1 then
		return banner, 1, banner.costPerRoll, nil
	end
	if count == banner.multi.count then
		return banner, banner.multi.count, banner.multi.cost, nil
	end
	-- Anything else is a client inventing its own bulk discount.
	return nil, 0, 0, "unsupported pull count"
end

function RollService.handleRequest(player: Player, bannerId: unknown, count: unknown)
	if not limiter:consume(player) then
		refuse(player, "You are rolling too fast.")
		return
	end
	if rolling[player] then
		refuse(player, "A pull is already in progress.")
		return
	end

	local banner, pulls, cost, problem = priceOf(bannerId, count)
	if not banner then
		-- A normal client cannot produce this, so it is worth seeing in logs.
		warn(`[Gacha] rejected roll from {player.Name} ({player.UserId}): {tostring(problem)}`)
		refuse(player, "That banner is not available.")
		return
	end

	local record = Profile.get(player)
	if not record then
		refuse(player, "Your data is still loading.")
		return
	end

	local data = record.data
	if data.gems < cost then
		refuse(player, `You need {cost - data.gems} more {Config.Currency.displayName}.`)
		return
	end

	rolling[player] = true
	local ok, result = pcall(function()
		data.gems -= cost
		local state = Profile.stateFor(data, banner.id)
		local outcomes = GachaMath.rollMany(banner, state, rng, pulls)

		local results = table.create(pulls)
		for _, outcome in outcomes do
			local grant = Inventory.grant(data, outcome.itemId, outcome.rarity)
			Profile.pushHistory(data, {
				itemId = outcome.itemId,
				rarity = outcome.rarity,
				featured = outcome.featured,
				at = os.time(),
			})
			table.insert(results, {
				itemId = outcome.itemId,
				rarity = outcome.rarity,
				featured = outcome.featured,
				hardPity = outcome.hardPity,
				isNew = grant.isNew,
				stars = grant.stars,
				shardsGained = grant.shardsGained,
			})
		end
		return results
	end)
	rolling[player] = nil

	if not ok then
		-- The pull failed after payment, which must never happen quietly.
		warn(`[Gacha] roll errored for {player.Name}: {tostring(result)}`)
		data.gems += cost
		Profile.markDirty(player)
		Sync.push(player, data)
		refuse(player, "Something went wrong. Your gems were not spent.")
		return
	end

	Profile.markDirty(player)
	-- Spending real money's worth of currency is worth a write now rather than
	-- at the next autosave, since a server crash in between is a refund ticket.
	task.spawn(Profile.save, player, false)

	respond(player, {
		ok = true,
		bannerId = banner.id,
		spent = cost,
		results = result,
		snapshot = Sync.snapshot(data),
	})
end

function RollService.start()
	Remotes.get(Remotes.Names.RollRequest).OnServerEvent:Connect(function(player, bannerId, count)
		-- Never let one player's bad request take down the handler for everyone.
		local ok, err = pcall(RollService.handleRequest, player, bannerId, count)
		if not ok then
			warn(`[Gacha] unhandled error rolling for {player.Name}: {tostring(err)}`)
			refuse(player, "Something went wrong.")
		end
	end)

	game:GetService("Players").PlayerRemoving:Connect(function(player)
		rolling[player] = nil
		limiter:forget(player)
	end)
end

return RollService
