--!strict
--[[
	The odds disclosure.

	Every number on this panel is derived from the same banner table the server
	rolls against — nothing is typed in by hand, so the panel cannot drift out
	of date when someone retunes a rate. If you sell the currency that pays for
	these pulls, Roblox's paid random item policy requires this panel to exist
	and to be reachable before the purchase; treat it as part of the feature,
	not as an extra.

	It shows both numbers players care about: the advertised per-pull rate, and
	the rate they actually experience once pity is counted in. Publishing only
	the first reads as a lie the moment someone datamines the second.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local GachaCore = ReplicatedStorage:WaitForChild("GachaCore")
local GachaMath = require(GachaCore:WaitForChild("GachaMath"))

local Theme = require(script.Parent.Theme)

local RatesPanel = {}

local function percent(value: number): string
	return string.format("%.3f%%", value * 100)
end

local function row(entry: GachaMath.OddsRow, order: number): Frame
	local frame: Frame = Theme.create("Frame", {
		BackgroundColor3 = if order % 2 == 0 then Theme.panelLight else Theme.panel,
		BorderSizePixel = 0,
		Size = UDim2.new(1, 0, 0, 30),
		LayoutOrder = order,
	}, {
		Theme.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(0.5, 0, 1, 0),
			Position = UDim2.fromOffset(12, 0),
			Font = Theme.font,
			TextSize = 14,
			TextXAlignment = Enum.TextXAlignment.Left,
			TextColor3 = Theme.rarityColor(entry.rarity),
			Text = entry.label,
		}),
		Theme.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(0.24, 0, 1, 0),
			Position = UDim2.new(0.5, 0, 0, 0),
			Font = Theme.font,
			TextSize = 14,
			TextColor3 = Theme.textDim,
			Text = percent(entry.base),
		}),
		Theme.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(0.24, 0, 1, 0),
			Position = UDim2.new(0.74, 0, 0, 0),
			Font = Theme.fontBold,
			TextSize = 14,
			TextColor3 = Theme.text,
			Text = percent(entry.consolidated),
		}),
	})
	return frame
end

--[[
	Plain-language rules, written from the config rather than about it. If you
	change the pity numbers, these sentences change with them.
]]
local function explain(banner: GachaMath.Banner): { string }
	local lines = {}
	local legendary = banner.pity.Legendary
	local epic = banner.pity.Epic

	table.insert(lines, `"Advertised" is the chance on one pull with no pity counted.`)
	table.insert(lines, `"With pity" is the long-run rate including every guarantee below.`)

	if legendary.softStart then
		table.insert(
			lines,
			`From pull {legendary.softStart} of a cycle, the Legendary chance rises by `
				.. `{percent(legendary.softStep or 0)} per pull.`
		)
	end
	table.insert(lines, `Pull {legendary.hard} is always a Legendary. The counter then resets.`)
	table.insert(lines, `Every {epic.hard} pulls contains at least an Epic.`)

	local featuredTable = banner.featured
	local featured = featuredTable and featuredTable.Legendary
	if featured then
		table.insert(lines, `{percent(featured.chance)} of Legendaries are the featured unit.`)
		if featured.guaranteeAfterLoss then
			table.insert(lines, "If a Legendary is not the featured unit, the next one is guaranteed to be.")
		end
	end

	local perFeatured = GachaMath.Rates.expectedRollsPerFeaturedLegendary(banner)
	local perLegendary = GachaMath.Rates.expectedRollsPerLegendary(banner)
	local cost = banner.multi.cost / banner.multi.count
	table.insert(
		lines,
		`On average a Legendary takes {math.floor(perLegendary + 0.5)} pulls `
			.. `({Theme.commas(perLegendary * cost)} gems).`
	)
	if featured then
		table.insert(
			lines,
			`On average the featured unit takes {math.floor(perFeatured + 0.5)} pulls `
				.. `({Theme.commas(perFeatured * cost)} gems).`
		)
	end
	return lines
