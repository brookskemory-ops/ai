--!strict
--[[
	The single shape the client is told about a player's own account.

	Only what the UI draws goes across: balances, the collection, and pity
	counters. Nothing here is trusted on the way back — the client gets this so
	it has something to render, not so it has something to send.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local Config = require(GachaCore:WaitForChild("Config"))

local Profile = require(script.Parent.Profile)
local Remotes = require(ReplicatedStorage:WaitForChild("GachaRemotes"))

local Sync = {}

export type Snapshot = {
	gems: number,
	shards: number,
	inventory: { [string]: { stars: number, copies: number } },
	pity: { [string]: { legendary: number, epic: number, guaranteed: boolean, total: number } },
	history: { Profile.HistoryEntry },
}

function Sync.snapshot(data: Profile.Data): Snapshot
	local pity: { [string]: any } = {}
	for bannerId in Config.Banners do
		local state = Profile.stateFor(data, bannerId)
		pity[bannerId] = {
			legendary = state.pity.Legendary,
			epic = state.pity.Epic,
			guaranteed = state.guaranteed.Legendary,
			total = state.totalRolls,
		}
	end

	local inventory: { [string]: { stars: number, copies: number } } = {}
	for itemId, entry in data.inventory do
		inventory[itemId] = { stars = entry.stars, copies = entry.copies }
	end

	return {
		gems = data.gems,
		shards = data.shards,
		inventory = inventory,
		pity = pity,
		history = data.history,
	}
end

function Sync.push(player: Player, data: Profile.Data)
	Remotes.get(Remotes.Names.ProfileSync):FireClient(player, Sync.snapshot(data))
end

return Sync
