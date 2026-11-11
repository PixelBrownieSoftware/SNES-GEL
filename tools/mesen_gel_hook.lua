-- ==============================================================================
-- mesen_gel_hook.lua - Hot-Reload Hook for Mesen 2
-- Mesen 2 has no Lua "reload ROM" call, so on each build signal we copy the new
-- game.sfc straight into the emulated PRG ROM and reset the console.
-- ==============================================================================

local PROJECT_DIR = "@BUILD_DIR@"   -- filled in by compiler/build.py when it writes build/gel_hook.lua
local SIGNAL_FILE = PROJECT_DIR .. "hotreload.signal"
local ROM_FILE    = PROJECT_DIR .. "game.sfc"
local ROM_MODE    = "@ROM_MODE@"

-- Filled from game.layout and the HB_W/HB_H defines on each dev build.
local PLAYER_X_ADDR = @PLAYER_X_ADDR@
local PLAYER_Y_ADDR = @PLAYER_Y_ADDR@
local CAM_X_ADDR    = @CAM_X_ADDR@
local CAM_Y_ADDR    = @CAM_Y_ADDR@
local COLLISION_WIDTH  = @COLLISION_WIDTH@
local COLLISION_HEIGHT = @COLLISION_HEIGHT@
local LEVEL_WIDTH  = @LEVEL_WIDTH@
local LEVEL_HEIGHT = @LEVEL_HEIGHT@
local collision_bounds_enabled = false
local collision_toggle_was_down = false
local collision_config_logged = false

local function read_signal()
    local f = io.open(SIGNAL_FILE, "r")
    if not f then return nil end
    local ts = f:read("*n")
    f:read("*l")
    local name = f:read("*l")
    local mode = f:read("*l")
    f:close()
    return ts, name, mode
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

local function reload_rom(mode)
    if mode ~= ROM_MODE then
        emu.log("[GEL Hot-Reload] ROM mapping changed or is missing; restart Mesen.")
        return false
    end
    local f = io.open(ROM_FILE, "rb")
    if not f then
        emu.log("[GEL Hot-Reload] Cannot open " .. ROM_FILE)
        return false
    end
    local data = f:read("*a")
    f:close()

    local size = emu.getMemorySize and emu.getMemorySize(emu.memType.snesPrgRom) or #data
    if #data ~= size then
        emu.log("[GEL Hot-Reload] ROM size changed; restart Mesen.")
        return false
    end
    stage_persist_state()
    for i = 1, size do
        emu.write(i - 1, data:byte(i), emu.memType.snesPrgRom)
    end
    emu.reset()
    return true
end

local function read_word(address, wram)
    return emu.read(address, wram) | (emu.read(address + 1, wram) << 8)
end

local SLOPE_FLAG = 0x80
local TILE_SIZE = 8
local SLOPE_DRAW_RADIUS = 64
local MODE_PLATFORMER = 0
local slope_collision = nil
local slope_table = nil
local slope_collision_logged = false

local function load_slope_collision()
    slope_collision = nil
    slope_table = nil
    if not LEVEL_WIDTH or not LEVEL_HEIGHT then return end
    local f = io.open(PROJECT_DIR .. "level_coll.bin", "rb")
    local t = io.open(PROJECT_DIR .. "level_slopes.bin", "rb")
    if not f or not t then
        if f then f:close() end
        if t then t:close() end
        if not slope_collision_logged then
            emu.log("[GEL Debug] Cannot open level_coll.bin/level_slopes.bin for slope visualization.")
            slope_collision_logged = true
        end
        return
    end
    local data = f:read("*a")
    local table_data = t:read("*a")
    f:close()
    t:close()
    if #data < LEVEL_WIDTH * LEVEL_HEIGHT then
        emu.log("[GEL Debug] level_coll.bin is smaller than the generated level dimensions.")
        return
    end
    slope_collision = data
    slope_table = table_data
end

load_slope_collision()

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
function gel.draw_slopes()
    if not collision_bounds_enabled or not slope_collision or not slope_table or
       not gel.has("cam_x") or not gel.has("cam_y") or
       not gel.has("player_x") or not gel.has("player_y") or
       not gel.has("game_mode") or gel.raw("game_mode") ~= MODE_PLATFORMER then
        return
    end
    local cam_x, cam_y = gel.raw("cam_x"), gel.raw("cam_y")
    local player_x, player_y = gel.raw("player_x"), gel.raw("player_y")
    local first_x = math.max(0, math.floor(cam_x / TILE_SIZE),
        math.floor((player_x - SLOPE_DRAW_RADIUS) / TILE_SIZE))
    local first_y = math.max(0, math.floor(cam_y / TILE_SIZE) - 1,
        math.floor((player_y - SLOPE_DRAW_RADIUS) / TILE_SIZE))
    local last_x = math.min(LEVEL_WIDTH - 1, math.floor((cam_x + 255) / TILE_SIZE),
        math.floor((player_x + COLLISION_WIDTH + SLOPE_DRAW_RADIUS) / TILE_SIZE))
    local last_y = math.min(LEVEL_HEIGHT - 1, math.floor((cam_y + 223) / TILE_SIZE) + 1,
        math.floor((player_y + COLLISION_HEIGHT + SLOPE_DRAW_RADIUS) / TILE_SIZE))

    for tile_y = first_y, last_y do
        for tile_x = first_x, last_x do
            local index = tile_y * LEVEL_WIDTH + tile_x + 1
            local code = slope_collision:byte(index)
            if code and (code & SLOPE_FLAG) ~= 0 then
                local entry = (code & 0x7F) * 8
                local color = 0xFF4040
                local first_surface = slope_table:byte(entry + 1) or 8
                local last_surface = slope_table:byte(entry + 8) or 8
                if last_surface < first_surface then color = 0x4080FF end
                for pixel_x = 0, TILE_SIZE - 1 do
                    local surface_y = (slope_table:byte(entry + pixel_x + 1) or 8) - 8
                    local screen_x = tile_x * TILE_SIZE + pixel_x - cam_x
                    local screen_y = tile_y * TILE_SIZE + surface_y - cam_y + 7
                    emu.drawRectangle(screen_x, screen_y, 1, 1, color, true, 1)
                end
            end
        end
    end
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
    local ts, name, mode = read_signal()
    if ts and ts > last_modified then
        last_modified = ts
        local info = (name and name ~= "") and (" (" .. name .. ")") or ""
        if reload_rom(mode) then
            load_slope_collision()
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
