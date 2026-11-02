-- Checks that a non-player entity runs the shared physics: a gem flagged MODE_FLAG_PHYSICS is
-- dropped from the air and must fall, land on the ground and pick the idle clip.
-- Table addresses are from game/build/build_output.asm (slot stride: u8 +1, u16 +2).
local wram = emu.memType.snesWorkRam
local E = 1
local TYPE, X, Y, VY, FLAGS, CLIP = 0x600 + E, 0x610 + 2 * E, 0x630 + 2 * E, 0x690 + 2 * E, 0x700 + E, 0x780 + E
local function rb(a) return emu.read(a, wram) end
local function rw(a) return emu.read(a, wram) | (emu.read(a + 1, wram) << 8) end
local function ww(a, v) emu.write(a, v & 0xFF, wram); emu.write(a + 1, (v >> 8) & 0xFF, wram) end

local frame, out, lastY, minY = 0, {}, nil, 9999
emu.addEventCallback(function() emu.setInput({}, 0, 0) end, emu.eventType.inputPolled)
emu.addEventCallback(function()
  frame = frame + 1
  if frame == 100 then
    out[#out + 1] = string.format("slot1 type=%d", rb(TYPE))
    emu.write(FLAGS, 0x10, wram)   -- MODE_FLAG_PHYSICS
    ww(X, 200); ww(Y, 100)
  end
  if frame == 102 or frame == 105 or frame == 160 or frame == 400 then
    out[#out + 1] = string.format("frame %d: y=%d vy=%d flags=%02X clip=%d",
      frame, rw(Y), rw(VY), rb(FLAGS), rb(CLIP))
  end
  if frame >= 400 then
    local f = io.open(os.getenv("GEL_PHYS_OUT") or "phys_result.txt", "w")
    f:write(table.concat(out, "\n") .. "\n"); f:close(); emu.stop(0)
  end
end, emu.eventType.endFrame)
