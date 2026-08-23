--!strict
--[[
	Runs the gacha test suite outside Roblox.

		luau tests/run.luau

	Exits non-zero on failure, so it drops straight into CI.
]]
local testkit = require("./testkit")
require("./GachaMath.spec")

print("")
print("gacha test suite")
print("")

local failures = testkit.run()
if failures > 0 then
	error(`{failures} test(s) failed`, 0)
end
