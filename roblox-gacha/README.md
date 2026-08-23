# roblox-gacha

A working gacha system for Roblox: server-authoritative rolls, pity, rate-up
banners with a 50/50 and a guarantee, duplicate conversion, session-locked
saves, and an odds panel that is generated from the same numbers the server
rolls against.

The probability model is pure Luau with no Roblox dependencies, so the part
that decides what you pulled can be run and tested outside the engine. It is,
here: 25 tests, including a two-million-pull simulation checked against a
closed-form solution of the same chain.

```
src/shared/GachaCore/Config.luau      every tunable number, and nothing else
src/shared/GachaCore/GachaMath.luau   pity, rolling, and the published odds
src/shared/GachaRemotes.luau          remote names, in one place
src/server/Gacha/                     rolls, inventory, currency, saving
src/client/GachaClient/               summon screen, reveal, rates panel
tests/                                the suite, runnable without Roblox
tools/simulate.luau                   what a banner costs, before you ship it
```

## Getting it into Studio

With [Rojo](https://rojo.space):

```bash
rojo serve            # then connect from the Rojo plugin in Studio
# or
rojo build -o gacha.rbxlx && open gacha.rbxlx
```

Then in Studio: **Game Settings → Security → Enable Studio Access to API
Services**, or saving will not work and the kit will fall back to volatile
profiles with a warning in the output.

Without Rojo, copy by hand — `src/shared/GachaCore` into ReplicatedStorage as a
folder of ModuleScripts, `src/shared/GachaRemotes.luau` into ReplicatedStorage
as a ModuleScript, `src/server/Gacha` into ServerScriptService (the
`init.server.luau` becomes the Script, the rest become its children), and
`src/client/GachaClient` into StarterPlayer → StarterPlayerScripts the same way.

Press play. You start with 1,600 gems, which is one ten-pull.

## Running the math without Roblox

You need the [Luau](https://github.com/luau-lang/luau/releases) CLI — one
binary, no project setup.

```bash
luau tests/run.luau              # 25 tests, ~3 seconds
luau tools/simulate.luau -a 50000  # what your current banner costs
```

The simulator prints the numbers you actually need before shipping a banner:

```
  rarity                       advertised    with pity
  ------------------------------------------------------------
  Legendary                     0.900%      1.783%
  Legendary — featured unit     0.450%      1.188%
  Epic                          5.100%     11.677%

  average pulls per Legendary              56.1   (8,976 gems)
  average pulls per featured unit          84.2   (13,464 gems)

                        median      75th      90th      99th
  ------------------------------------------------------------
  pulls to featured         79       114       153       160
  gems to featured      12,640    18,240    24,480    25,600
```

The gap between 0.900% and 1.783% is the whole point of pity, and the gap
between the median player (79 pulls) and the unluckiest 1% (160) is the thing
to look at hardest. That tail is who writes the reviews.

## How the rolls work

Every pull runs the same four steps, in `GachaMath.roll`:

1. **Legendary check.** Flat 0.9% for the first 73 pulls of a cycle. From pull
   74 it climbs by 6 points a pull — 6.9%, 12.9%, 18.9% — and pull 90 is a
   guarantee. This is the standard shape, and it is why players talk about
   being "close to pity" rather than about the advertised rate.
2. **Epic floor.** Failing the Legendary check, an Epic is guaranteed if it has
   been 10 pulls since the last Epic or better. A ten-pull therefore always
   contains something.
3. **Rate-up.** On a Legendary, half go to the featured unit. Lose that coin
   flip and the *next* Legendary is guaranteed featured, so the worst case for
   a featured unit is 180 pulls rather than unbounded.
4. **Duplicates.** A repeat raises the unit's star level up to 5, then converts
   to shards. A duplicate is never worth nothing.

A Legendary resets both pity counters; an Epic resets only its own.

### The odds panel is generated, not written

`GachaMath.Rates.consolidated` solves the (legendary counter, epic counter)
Markov chain for its stationary distribution and reports the rate players
actually experience. `RatesPanel` renders that, plus the pity rules in plain
sentences built from the config. Retune a rate and every number and sentence in
the panel moves with it — there is no hand-maintained copy to forget.

The suite checks the solver against a closed-form renewal calculation
(`1 / E[cycle length]`, agreeing to 1e-10) and against 2,000,000 simulated
pulls. If the panel and the server ever disagree, the tests fail.

## Tuning a banner

Everything lives in `src/shared/GachaCore/Config.luau`. Add units to
`Config.Items`, list them in a banner's `pool`, put the rate-up units in
`featured.ids`, and adjust `rates` and `pity`. Then:

```bash
luau tests/run.luau && luau tools/simulate.luau
```

`GachaMath.validate` runs again at server startup and refuses to boot on a
banner whose rates do not sum to 1, whose pool references a unit that does not
exist, whose featured unit is also in the off-rate pool, or whose item is filed
under the wrong rarity. A config typo is a startup error, never a failed pull
that has already taken someone's gems.

## What the server does not trust

The client sends a banner id and a pull count. That is all it sends, and both
are checked.

- **Rarity and items are never client input.** The client is told what it got.
- **One pull at a time per player.** Two overlapping requests would both read
  the same balance, pass the same affordability check, and hand out 320 gems of
  value for 160 gems.
- **Prices come from the config, not the request.** A count that is neither 1
  nor the banner's multi size is rejected, so no client invents its own bulk
  discount.
- **Rate limited** with a token bucket. Normal play never touches it.
- **Payment cannot outlive the roll.** Gems are deducted, the roll happens
  inside a pcall, and an error refunds and reports rather than swallowing.
- **`Random.new()`, not `math.random`.** `math.random` shares its state with
  every other script in the game, so anything that reseeds it reaches into your
  drop rates.

## Saving

`Profile.luau` is a small session-locked DataStore wrapper. Two things there
matter more than the rest of this repo combined:

- **Session locking.** Roblox can briefly run the same player on two servers.
  Without a lock, both save over each other and currency duplicates or
  vanishes. A profile is claimed by one job id, and a stale claim is only
  stolen after five minutes.
- **`UpdateAsync`, never `SetAsync`.** Read-then-write as one operation, so a
  race cannot silently drop a side.

Rolls save immediately rather than waiting for the next autosave, because a
crash between the two is a refund ticket.

For a live game, swap this module for
[ProfileStore](https://github.com/MadStudioRoblox/ProfileStore). It handles
more edge cases than this does. This version exists so the kit runs with no
dependencies, and so the failure modes are visible instead of hidden.

`Products.luau` handles developer product receipts the careful way: grant only
after the save succeeds, remember the `PurchaseId` so a re-delivered receipt
pays out once, and return `NotProcessedYet` on anything unexpected so Roblox
retries instead of charging for nothing.

## Rules that apply to this feature

If you sell the currency that pays for these pulls, you are selling paid random
items, and Roblox's policy on those applies:

- **Odds must be disclosed in-experience**, before the purchase. The rates panel
  is that disclosure. Keep it reachable from the banner itself.
- **Publish the number players will find anyway.** The panel shows the
  advertised rate *and* the rate with pity. Publishing only the first reads as
  a lie the moment someone datamines the second.
- **Age and region restrictions exist and have changed more than once.** Check
  the current rules in the Creator Docs under Monetization before you ship, and
  again before a major banner — this is the part of the policy most likely to
  have moved since this was written.

None of that is legal advice, and the policy is Roblox's, not this repo's.

## What is not here

Deliberate omissions, roughly in the order you will want them:

- **An inventory screen.** The data is synced (`Sync.snapshot` carries the full
  collection and a 100-pull history); nothing draws it yet.
- **Art.** Cards are rarity-tinted rectangles. Swap the `Art` frame in
  `Reveal.luau` for an ImageLabel and give `Config.Items` an `image` field.
- **A shard shop**, banner end dates, daily free pulls, and a first-pull
  guarantee for new accounts.
- **Analytics.** Log pulls per player per banner to somewhere you can query.
  You cannot tune a banner you cannot see.
