--!strict
--[[
	The one place remote instances are named.

	The server creates them at startup; the client waits for them. Keeping the
	names in a module rather than typing strings on both sides means a rename
	is a rename, not a silent runtime break.

	Every one of these is a RemoteEvent. There is no RemoteFunction here on
	purpose: a client-invoked RemoteFunction blocks a server thread for as long
	as the invocation runs, and an exploiter can invoke it in a tight loop.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local FOLDER_NAME = "GachaRemotes"

local NAMES = {
	-- client -> server: "roll `count` times on `bannerId`"
	RollRequest = "RollRequest",
	-- server -> client: the outcome, or a refusal
	RollResponse = "RollResponse",
	-- server -> client: balances, inventory and pity after any change
	ProfileSync = "ProfileSync",
}

local Remotes = {}
Remotes.Names = NAMES

local folder: Folder? = nil

local function getFolder(): Folder
	if folder then
		return folder
	end
	if RunService:IsServer() then
		local existing = ReplicatedStorage:FindFirstChild(FOLDER_NAME)
		if existing then
			folder = existing :: Folder
			return existing :: Folder
		end
		local created = Instance.new("Folder")
		created.Name = FOLDER_NAME
		for _, name in NAMES do
			local remote = Instance.new("RemoteEvent")
			remote.Name = name
			remote.Parent = created
		end
		created.Parent = ReplicatedStorage
		folder = created
		return created
	end
	local found = ReplicatedStorage:WaitForChild(FOLDER_NAME, 30)
	assert(found, "GachaRemotes never appeared; is the server script running?")
	folder = found :: Folder
	return found :: Folder
end

function Remotes.get(name: string): RemoteEvent
	local remote = getFolder():WaitForChild(name, 30)
	assert(remote, `remote "{name}" was never created`)
	return remote :: RemoteEvent
end

return Remotes
