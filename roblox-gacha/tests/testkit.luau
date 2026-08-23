--!strict
--[[
	A test kit small enough to read in one sitting. Run the suite with:

		luau tests/run.luau

	These tests deliberately do not need Roblox. Everything under test lives in
	src/shared/GachaCore, which is why that folder must never require a Roblox
	API.
]]

local testkit = {}

type Case = { name: string, fn: () -> () }

local cases: { Case } = {}
local currentSuite = ""

function testkit.suite(name: string)
	currentSuite = name
end

function testkit.test(name: string, fn: () -> ())
	table.insert(cases, { name = `{currentSuite}: {name}`, fn = fn })
end

function testkit.assert(condition: any, message: string)
	if not condition then
		error(message, 2)
	end
end

function testkit.equal(actual: any, expected: any, message: string)
	if actual ~= expected then
		error(`{message}\n  expected: {tostring(expected)}\n  actual:   {tostring(actual)}`, 2)
	end
end

function testkit.near(actual: number, expected: number, tolerance: number, message: string)
	if math.abs(actual - expected) > tolerance then
		error(
			`{message}\n  expected: {string.format("%.8f", expected)} +/- {string.format("%.8f", tolerance)}`
				.. `\n  actual:   {string.format("%.8f", actual)}`
				.. `\n  off by:   {string.format("%.8f", math.abs(actual - expected))}`,
			2
		)
	end
end

function testkit.throws(fn: () -> (), message: string)
	local ok = pcall(fn)
	if ok then
		error(message, 2)
	end
end

--[[
	A seeded xorshift32. Its only job is to make failures reproducible: a test
	that fails on seed 7 fails on seed 7 forever, on any Luau build. The
	statistical tests use many seeds rather than trusting one.
]]
function testkit.rng(seed: number): () -> number
	local state = bit32.bxor(seed * 2654435761 % 4294967296, 0x9E3779B9)
	if state == 0 then
		state = 1
	end
	return function(): number
		state = bit32.bxor(state, bit32.lshift(state, 13))
		state = bit32.bxor(state, bit32.rshift(state, 17))
		state = bit32.bxor(state, bit32.lshift(state, 5))
		return state / 4294967296
	end
end

function testkit.run(): number
	local failures = 0
	local started = os.clock()
	for _, case in cases do
		-- Cast because the checker will not hand back pcall's second return for a
		-- function typed as returning nothing.
		local ok, err = pcall(case.fn :: any)
		if ok then
			print(`  ok    {case.name}`)
		else
			failures += 1
			print(`  FAIL  {case.name}`)
			for line in string.gmatch(tostring(err), "[^\n]+") do
				print(`          {line}`)
			end
		end
	end
	local elapsed = os.clock() - started
	print("")
	print(`  {#cases - failures}/{#cases} passed in {string.format("%.2f", elapsed)}s`)
	return failures
end

return testkit
