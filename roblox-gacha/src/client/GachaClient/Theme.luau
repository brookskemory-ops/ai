--!strict
--[[
	Colors, sizes and the small helpers every other UI file uses.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage:WaitForChild("GachaCore"):WaitForChild("Config"))

local Theme = {}

Theme.background = Color3.fromRGB(18, 19, 26)
Theme.panel = Color3.fromRGB(28, 30, 41)
Theme.panelLight = Color3.fromRGB(40, 43, 58)
Theme.text = Color3.fromRGB(238, 240, 246)
Theme.textDim = Color3.fromRGB(150, 156, 172)
Theme.accent = Color3.fromRGB(255, 189, 74)
Theme.danger = Color3.fromRGB(255, 105, 97)

Theme.font = Enum.Font.GothamMedium
Theme.fontBold = Enum.Font.GothamBold

function Theme.rarityColor(rarity: string): Color3
	local hex = Config.RarityColor[rarity]
	if not hex then
		return Theme.textDim
	end
	return Color3.fromHex(string.format("%06X", hex))
end

--[[
	Instance.new with properties and children in one expression. Callers
	annotate the local (`local frame: Frame = create(...)`) so the type checker
	still has something to work with.
]]
function Theme.create(className: string, props: { [string]: any }, children: { Instance }?): any
	local instance = Instance.new(className)
	for key, value in props do
		(instance :: any)[key] = value
	end
	if children then
		for _, child in children do
			child.Parent = instance
		end
	end
	return instance
end

function Theme.corner(radius: number): UICorner
	local corner: UICorner = Theme.create("UICorner", { CornerRadius = UDim.new(0, radius) })
	return corner
end

function Theme.padding(pixels: number): UIPadding
	local padding: UIPadding = Theme.create("UIPadding", {
		PaddingTop = UDim.new(0, pixels),
		PaddingBottom = UDim.new(0, pixels),
		PaddingLeft = UDim.new(0, pixels),
		PaddingRight = UDim.new(0, pixels),
	})
	return padding
end

function Theme.commas(value: number): string
	local text = string.format("%d", math.floor(value))
	local out = text:reverse():gsub("(%d%d%d)", "%1,"):reverse()
	return (out:gsub("^,", ""))
end

return Theme
