--!strict
--[[
	Client entry point. Rojo builds this folder into
	StarterPlayer.StarterPlayerScripts.GachaClient.

	It does three things: draw the UI, send pull requests, and play back
	whatever the server says came out. It never decides an outcome, and it never
	trusts its own copy of a balance — every label is redrawn from the server's
	snapshot.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Remotes = require(ReplicatedStorage:WaitForChild("GachaRemotes"))
local Reveal = require(script.Reveal)
local Ui = require(script.Ui)

local rollRequest = Remotes.get(Remotes.Names.RollRequest)
local rollResponse = Remotes.get(Remotes.Names.RollResponse)
local profileSync = Remotes.get(Remotes.Names.ProfileSync)

local ui = Ui.create()

--[[
	Only one pull is in flight at a time. The server enforces this too — this
	side of it is so the buttons feel right, not so the rule holds.
]]
local pending = false

ui.onPull = function(bannerId: string, count: number)
	if pending then
		return
	end
	pending = true
	ui.setBusy(true)
	rollRequest:FireServer(bannerId, count)

	-- If the response never arrives, give the buttons back rather than leaving
	-- the player staring at a dead screen.
	task.delay(15, function()
		if pending then
			pending = false
			ui.setBusy(false)
			ui.message("The server did not respond. Try again.")
		end
	end)
end

profileSync.OnClientEvent:Connect(function(snapshot)
	ui.setSnapshot(snapshot)
end)

rollResponse.OnClientEvent:Connect(function(payload)
	if not pending then
		return
	end
	pending = false
	ui.setBusy(false)

	if not payload or not payload.ok then
		ui.message(if payload and payload.reason then payload.reason else "That pull did not go through.")
		return
	end

	-- Balances update before the reveal, so the numbers are already right when
	-- the cards clear.
	ui.setSnapshot(payload.snapshot)
	Reveal.play(ui.gui, payload.results)
end)
