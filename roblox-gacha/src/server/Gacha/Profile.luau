--!strict
--[[
	Player data: load, session-lock, autosave, release.

	A gacha game is a game about a number in a database. If saving is sloppy,
	the first duplicated Legendary or lost 10,000 gems will cost you more
	goodwill than every other bug in the game combined. Two things matter more
	than anything else here:

	  1. Session locking. Roblox can run the same player on two servers at once
	     (a teleport that goes wrong, a server that has not yet shut down). Both
	     copies then save over each other, and gems get duplicated or vanish.
	     A profile is claimed by one job id at a time, and a stale claim is only
	     stolen after LOCK_STALE_SECONDS.

	  2. UpdateAsync, never SetAsync. UpdateAsync reads and writes as one
	     operation, so two servers racing cannot silently drop one side.

	For a live game, prefer a maintained library — ProfileStore is the usual
	choice — rather than this. This module exists so the kit runs with no
	dependencies and so the failure modes above are visible rather than hidden
	behind an API.
]]

local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local Config = require(GachaCore:WaitForChild("Config"))
local GachaMath = require(GachaCore:WaitForChild("GachaMath"))

local SCHEMA_VERSION = 1
local STORE_NAME = "GachaProfiles"
local STORE_SCOPE = "v1"
local LOCK_STALE_SECONDS = 300
local AUTOSAVE_SECONDS = 60
local LOAD_ATTEMPTS = 5
local HISTORY_LIMIT = 100

local Profile = {}

export type InventoryEntry = {
	stars: number,
	copies: number,
}

export type HistoryEntry = {
	itemId: string,
	rarity: string,
	featured: boolean,
	at: number,
}

export type Data = {
	schema: number,
	gems: number,
	shards: number,
	inventory: { [string]: InventoryEntry },
	banners: { [string]: GachaMath.PityState },
	history: { HistoryEntry },
	-- Developer product receipts already granted, newest first, so a retried
	-- receipt does not pay out twice. Roblox re-delivers a receipt until you
	-- return PurchaseGranted, including after a server restart, so this has to
	-- survive in the save rather than living in memory.
	grantedReceipts: { string },
	lock: { jobId: string, at: number }?,
}

export type Record = {
	data: Data,
	dirty: boolean,
	released: boolean,
}

local store = DataStoreService:GetDataStore(STORE_NAME, STORE_SCOPE)
local records: { [Player]: Record } = {}
local volatile = false -- set in Studio when the API is unreachable

local function key(player: Player): string
	return `player_{player.UserId}`
end

local function newData(): Data
	return {
		schema = SCHEMA_VERSION,
		gems = Config.Currency.startingBalance,
		shards = 0,
		inventory = {},
		banners = {},
		history = {},
		grantedReceipts = {},
		lock = nil,
	}
end

--[[
	Brings an old save up to the current schema and repairs anything that a
	config change has invalidated. Runs on every load, including on brand new
	data, so there is only ever one path into the game.
]]
local function migrate(raw: any): Data
	local data: Data = if type(raw) == "table" then raw else newData()

	data.schema = SCHEMA_VERSION
	data.gems = if type(data.gems) == "number" and data.gems >= 0 then math.floor(data.gems) else 0
	data.shards = if type(data.shards) == "number" and data.shards >= 0 then math.floor(data.shards) else 0
	data.inventory = if type(data.inventory) == "table" then data.inventory else {}
	data.history = if type(data.history) == "table" then data.history else {}
	data.grantedReceipts = if type(data.grantedReceipts) == "table" then data.grantedReceipts else {}
	-- An older build stored these as a set keyed by purchase id. Convert rather
	-- than clear: clearing would re-grant every receipt Roblox still retries.
	if next(data.grantedReceipts) ~= nil and #data.grantedReceipts == 0 then
		local converted: { string } = {}
		for purchaseId in data.grantedReceipts :: any do
			table.insert(converted, tostring(purchaseId))
		end
		data.grantedReceipts = converted
	end

	-- Drop units that no longer exist, so a removed item cannot crash the UI.
	for itemId, entry in data.inventory do
		if not Config.Items[itemId] or type(entry) ~= "table" then
			data.inventory[itemId] = nil
		else
			entry.stars = math.clamp(tonumber(entry.stars) or 1, 1, Config.MaxStars)
			entry.copies = math.max(1, math.floor(tonumber(entry.copies) or 1))
		end
	end

	-- Pity state is the one thing players notice going backwards. Normalize it
	-- rather than discarding it, and keep counters for banners that have since
	-- been retired in case they come back.
	local banners: { [string]: GachaMath.PityState } = {}
	if type(data.banners) == "table" then
		for bannerId, state in data.banners do
			banners[bannerId] = GachaMath.normalizeState(state)
		end
	end
	data.banners = banners

	return data
end

--[[
	Receipt bookkeeping. Roblox only retries a receipt for a few days, so a
	bounded list is enough, and an unbounded one would grow forever on an
	account that buys often.
]]
local RECEIPT_MEMORY = 100

function Profile.wasReceiptGranted(data: Data, purchaseId: string): boolean
	return table.find(data.grantedReceipts, purchaseId) ~= nil
end

