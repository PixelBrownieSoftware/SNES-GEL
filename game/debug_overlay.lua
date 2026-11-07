-- Debug overlay for Mesen (toggle with F3). Edit and save: it reloads within half a second.
-- Must return a function that receives the `gel` helper object:
--   gel.get("name")       signed / fixed-point value (f16 shows as a real number)
--   gel.raw("name")       unsigned value as stored
--   gel.hex("name")       zero-padded hex string
--   gel.bit("name", n)    bit n of a variable
--   gel.has("name")       true if the variable exists in this build
--   gel.text(x, y, str, color)
--   gel.draw_slopes() draws the packed collision profiles over the visible level
--   gel.fps               emulator frames per second (updates once a second)
--   gel.logic_rate        game ticks per second the ROM actually processed (should match fps)
-- Any dp/persist variable in game.gel (or the stdlib) can be read by name.

return function(gel)
    gel.draw_slopes()
    local lines = {
        string.format("FPS: %.0f  LOGIC: %.0f/s", gel.fps, gel.logic_rate),
        string.format("X: %d  Y: %d", gel.get("player_x"), gel.get("player_y")),
        string.format("VX: %.2f  VY: %.2f", gel.get("player_vx"), gel.get("player_vy")),
        string.format("PAD: %s", gel.hex("pad1_held")),
        string.format("GROUND: %d  DIR: %s", gel.bit("mode_flags", 0),
            gel.bit("mode_flags", 2) == 1 and "L" or "R"),
        -- string.format("MODE: %d  ABILITY: %d  DASH: %d", gel.get("game_mode"),
        --     gel.get("player_ability"), gel.get("dash_frames")),
    }
    for i, line in ipairs(lines) do
        gel.text(145, 16 + (i - 1) * 9, line)
    end
    -- gel.text(0, 16 + (1) * 9, gel.hex("player_test_anim_files"))
    -- gel.text(0, 16 + (2) * 9, gel.get("player_curr_anim"))
    -- gel.text(0, 16 + (3) * 9, gel.hex("player_anim_addr"))
    -- gel.text(0, 16 + (4) * 9, gel.get("dummy_vx"))
    -- gel.text(0, 16 + (5) * 9, gel.get("dummy_fric"))
    for i, bit in ipairs({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}) do
        -- Make it so that it shows each bit descriptivley based on the defines from player.gel
        local bit_names = {
            [0] = "HORIZONTAL_PRESSED",
            [1] = "VERTICAL_PRESSED",
            [2] = "HORIZONTAL_DIR",
            [3] = "VERTICAL_DIR",
            [4] = "ACTION_PRESSED",
            [5] = "ACTION2_PRESSED",
            [6] = "ACTION3_PRESSED",
            [7] = "ACTION4_PRESSED",
            [8] = "ACTION_RELEASED",
            [9] = "ACTION2_RELEASED",
            [10] = "ACTION3_RELEASED",
            [11] = "ACTION4_RELEASED",
            [12] = "ACTION_PUSHED",
            [13] = "ACTION2_PUSHED",
            [14] = "ACTION3_PUSHED",
            [15] = "ACTION4_PUSHED"
        }
        -- 2 per horizontal row
        if (i % 2 == 0) then
            gel.text(0, 16 + (6 + math.floor(i / 2)) * 9, string.format("%s: %d", bit_names[bit], gel.bit("input_presses", bit)))
        end
        -- gel.text(0 + (i - 1) * 40, 16 + (6) * 9, string.format("%s: %d", bit_names[bit], gel.bit("input_presses", bit)))
    end
end
