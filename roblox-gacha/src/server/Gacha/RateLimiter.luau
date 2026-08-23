--!strict
--[[
	A token bucket per player.

	Rolling is cheap for a client to ask for and expensive for a server to
	answer: a DataStore write, a pity update, a replicated response. Without a
	limit, one modified client can pin a server by spamming the remote. The
	bucket lets normal play through untouched — a player mashing the ten-pull
	button uses about one token a second — and flattens anything faster.
]]

local RateLimiter = {}
RateLimiter.__index = RateLimiter

export type RateLimiter = typeof(setmetatable(
	{} :: {
		burst: number,
		refillPerSecond: number,
		tokens: { [Player]: number },
		lastRefill: { [Player]: number },
	},
	RateLimiter
))

function RateLimiter.new(burst: number, refillPerSecond: number): RateLimiter
	return setmetatable({
		burst = burst,
		refillPerSecond = refillPerSecond,
		tokens = {},
		lastRefill = {},
	}, RateLimiter) :: any
end

-- Returns true if the player may act now, spending a token.
function RateLimiter.consume(self: RateLimiter, player: Player): boolean
	local now = os.clock()
	local tokens = self.tokens[player] or self.burst
	local last = self.lastRefill[player] or now
	tokens = math.min(self.burst, tokens + (now - last) * self.refillPerSecond)
	self.lastRefill[player] = now

	if tokens < 1 then
		self.tokens[player] = tokens
		return false
	end
	self.tokens[player] = tokens - 1
	return true
end

function RateLimiter.forget(self: RateLimiter, player: Player)
	self.tokens[player] = nil
	self.lastRefill[player] = nil
end

return RateLimiter