end

--[[
	Builds the panel for a banner. Returns the frame, hidden; the caller shows
	it. The solver behind these numbers is cached per banner, so reopening the
	panel is free.
]]
function RatesPanel.create(banner: GachaMath.Banner): Frame
	local list: ScrollingFrame = Theme.create("ScrollingFrame", {
		BackgroundTransparency = 1,
		BorderSizePixel = 0,
		Size = UDim2.new(1, 0, 1, -84),
		Position = UDim2.fromOffset(0, 52),
		CanvasSize = UDim2.new(),
		AutomaticCanvasSize = Enum.AutomaticSize.Y,
		ScrollBarThickness = 4,
	}, {
		Theme.create("UIListLayout", {
			SortOrder = Enum.SortOrder.LayoutOrder,
			Padding = UDim.new(0, 2),
		}),
	})

	local header: Frame = Theme.create("Frame", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, 0, 0, 24),
		LayoutOrder = 0,
	}, {
		Theme.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(0.5, 0, 1, 0),
			Position = UDim2.fromOffset(12, 0),
			Font = Theme.font,
			TextSize = 12,
			TextXAlignment = Enum.TextXAlignment.Left,
			TextColor3 = Theme.textDim,
			Text = "RARITY",
		}),
		Theme.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(0.24, 0, 1, 0),
			Position = UDim2.new(0.5, 0, 0, 0),
			Font = Theme.font,
			TextSize = 12,
			TextColor3 = Theme.textDim,
			Text = "ADVERTISED",
		}),
		Theme.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(0.24, 0, 1, 0),
			Position = UDim2.new(0.74, 0, 0, 0),
			Font = Theme.font,
			TextSize = 12,
			TextColor3 = Theme.textDim,
			Text = "WITH PITY",
		}),
	})
	header.Parent = list

	local order = 1
	for _, entry in GachaMath.Rates.published(banner) do
		row(entry, order).Parent = list
		order += 1
	end

	local notes: TextLabel = Theme.create("TextLabel", {
		BackgroundTransparency = 1,
		Size = UDim2.new(1, -24, 0, 0),
		AutomaticSize = Enum.AutomaticSize.Y,
		Position = UDim2.fromOffset(12, 0),
		Font = Theme.font,
		TextSize = 13,
		LineHeight = 1.25,
		TextWrapped = true,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextYAlignment = Enum.TextYAlignment.Top,
		TextColor3 = Theme.textDim,
		Text = "\n" .. table.concat(explain(banner), "\n"),
		LayoutOrder = order + 1,
	})
	notes.Parent = list

	local panel: Frame = Theme.create("Frame", {
		Name = "RatesPanel",
		Visible = false,
		BackgroundColor3 = Theme.panel,
		BorderSizePixel = 0,
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.fromScale(0.5, 0.5),
		Size = UDim2.fromOffset(460, 440),
	}, {
		Theme.corner(10),
		Theme.create("TextLabel", {
			BackgroundTransparency = 1,
			Size = UDim2.new(1, 0, 0, 44),
			Font = Theme.fontBold,
			TextSize = 18,
			TextColor3 = Theme.text,
			Text = `{banner.displayName} — drop rates`,
		}),
		list,
	})

	local close: TextButton = Theme.create("TextButton", {
		Name = "Close",
		BackgroundColor3 = Theme.panelLight,
		BorderSizePixel = 0,
		AnchorPoint = Vector2.new(0.5, 1),
		Position = UDim2.new(0.5, 0, 1, -12),
		Size = UDim2.fromOffset(140, 32),
		Font = Theme.fontBold,
		TextSize = 14,
		TextColor3 = Theme.text,
		Text = "Close",
		AutoButtonColor = true,
	}, { Theme.corner(6) })
	close.Parent = panel
	close.Activated:Connect(function()
		panel.Visible = false
	end)

	return panel
end

return RatesPanel
