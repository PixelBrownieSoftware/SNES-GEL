-- Map streaming check for tall levels. Teleports the player up and down the level, waits for
-- the streamer, and dumps the BG2 map region of VRAM plus the camera so a script can verify that
-- every visible row holds the right map row.
-- Env: GEL_STREAM_OUT (result file), GEL_STREAM_MAXY (world pixel height of the level).
-- Player slot addresses: x $610, y $630 (see build_output.asm).
local wram = emu.memType.snesWorkRam
local vram = emu.memType.snesVideoRam
local OUT = os.getenv("GEL_STREAM_OUT") or "stream_result.txt"
local MAXY = tonumber(os.getenv("GEL_STREAM_MAXY") or "800")
local function ww(a, v) emu.write(a, v & 0xFF, wram); emu.write(a + 1, (v >> 8) & 0xFF, wram) end
local function rw(a) return emu.read(a, wram) | (emu.read(a + 1, wram) << 8) end

-- cam_y address comes from the baked layout; the script receives it from the env.
local CAMY = tonumber(os.getenv("GEL_STREAM_CAMY") or "34")
local f = assert(io.open(OUT, "w"))
local targets = { MAXY - 60, 300, 60, 500, MAXY - 60, 10, 400 }
local frame, idx = 0, 1
emu.addEventCallback(function() emu.setInput({}, 0, 0) end, emu.eventType.inputPolled)
emu.addEventCallback(function()
  frame = frame + 1
  if frame % 90 == 1 and idx <= #targets then
    -- freeze the player in mid-air at the target by zeroing velocity and teleporting
    ww(0x610, 100); ww(0x630, targets[idx])
  end
  if frame % 90 == 80 and idx <= #targets then
    local bytes = {}
    for a = 0x8000, 0x8FFF do bytes[#bytes + 1] = string.format("%02x", emu.read(a, vram)) end
    f:write(string.format("%d %d %s\n", targets[idx], rw(CAMY), table.concat(bytes)))
    idx = idx + 1
  end
  if idx > #targets then f:close(); emu.stop(0) end
end, emu.eventType.endFrame)