function Profile.rememberReceipt(data: Data, purchaseId: string)
	table.insert(data.grantedReceipts, 1, purchaseId)
	while #data.grantedReceipts > RECEIPT_MEMORY do
		table.remove(data.grantedReceipts)
	end
end

function Profile.forgetReceipt(data: Data, purchaseId: string)
	local index = table.find(data.grantedReceipts, purchaseId)
	if index then
		table.remove(data.grantedReceipts, index)
	end
end

function Profile.stateFor(data: Data, bannerId: string): GachaMath.PityState
	local state = data.banners[bannerId]
	if not state then
		state = GachaMath.newState()
		data.banners[bannerId] = state
	end
	return state
end

function Profile.pushHistory(data: Data, entry: HistoryEntry)
	table.insert(data.history, 1, entry)
	while #data.history > HISTORY_LIMIT do
		table.remove(data.history)
	end
end

--[[
	Claims the profile for this server, or fails if another server holds it.
	Returns the data, or nil plus the reason.
]]
local function claim(player: Player): (Data?, string?)
	local jobId = if game.JobId ~= "" then game.JobId else "studio"
	local blocked = false

	local ok, result = pcall(function()
		return store:UpdateAsync(key(player), function(raw)
			local data = migrate(raw)
			local lock = data.lock
			if lock and lock.jobId ~= jobId and (os.time() - (lock.at or 0)) < LOCK_STALE_SECONDS then
				blocked = true
				return nil -- cancels the write; someone else is live with this profile
			end
			data.lock = { jobId = jobId, at = os.time() }
			return data
		end)
	end)

	if not ok then
		return nil, tostring(result)
	end
	if blocked then
		return nil, "profile is locked by another server"
	end
	return result :: Data, nil
end

--[[
	Loads a player's profile, yielding until it is claimed. Returns nil if the
	player left or the profile could not be claimed after LOAD_ATTEMPTS, and in
	that case the caller must kick rather than hand out a fresh profile — a
	fresh profile over a real one is how players lose everything.
]]
function Profile.load(player: Player): Record?
	for attempt = 1, LOAD_ATTEMPTS do
		if not player.Parent then
			return nil
		end
		local data, reason = claim(player)
		if data then
			local record: Record = { data = data, dirty = false, released = false }
			records[player] = record
			return record
		end

		if RunService:IsStudio() and attempt == 1 and reason and not string.find(reason, "locked") then
			-- Studio without API access enabled. Let the developer keep working
			-- with data that lives only for this session.
			warn(`[Gacha] DataStore unavailable ({reason}); running with volatile profiles.`)
			volatile = true
			local record: Record = { data = newData(), dirty = false, released = false }
			records[player] = record
			return record
		end

		warn(`[Gacha] profile load attempt {attempt} for {player.Name} failed: {tostring(reason)}`)
		task.wait(2 ^ attempt)
	end
	return nil
end

function Profile.get(player: Player): Record?
	return records[player]
end

function Profile.markDirty(player: Player)
	local record = records[player]
	if record then
		record.dirty = true
	end
end

--[[
	Writes the profile back. `release` clears the session lock, and must be set
	on the last save for a player — otherwise their next server has to wait out
	LOCK_STALE_SECONDS before it can let them in.
]]
function Profile.save(player: Player, release: boolean): boolean
	local record = records[player]
	if not record or record.released then
		return true
	end
	if not record.dirty and not release then
		return true
	end
	if volatile then
		record.dirty = false
		if release then
			record.released = true
			records[player] = nil
		end
		return true
	end

	local jobId = if game.JobId ~= "" then game.JobId else "studio"
	local payload = record.data

	local ok, err = pcall(function()
		store:UpdateAsync(key(player), function(raw)
			-- Only write over our own claim. If another server stole the profile
			-- while we held it, its copy is the newer one and ours is stale.
			if type(raw) == "table" and raw.lock and raw.lock.jobId ~= jobId then
				local age = os.time() - (raw.lock.at or 0)
				if age < LOCK_STALE_SECONDS then
					return nil
				end
			end
			payload.lock = if release then nil else { jobId = jobId, at = os.time() }
			return payload
		end)
	end)

	if not ok then
		warn(`[Gacha] failed to save {player.Name}: {tostring(err)}`)
		return false
	end

	record.dirty = false
	if release then
		record.released = true
		records[player] = nil
	end
	return true
end

function Profile.start()
	task.spawn(function()
		while true do
			task.wait(AUTOSAVE_SECONDS)
			for player in records do
				-- Saves stagger themselves across the loop rather than firing
				-- every profile at the DataStore in one frame.
				task.spawn(Profile.save, player, false)
				task.wait(0.2)
			end
		end
	end)

	Players.PlayerRemoving:Connect(function(player)
		Profile.save(player, true)
	end)

	game:BindToClose(function()
		if RunService:IsStudio() then
			return
		end
		local pending = 0
		for player in records do
			pending += 1
			task.spawn(function()
				Profile.save(player, true)
				pending -= 1
			end)
		end
		local deadline = os.clock() + 20
		while pending > 0 and os.clock() < deadline do
			task.wait(0.1)
		end
	end)
end

return Profile
