--!strict
--[[
	What actually happens to a pull once the dice have landed.

	Duplicates are the reason a gacha collection stays interesting after the
	first week: a repeat unit raises its star level until the cap, and after
	that it converts to shards. Never let a duplicate be worth nothing — that is
	the single fastest way to make a pull feel like a punishment.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local Config = require(GachaCore:WaitForChild("Config"))

local Profile = require(script.Parent.Profile)

local Inventory = {}

export type Grant = {
	itemId: string,
	isNew: boolean,
	stars: number,
	shardsGained: number,
}

function Inventory.grant(data: Profile.Data, itemId: string, rarity: string): Grant
	local entry = data.inventory[itemId]
	if not entry then
		data.inventory[itemId] = { stars = 1, copies = 1 }
		return { itemId = itemId, isNew = true, stars = 1, shardsGained = 0 }
	end

	entry.copies += 1
	if entry.stars < Config.MaxStars then
		entry.stars += 1
		return { itemId = itemId, isNew = false, stars = entry.stars, shardsGained = 0 }
	end

	local shards = Config.DuplicateShards[rarity] or 0
	data.shards += shards
	return { itemId = itemId, isNew = false, stars = entry.stars, shardsGained = shards }
end

return Inventory
