-- Deterministic input replay for the Mesen test runner. Logs the player's state every frame so
-- two ROMs (before / after a refactor) can be diffed. Usage:
--   Mesen.exe --testrunner game.sfc tools\replay_test.lua      (set GEL_REPLAY_OUT / GEL_REPLAY_LAYOUT)
-- GEL_REPLAY_LAYOUT: "globals" (player_* variables) or "slot" (entity table slot 0).
local wram = emu.memType.snesWorkRam
local OUT = os.getenv("GEL_REPLAY_OUT") or "replay_log.txt"
local LAYOUT = os.getenv("GEL_REPLAY_LAYOUT") or "globals"
local FRAMES = tonumber(os.getenv("GEL_REPLAY_FRAMES") or "15000")

-- Addresses come from build/game.layout and the table comment in build_output.asm.
local A = {
  globals = { x = 16, y = 18, subx = 20, suby = 22, vx = 24, vy = 26 },
  slot    = { x = 0x670, y = 0x690, subx = 0x710, suby = 0x730, vx = 0x6D0, vy = 0x6F0 },
  slot3   = { x = 0x610, y = 0x630, subx = 0x6B0, suby = 0x6D0, vx = 0x670, vy = 0x690 },
}
local P = A[LAYOUT] or A.slot
local STATE, DASH, CHAR, FLAGS, CAMX, CAMY, AIR = 28, 29, 30, 38, 32, 34, 39
if LAYOUT == "slot2" then   -- state fields moved into the entity table
  P = A.slot
  STATE, FLAGS, DASH, CHAR = 0x750, 0x760, 0x770, 0x7A0
elseif LAYOUT == "slot3" then   -- table moved up after the unused Bullets table went away
  STATE, FLAGS, DASH, CHAR = 0x6F0, 0x700, 0x710, 0x740
end

local function rb(a) return emu.read(a, wram) end
local function rw(a) return emu.read(a, wram) | (emu.read(a + 1, wram) << 8) end
local function ww(a, v) emu.write(a, v & 0xFF, wram); emu.write(a + 1, (v >> 8) & 0xFF, wram) end

local log = assert(io.open(OUT, "w"))
local seed = 12345
local function rnd(n)
  seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
  return (seed >> 8) % n
end

-- Spots to teleport to (x, y): spawn, spring, a wall for climbing, high ledges.
local spots = { {41, 186}, {392, 100}, {10, 120}, {120, 60}, {300, 100}, {470, 60}, {200, 150} }

local pad = {}
local hold = 0
local frame = 0
local function choose()
  pad = {}
  local r = rnd(10)
  if r < 4 then pad.right = true elseif r < 7 then pad.left = true end
  if rnd(2) == 0 then pad.b = true end
  if rnd(3) == 0 then pad.down = true end
  if rnd(8) == 0 then pad.up = true end
  if rnd(40) == 0 then pad.x = true end
  if rnd(400) == 0 then pad.select = true end
  hold = 1 + rnd(10)
end

emu.addEventCallback(function()
  if hold <= 0 then choose() end
  hold = hold - 1
  emu.setInput(pad, 0, 0)
end, emu.eventType.inputPolled)

emu.addEventCallback(function()
  frame = frame + 1
  if frame > 60 and frame % 500 == 0 then
    local s = spots[(frame // 500) % #spots + 1]
    ww(P.x, s[1]); ww(P.y, s[2])
  end
  if frame > 30 then
    log:write(string.format("%d %d %d %d %d %d %d %d %d %d %d %d\n", frame,
      rw(P.x), rw(P.y), rw(P.subx), rw(P.suby), rw(P.vx), rw(P.vy),
      rb(FLAGS), rb(STATE), rb(CHAR), rb(DASH), rw(CAMX)))
  end
  if frame >= FRAMES then log:close(); emu.stop(0) end
end, emu.eventType.endFrame)

