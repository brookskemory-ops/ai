--!strict
--[[
	The reveal.

	This is the part players remember, and it is worth more care than the odds
	behind it. Three things it gets right:

	  * Cards land one at a time, so a ten-pull has a rhythm instead of a flash.
	  * A tap skips straight to the end. Players who pull hundreds of times will
	    hate you otherwise.
	  * A Legendary announces itself before the card lands, because the pause is
	    the payoff.

	The results are already decided by the server before any of this runs. The
	animation never influences and never predicts an outcome — it just plays one
	back.
]]

local TweenService = game:GetService("TweenService")

local Theme = require(script.Parent.Theme)

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage:WaitForChild("GachaCore"):WaitForChild("Config"))

local Reveal = {}

export type Result = {
	itemId: string,
	rarity: string,
	featured: boolean,
	hardPity: boolean,
	isNew: boolean,
	stars: number,
	shardsGained: number,
}

local CARD_SIZE = Vector2.new(120, 168)
local STAGGER = 0.12

local function card(result: Result, order: number): Frame
	local item = Config.Items[result.itemId]
	local color = Theme.rarityColor(result.rarity)

	local badge = ""
	if result.isNew then
		badge = "NEW"
	elseif result.shardsGained > 0 then
		badge = `+{result.shardsGained} shards`
	else
		badge = `★ {result.stars}`
	end

	local frame: Frame = Theme.create("Frame", {
		BackgroundColor3 = Theme.panel,
		BorderSizePixel = 0,
		Size = UDim2.fromOffset(CARD_SIZE.X, CARD_SIZE.Y),
		LayoutOrder = order,
		BackgroundTransparency = 1,
	}, {
		Theme.corner(8),
		Theme.create("UIStroke", { Color = color, Thickness = 2, Transparency = 1 }),
		Theme.create("Frame", {
			Name = "Art",
			BackgroundColor3 = color,
			BackgroundTransparency = 0.75,
			BorderSizePixel = 0,
			Size = UDim2.new(1, -16, 0, 96),
			Position = UDim2.fromOffset(8, 8),
		}, { Theme.corner(6) }),
		Theme.create("TextLabel", {
			Name = "Name",
			BackgroundTransparency = 1,
			Size = UDim2.new(1, -12, 0, 32),
			Position = UDim2.fromOffset(6, 108),
			Font = Theme.fontBold,
			TextSize = 13,
			TextWrapped = true,
			TextColor3 = Theme.text,
			Text = if item then item.name else result.itemId,
		}),
		Theme.create("TextLabel", {
			Name = "Badge",
			BackgroundTransparency = 1,
			Size = UDim2.new(1, -12, 0, 18),
			Position = UDim2.fromOffset(6, 142),
			Font = Theme.font,
			TextSize = 12,
			TextColor3 = if result.isNew then Theme.accent else Theme.textDim,
			Text = badge,
		}),
	})
	return frame
end

--[[
	Plays the reveal and yields until the player dismisses it.
]]
function Reveal.play(parent: ScreenGui, results: { Result })
	local skipped = false
	local finished = false

	local grid: Frame = Theme.create("Frame", {
		BackgroundTransparency = 1,
		AnchorPoint = Vector2.new(0.5, 0.5),
		Position = UDim2.fromScale(0.5, 0.48),
		Size = UDim2.fromOffset(math.min(#results, 5) * 132, math.ceil(#results / 5) * 180),
	}, {
		Theme.create("UIGridLayout", {
			CellSize = UDim2.fromOffset(CARD_SIZE.X, CARD_SIZE.Y),
			CellPadding = UDim2.fromOffset(12, 12),
			SortOrder = Enum.SortOrder.LayoutOrder,
			HorizontalAlignment = Enum.HorizontalAlignment.Center,
			VerticalAlignment = Enum.VerticalAlignment.Center,
		}),
	})

	local prompt: TextLabel = Theme.create("TextLabel", {
		BackgroundTransparency = 1,
		AnchorPoint = Vector2.new(0.5, 1),
		Position = UDim2.new(0.5, 0, 1, -28),
		Size = UDim2.fromOffset(300, 24),
		Font = Theme.font,
		TextSize = 14,
		TextColor3 = Theme.textDim,
		TextTransparency = 1,
		Text = "Tap to skip",
	})

	local overlay: TextButton = Theme.create("TextButton", {
		Name = "Reveal",
		BackgroundColor3 = Theme.background,
		BackgroundTransparency = 0.15,
		BorderSizePixel = 0,
		Size = UDim2.fromScale(1, 1),
		Text = "",
		AutoButtonColor = false,
		ZIndex = 10,
	}, { grid, prompt })
	overlay.Parent = parent

	TweenService:Create(prompt, TweenInfo.new(0.4), { TextTransparency = 0 }):Play()

	overlay.Activated:Connect(function()
		if finished then
			overlay:Destroy()
		else
			skipped = true
		end
	end)

	local cards: { Frame } = {}
	for order, result in results do
		local frame = card(result, order)
		frame.Parent = grid
		table.insert(cards, frame)
	end

	for index, frame in cards do
		local result = results[index]
		local stroke = frame:FindFirstChildOfClass("UIStroke") :: UIStroke
		local duration = if skipped then 0.05 else 0.22

		-- A Legendary gets a beat of anticipation before it lands.
		if not skipped and result.rarity == "Legendary" then
			task.wait(0.35)
		end

		frame.BackgroundTransparency = 0
		stroke.Transparency = 0
		frame.Size = UDim2.fromOffset(CARD_SIZE.X * 0.8, CARD_SIZE.Y * 0.8)
		TweenService
			:Create(frame, TweenInfo.new(duration, Enum.EasingStyle.Back, Enum.EasingDirection.Out), {
				Size = UDim2.fromOffset(CARD_SIZE.X, CARD_SIZE.Y),
			})
			:Play()

		if not skipped then
			task.wait(STAGGER)
		end
	end

	finished = true
	prompt.Text = "Tap to continue"

	-- Wait for the dismissing tap, but never trap the player forever if
	-- something above went wrong.
	local deadline = os.clock() + 30
	while overlay.Parent and os.clock() < deadline do
		task.wait(0.1)
	end
	if overlay.Parent then
		overlay:Destroy()
	end
end

return Reveal
