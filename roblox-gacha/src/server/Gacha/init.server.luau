--!strict
--[[
	Server entry point. Rojo builds this folder into ServerScriptService.Gacha.

	Order matters: banners are validated before a single player can connect, so
	a typo in Config is a startup error you see immediately rather than a failed
	pull that has already taken someone's gems.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local Config = require(GachaCore:WaitForChild("Config"))
local GachaMath = require(GachaCore:WaitForChild("GachaMath"))
local Remotes = require(ReplicatedStorage:WaitForChild("GachaRemotes"))

local Profile = require(script.Profile)
local Products = require(script.Products)
local RollService = require(script.RollService)
local Sync = require(script.Sync)

local function validateConfig()
	local count = 0
	for id, banner in Config.Banners do
		local ok, err = GachaMath.validate((banner :: any) :: GachaMath.Banner, Config.Items)
		if not ok then
			error(`[Gacha] {tostring(err)}`, 0)
		end
		if banner.id ~= id then
			error(`[Gacha] banner stored under key "{id}" declares id "{banner.id}"`, 0)
		end
		count += 1
	end
	if count == 0 then
		error("[Gacha] no banners are configured", 0)
	end
	print(`[Gacha] {count} banner(s) validated`)
end

local function onPlayerAdded(player: Player)
	local record = Profile.load(player)
	if not record then
		-- Letting them in with a fresh profile would overwrite whatever is
		-- really in the DataStore. Kicking is the kinder failure.
		player:Kick("Could not load your save. Please rejoin in a moment.")
		return
	end
	if not player.Parent then
		Profile.save(player, true)
		return
	end
	Sync.push(player, record.data)
end

validateConfig()

-- Creates the remote folder before any client asks for it.
Remotes.get(Remotes.Names.RollRequest)

Profile.start()
Products.start()
RollService.start()

Players.PlayerAdded:Connect(onPlayerAdded)
-- Anyone who joined while this script was still starting up.
for _, player in Players:GetPlayers() do
	task.spawn(onPlayerAdded, player)
end

print("[Gacha] server ready")
