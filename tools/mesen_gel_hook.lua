-- ==============================================================================
-- mesen_gel_hook.lua - Hot-Reload Hook for Mesen 2
-- Mesen 2 has no Lua "reload ROM" call, so on each build signal we copy the new
-- game.sfc straight into the emulated PRG ROM and reset the console.
-- ==============================================================================

local PROJECT_DIR = "@BUILD_DIR@"   -- filled in by compiler/build.py when it writes build/gel_hook.lua
local SIGNAL_FILE = PROJECT_DIR .. "hotreload.signal"
local ROM_FILE    = PROJECT_DIR .. "game.sfc"

-- Filled from game.layout and the HB_W/HB_H defines on each dev build.
local PLAYER_X_ADDR = @PLAYER_X_ADDR@
local PLAYER_Y_ADDR = @PLAYER_Y_ADDR@
local CAM_X_ADDR    = @CAM_X_ADDR@
local CAM_Y_ADDR    = @CAM_Y_ADDR@
local COLLISION_WIDTH  = @COLLISION_WIDTH@
local COLLISION_HEIGHT = @COLLISION_HEIGHT@
local collision_bounds_enabled = false
local collision_toggle_was_down = false
local collision_config_logged = false

local function read_signal()
    local f = io.open(SIGNAL_FILE, "r")
    if not f then return nil end
    local ts = f:read("*n")
    f:read("*l")
    local name = f:read("*l")
    f:close()
    return ts, name
end

-- Ignore any signal left over from before Mesen started.
local last_modified = read_signal() or 0

local STAGE_BASE  = 0x3000   -- WRAM staging area; the ROM restores `persist` vars from here
local STAGE_MAGIC = 0x2FFE

local function stage_persist_state()
    local wram = emu.memType.snesWorkRam
    for a = 0x10, 0x1FF do
        emu.write(STAGE_BASE + a, emu.read(a, wram), wram)
    end
    for a = 0x600, 0x1EFF do   -- struct tables
        emu.write(STAGE_BASE + a, emu.read(a, wram), wram)
    end
    emu.write(STAGE_MAGIC, 0x5A, wram)
    emu.write(STAGE_MAGIC + 1, 0xA5, wram)
end

