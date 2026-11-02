-- Hot-reload persistence check: run, stage WRAM like the hook does, reset, and compare the player.
-- Writes the result to GEL_RELOAD_OUT (default reload_result.txt).
local wram = emu.memType.snesWorkRam
local frame, staged = 0, false
local before = {}

local function px() return emu.read(0x610, wram) + emu.read(0x611, wram) * 256 end
local function py() return emu.read(0x630, wram) + emu.read(0x631, wram) * 256 end

emu.addEventCallback(function()
    emu.setInput({ right = frame > 100 and frame < 200 }, 0, 0)
end, emu.eventType.inputPolled)

emu.addEventCallback(function()
    frame = frame + 1
    if frame == 260 and not staged then
        before = { px(), py(), emu.read(0x660, wram) }
        for a = 0x10, 0x1FF do emu.write(0x3000 + a, emu.read(a, wram), wram) end
        for a = 0x600, 0x1EFF do emu.write(0x3000 + a, emu.read(a, wram), wram) end
        emu.write(0x2FFE, 0x5A, wram); emu.write(0x2FFF, 0xA5, wram)
        staged = true
        local d = io.open((os.getenv("GEL_RELOAD_OUT") or "reload_result.txt") .. ".dbg", "w")
        d:write("staged " .. before[1] .. "\n"); d:close()
        emu.reset()
        frame = 261
    elseif staged and frame == 400 then
        local f = io.open(os.getenv("GEL_RELOAD_OUT") or "reload_result.txt", "w")
        f:write(string.format("before x=%d y=%d type=%d\nafter  x=%d y=%d type=%d\n",
            before[1], before[2], before[3], px(), py(), emu.read(0x660, wram)))
        f:close()
        emu.stop(0)
    end
end, emu.eventType.endFrame)
