--!strict
--[[
	Turning Robux into roll currency.

	ProcessReceipt is the one callback in Roblox where getting it slightly wrong
	costs real money — the player's or yours. Two rules:

	  * Grant only after the profile has been written. If you return
	    PurchaseGranted and the server then dies before saving, the player paid
	    and received nothing, and Roblox will not retry a granted receipt.
	  * Remember the PurchaseId. Roblox re-delivers the same receipt until it is
	    granted, so a receipt that arrives twice must pay out once.

	Anything unexpected returns NotProcessedYet, which tells Roblox to try again
	later. That is always safer than granting on a guess.
]]

local MarketplaceService = game:GetService("MarketplaceService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local Config = require(GachaCore:WaitForChild("Config"))

local Profile = require(script.Parent.Profile)
local Sync = require(script.Parent.Sync)

local Products = {}

local function processReceipt(receipt: any): Enum.ProductPurchaseDecision
	local player = Players:GetPlayerByUserId(receipt.PlayerId)
	if not player then
		-- They left mid-purchase. Roblox will deliver this again next time they
		-- join, and the receipt stays valid until then.
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	local record = Profile.get(player)
	if not record then
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	local purchaseId = tostring(receipt.PurchaseId)
	local data = record.data
	if Profile.wasReceiptGranted(data, purchaseId) then
		return Enum.ProductPurchaseDecision.PurchaseGranted
	end

	local product = Config.Products[receipt.ProductId]
	if not product then
		warn(`[Gacha] product {receipt.ProductId} is not in Config.Products; leaving the receipt unprocessed`)
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	data.gems += product.gems
	Profile.rememberReceipt(data, purchaseId)
	Profile.markDirty(player)

	if not Profile.save(player, false) then
		-- Roll it back and let Roblox retry, rather than granting something we
		-- could not persist.
		data.gems -= product.gems
		Profile.forgetReceipt(data, purchaseId)
		return Enum.ProductPurchaseDecision.NotProcessedYet
	end

	Sync.push(player, data)
	return Enum.ProductPurchaseDecision.PurchaseGranted
end

function Products.start()
	MarketplaceService.ProcessReceipt = function(receipt)
		local ok, result = pcall(processReceipt, receipt)
		if not ok then
			warn(`[Gacha] ProcessReceipt errored: {tostring(result)}`)
			return Enum.ProductPurchaseDecision.NotProcessedYet
		end
		return result
	end
end

return Products