local function reload_rom()
    local f = io.open(ROM_FILE, "rb")
    if not f then
        emu.log("[GEL Hot-Reload] Cannot open " .. ROM_FILE)
        return false
    end
    local data = f:read("*a")
    f:close()

    stage_persist_state()

    local size = emu.getMemorySize and emu.getMemorySize(emu.memType.snesPrgRom) or #data
    local count = math.min(#data, size)
    for i = 1, count do
        emu.write(i - 1, data:byte(i), emu.memType.snesPrgRom)
    end
    emu.reset()
    return true
end

local function read_word(address, wram)
    return emu.read(address, wram) | (emu.read(address + 1, wram) << 8)
end

-- ---------------------------------------------------------------------------
-- Debug overlay: game/debug_overlay.lua is user-editable and reloaded on change.
-- ---------------------------------------------------------------------------
local OVERLAY_FILE = "@OVERLAY_FILE@"
local GEL_VARS = @GEL_VARS@      -- name = {type, address} for every RAM variable
local overlay_enabled = true
local overlay_toggle_was_down = false
local overlay_draw = nil
local overlay_source = nil
local overlay_check = 0

-- Helpers handed to the overlay script as `gel`.
local gel = {}
function gel.has(name) return GEL_VARS[name] ~= nil end
function gel.raw(name)          -- unsigned value as stored
    local v = GEL_VARS[name]
    if not v then return 0 end
    local wram = emu.memType.snesWorkRam
    if v[1] == "u8" or v[1] == "i8" then return emu.read(v[2], wram) end
    return read_word(v[2], wram)
end
function gel.get(name)          -- signed for i8/i16, real number for f16 (8.8 fixed point)
    local v = GEL_VARS[name]
    if not v then return 0 end
    local raw = gel.raw(name)
    local t = v[1]
    if t == "i8" and raw >= 0x80 then return raw - 0x100 end
    if (t == "i16" or t == "f16") and raw >= 0x8000 then raw = raw - 0x10000 end
    if t == "f16" then return raw / 256 end
    return raw
end
function gel.bit(name, n) return (gel.raw(name) >> n) & 1 end
function gel.hex(name)
    local v = GEL_VARS[name]
    local width = (v and (v[1] == "u8" or v[1] == "i8")) and 2 or 4
    return string.format("%0" .. width .. "X", gel.raw(name))
end
function gel.text(x, y, str, color)
    emu.drawString(x, y, str, color or 0xFFFFFF, 0x90000000)
end

-- Frame-rate counters, refreshed once per wall-clock second.
--   gel.fps        frames the emulator rendered in the last second
--   gel.logic_rate game ticks the ROM consumed in the last second (needs `ticks_seen`);
--                  it drops below fps when the game falls behind and skips catch-up steps
gel.fps = 0
gel.logic_rate = 0
local rate_second, rate_frames, rate_ticks_start = os.time(), 0, nil

local function update_rates()
    rate_frames = rate_frames + 1
    local now = os.time()
    if now == rate_second then return end
    local ticks = gel.has("ticks_seen") and gel.raw("ticks_seen") or nil
    gel.fps = rate_frames / (now - rate_second)
    if ticks and rate_ticks_start then
        gel.logic_rate = ((ticks - rate_ticks_start) % 256) / (now - rate_second)
    end
    rate_ticks_start = ticks
    rate_second, rate_frames = now, 0
end

local function load_overlay()
    local f = io.open(OVERLAY_FILE, "r")
    if not f then
        overlay_draw, overlay_source = nil, nil
        return
    end
    local src = f:read("*a")
    f:close()
    if src == overlay_source then return end
    overlay_source = src
    local chunk, err = load(src, "debug_overlay.lua", "t", _ENV)
    if not chunk then
        emu.log("[GEL Debug] debug_overlay.lua: " .. tostring(err))
        overlay_draw = nil
        return
    end
    local ok, result = pcall(chunk)
    if ok and type(result) == "function" then
        overlay_draw = result
        emu.log("[GEL Debug] debug_overlay.lua loaded")
    else
        overlay_draw = nil
        emu.log("[GEL Debug] debug_overlay.lua must `return function(gel) ... end`" ..
            (ok and "" or (": " .. tostring(result))))
    end
end

local function draw_overlay()
    overlay_check = overlay_check + 1
    if overlay_check % 30 == 1 then load_overlay() end
    if overlay_draw then
        local ok, err = pcall(overlay_draw, gel)
        if not ok then
            emu.log("[GEL Debug] overlay error: " .. tostring(err))
            overlay_draw = nil
            overlay_source = nil   -- retry on the next check
        end
    end
end

local function draw_collision_bounds()
    if not PLAYER_X_ADDR or not PLAYER_Y_ADDR or not CAM_X_ADDR or not CAM_Y_ADDR
        or not COLLISION_WIDTH or not COLLISION_HEIGHT then
        if not collision_config_logged then
            emu.log("[GEL Debug] Collision bounds unavailable: player addresses or HB_W/HB_H missing.")
            collision_config_logged = true
        end
        return
    end

    local wram = emu.memType.snesWorkRam
    local x = read_word(PLAYER_X_ADDR, wram) - read_word(CAM_X_ADDR, wram)
    -- Mesen's overlay origin sits above the SNES picture's first visible line.
    local y = read_word(PLAYER_Y_ADDR, wram) - read_word(CAM_Y_ADDR, wram) + 7
    emu.drawRectangle(x, y, COLLISION_WIDTH, COLLISION_HEIGHT, 0xFF3030, false, 2)
end

function on_end_frame()
    local ts, name = read_signal()
    if ts and ts > last_modified then
        last_modified = ts
        local info = (name and name ~= "") and (" (" .. name .. ")") or ""
        if reload_rom() then
            emu.log("[GEL Hot-Reload] ROM reloaded" .. info)
        end
    end

    local toggle_down = emu.isKeyPressed("F2")
    if toggle_down and not collision_toggle_was_down then
        collision_bounds_enabled = not collision_bounds_enabled
        emu.log("[GEL Debug] Collision bounds " ..
            (collision_bounds_enabled and "ON" or "OFF") .. " (F2)")
    end
    collision_toggle_was_down = toggle_down

    local overlay_down = emu.isKeyPressed("F3")
    if overlay_down and not overlay_toggle_was_down then
        overlay_enabled = not overlay_enabled
        emu.log("[GEL Debug] Overlay " .. (overlay_enabled and "ON" or "OFF") .. " (F3)")
    end
    overlay_toggle_was_down = overlay_down
    update_rates()
    if overlay_enabled then
        draw_overlay()
    end

    if collision_bounds_enabled then
        draw_collision_bounds()
    end
end

emu.addEventCallback(on_end_frame, emu.eventType.endFrame)
emu.log("[GEL Hot-Reload] Active. Watching: " .. SIGNAL_FILE)
emu.log("[GEL Debug] Press F2 to toggle the player collision bounds, F3 for the debug overlay.")
