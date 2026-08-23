--!strict
--[[
	The summon screen: balances, banner tabs, the two pull buttons, and the way
	into the rates panel.

	Nothing here decides anything. Buttons ask the server; labels redraw from
	whatever the server last sent. That is what makes the client safe to be
	wrong: a modified client can lie to its own screen and change nothing about
	what it owns.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local Config = require(GachaCore:WaitForChild("Config"))
local GachaMath = require(GachaCore:WaitForChild("GachaMath"))

local RatesPanel = require(script.Parent.RatesPanel)
local Theme = require(script.Parent.Theme)

local Ui = {}

export type Snapshot = {
	gems: number,
	shards: number,
	inventory: { [string]: { stars: number, copies: number } },
	pity: { [string]: { legendary: number, epic: number, guaranteed: boolean, total: number } },
}

export type Handle = {
	gui: ScreenGui,
	onPull: ((bannerId: string, count: number) -> ())?,
	setSnapshot: (snapshot: Snapshot) -> (),
	setBusy: (busy: boolean) -> (),
	message: (text: string, isError: boolean?) -> (),
}

local function sortedBannerIds(): { string }
	local ids = {}
	for id in Config.Banners do
		table.insert(ids, id)
	end
	table.sort(ids)
	return ids
end

local function pullButton(text: string, primary: boolean): TextButton
	local button: TextButton = Theme.create("TextButton", {
		BackgroundColor3 = if primary then Theme.accent else Theme.panelLight,
		BorderSizePixel = 0,
		Size = UDim2.fromOffset(180, 44),
		Font = Theme.fontBold,
		TextSize = 15,
		TextColor3 = if primary then Theme.background else Theme.text,
		Text = text,
		AutoButtonColor = true,
	}, { Theme.corner(8) })
	return button
end

function Ui.create(): Handle
	local player = Players.LocalPlayer
	local bannerIds = sortedBannerIds()
	local currentBannerId = bannerIds[1]
	local snapshot: Snapshot? = nil
	local busy = false

	local gui: ScreenGui = Theme.create("ScreenGui", {
		Name = "GachaUi",
		ResetOnSpawn = false,
		ZIndexBehavior = Enum.ZIndexBehavior.Sibling,
	})

	--== balances =============================================================
	local gemsLabel: TextLabel = Theme.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 22),
		Font = Theme.fontBold,
		TextSize = 18,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = Theme.accent,
		Text = "—",
	})
	local shardsLabel: TextLabel = Theme.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 18),
		Position = UDim2.fromOffset(0, 22),
		Font = Theme.font,
		TextSize = 13,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = Theme.textDim,
		Text = "",
	})
	local wallet: Frame = Theme.create("Frame", {
		BackgroundColor3 = Theme.panel,
		BackgroundTransparency = 0.15,
		BorderSizePixel = 0,
		Position = UDim2.fromOffset(16, 16),
		Size = UDim2.fromOffset(190, 62),
	}, { Theme.corner(8), Theme.padding(10), gemsLabel, shardsLabel })
	wallet.Parent = gui

	--== banner panel =========================================================
	local titleLabel: TextLabel = Theme.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 26),
		Position = UDim2.fromOffset(0, 44),
		Font = Theme.fontBold,
		TextSize = 20,
		TextColor3 = Theme.text,
		Text = "",
	})
	local pityLabel: TextLabel = Theme.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 34),
		Position = UDim2.fromOffset(0, 70),
		Font = Theme.font,
		TextSize = 13,
		TextColor3 = Theme.textDim,
		Text = "",
	})

	local tabs: Frame = Theme.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, -24, 0, 32),
		Position = UDim2.fromOffset(12, 8),
	}, {
		Theme.create("UIListLayout", {
			FillDirection = Enum.FillDirection.Horizontal,
			Padding = UDim.new(0, 8),
			HorizontalAlignment = Enum.HorizontalAlignment.Center,
			SortOrder = Enum.SortOrder.LayoutOrder,
		}),
	})

	local single = pullButton("", true)
	local multi = pullButton("", true)
	local ratesButton: TextButton = Theme.create("TextButton", {
		BackgroundTransparency = 1,
		Size = UDim2.fromOffset(120, 26),
		Font = Theme.font,
		TextSize = 13,
		TextColor3 = Theme.textDim,
		Text = "View drop rates",
	})

	local buttonRow: Frame = Theme.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, -24, 0, 44),
		Position = UDim2.fromOffset(12, 112),
	}, {
		Theme.create("UIListLayout", {
			FillDirection = Enum.FillDirection.Horizontal,
			Padding = UDim.new(0, 12),
			HorizontalAlignment = Enum.HorizontalAlignment.Center,
			SortOrder = Enum.SortOrder.LayoutOrder,
		}),
		single,
		multi,
	})

	local panel: Frame = Theme.create("Frame", {
		BackgroundColor3 = Theme.panel,
		BackgroundTransparency = 0.05,
		BorderSizePixel = 0,
		AnchorPoint = Vector2.new(0.5, 1),
		Position = UDim2.new(0.5, 0, 1, -24),
		Size = UDim2.fromOffset(520, 196),
	}, { Theme.corner(12), tabs, titleLabel, pityLabel, buttonRow })
	ratesButton.AnchorPoint = Vector2.new(0.5, 1)
	ratesButton.Position = UDim2.new(0.5, 0, 1, -6)
	ratesButton.Parent = panel
	panel.Parent = gui

	local messageLabel: TextLabel = Theme.create("TextLabel", {
		BackgroundTransparency = 1,
		AnchorPoint = Vector2.new(0.5, 1),
		Position = UDim2.new(0.5, 0, 1, -228),
		Size = UDim2.fromOffset(520, 22),
		Font = Theme.font,
		TextSize = 14,
		TextColor3 = Theme.danger,
		TextTransparency = 1,
		Text = "",
	})
	messageLabel.Parent = gui

	--== rates panels, built on first open ====================================
	local panels: { [string]: Frame } = {}
	local function showRates()
		local existing = panels[currentBannerId]
		if not existing then
			local banner = (Config.Banners[currentBannerId] :: any) :: GachaMath.Banner
			existing = RatesPanel.create(banner)
			existing.ZIndex = 20
			existing.Parent = gui
			panels[currentBannerId] = existing
		end
		for id, frame in panels do
			frame.Visible = id == currentBannerId
		end
	end

	--== redraw ===============================================================
	local tabButtons: { [string]: TextButton } = {}

	local function refresh()
		local banner = (Config.Banners[currentBannerId] :: any) :: GachaMath.Banner
		titleLabel.Text = banner.displayName
		single.Text = `Pull ×1  ·  {Theme.commas(banner.costPerRoll)}`
		multi.Text = `Pull ×{banner.multi.count}  ·  {Theme.commas(banner.multi.cost)}`

		for id, button in tabButtons do
			button.BackgroundColor3 = if id == currentBannerId then Theme.panelLight else Theme.panel
			button.TextColor3 = if id == currentBannerId then Theme.text else Theme.textDim
		end

		local current = snapshot
		if not current then
			pityLabel.Text = "Loading your account…"
			single.Active = false
			multi.Active = false
			return
		end

		gemsLabel.Text = `{Theme.commas(current.gems)} {Config.Currency.displayName}`
		shardsLabel.Text = `{Theme.commas(current.shards)} shards`

		local pity = current.pity[currentBannerId]
		if pity then
			local remaining = banner.pity.Legendary.hard - pity.legendary
			local parts = { `Legendary guaranteed within {remaining} pull{if remaining == 1 then "" else "s"}` }
			local featuredTable = banner.featured
			if featuredTable and featuredTable.Legendary then
				table.insert(
					parts,
					if pity.guaranteed
						then "next Legendary is guaranteed to be the featured unit"
						else "next Legendary has a 50/50 chance to be featured"
				)
			end
			pityLabel.Text = table.concat(parts, "  ·  ")
		end

		local affordableSingle = current.gems >= banner.costPerRoll
		local affordableMulti = current.gems >= banner.multi.cost
		single.Active = not busy and affordableSingle
		multi.Active = not busy and affordableMulti
		single.BackgroundTransparency = if single.Active then 0 else 0.6
		multi.BackgroundTransparency = if multi.Active then 0 else 0.6
	end

	for order, id in bannerIds do
		local banner = Config.Banners[id]
		local tab: TextButton = Theme.create("TextButton", {
			BackgroundColor3 = Theme.panel,
			BorderSizePixel = 0,
			Size = UDim2.fromOffset(150, 30),
			LayoutOrder = order,
			Font = Theme.font,
			TextSize = 13,
			TextColor3 = Theme.textDim,
			Text = banner.displayName,
			AutoButtonColor = true,
		}, { Theme.corner(6) })
		tab.Parent = tabs
		tabButtons[id] = tab
		tab.Activated:Connect(function()
			currentBannerId = id
			for _, frame in panels do
				frame.Visible = false
			end
			refresh()
		end)
	end

	local handle = {} :: Handle
	handle.gui = gui

	handle.setSnapshot = function(next_: Snapshot)
		snapshot = next_
		refresh()
	end

	handle.setBusy = function(nextBusy: boolean)
		busy = nextBusy
		refresh()
	end

	local messageToken = 0
	handle.message = function(text: string, isError: boolean?)
		messageToken += 1
		local token = messageToken
		messageLabel.Text = text
		messageLabel.TextColor3 = if isError == false then Theme.textDim else Theme.danger
		messageLabel.TextTransparency = 0
		task.delay(3, function()
			if messageToken == token then
				messageLabel.TextTransparency = 1
			end
		end)
	end

	local function requestPull(count: number)
		if busy then
			return
		end
		local callback = handle.onPull
		if callback then
			callback(currentBannerId, count)
		end
	end

	single.Activated:Connect(function()
		if single.Active then
			requestPull(1)
		end
	end)
	multi.Activated:Connect(function()
		if multi.Active then
			local banner = Config.Banners[currentBannerId]
			requestPull(banner.multi.count)
		end
	end)
	ratesButton.Activated:Connect(showRates)

	refresh()
	gui.Parent = player:WaitForChild("PlayerGui")
	return handle
end

return Ui
