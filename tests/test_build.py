"""End-to-end build tests: they run the real build.py on a tiny generated project.
Skipped where the Asar DLL can't be used (it ships as a Windows DLL)."""
import hashlib
import os
import shutil
import subprocess
import sys
import wave

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_PY = os.path.join(ROOT, "compiler", "build.py")

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="bundled Asar is a Windows DLL")

GAME = '#include "snes.gel"\n#include "assets.gel"\nfn init_game() { std_snes_init(); while (1) { } }\n'


def smconv_available():
    return bool(shutil.which("smconv")) or os.path.isfile(os.path.join(ROOT, "tools", "smconv", "smconv.exe"))


def make_png(path, color):
    img = Image.new("P", (16, 16), 0)
    img.putpalette([0, 0, 0, *color] + [0, 0, 0] * 14)
    for x in range(8):
        for y in range(8):
            img.putpixel((x, y), 1)
    img.save(path)


def make_wav(path, n=800):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join((i % 40 * 300 - 6000).to_bytes(2, "little", signed=True) for i in range(n)))


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    (p / "assets").mkdir(parents=True)
    (p / "game.gel").write_text(GAME)
    make_png(p / "assets" / "hero.png", (255, 0, 0))
    return p


def run_build(project, *flags):
    r = subprocess.run([sys.executable, BUILD_PY, str(project), *flags],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def rom_hash(project):
    with open(project / "build" / "game.sfc", "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_second_build_uses_cache_and_gives_identical_rom(project):
    rc, out = run_build(project)
    assert rc == 0, out
    assert "unchanged" not in out
    first = rom_hash(project)
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" in out and "sound: unchanged" in out
    assert rom_hash(project) == first


def test_cached_build_matches_a_forced_full_build(project):
    run_build(project)
    run_build(project)
    cached = rom_hash(project)
    rc, out = run_build(project, "--no-cache")
    assert rc == 0, out
    assert "unchanged" not in out
    assert rom_hash(project) == cached


def test_changing_an_asset_rebuilds_assets_but_not_sound(project):
    run_build(project)
    before = rom_hash(project)
    make_png(project / "assets" / "hero.png", (0, 255, 0))
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" not in out
    assert "sound: unchanged" in out
    assert rom_hash(project) != before


def test_corrupted_cached_output_is_rebuilt(project):
    run_build(project)
    good = rom_hash(project)
    pal = project / "build" / "hero.pal"
    pal.write_bytes(b"\x00" * pal.stat().st_size)
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" not in out
    assert rom_hash(project) == good


def test_deleted_cached_output_is_rebuilt(project):
    run_build(project)
    good = rom_hash(project)
    os.remove(project / "build" / "hero.bin")
    rc, out = run_build(project)
    assert rc == 0, out
    assert (project / "build" / "hero.bin").exists()
    assert rom_hash(project) == good


def test_failed_conversion_is_not_cached_and_recovers(project):
    run_build(project)
    good = rom_hash(project)
    (project / "assets" / "level.tmj").write_text("{ this is not json")
    rc, out = run_build(project)
    assert rc != 0
    (project / "assets" / "level.tmj").unlink()
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" not in out
    assert rom_hash(project) == good


def test_editor_session_files_do_not_invalidate(project):
    run_build(project)
    (project / "assets" / "x.tiled-session").write_text("{}")
    rc, out = run_build(project)
    assert "assets: unchanged" in out


def test_stale_outputs_of_removed_assets_are_cleaned(project):
    make_png(project / "assets" / "extra.png", (0, 0, 255))
    run_build(project)
    assert (project / "build" / "extra.bin").exists()
    os.remove(project / "assets" / "extra.png")
    rc, out = run_build(project)
    assert rc == 0, out
    assert not (project / "build" / "extra.bin").exists()


@pytest.mark.skipif(not smconv_available(), reason="smconv not built (tools/setup_smconv.bat)")
def test_sound_changes_rebuild_sound_only(project):
    (project / "sfx").mkdir()
    make_wav(project / "sfx" / "beep.wav")
    rc, out = run_build(project)
    assert rc == 0, out
    first = rom_hash(project)
    rc, out = run_build(project)
    assert "sound: unchanged" in out and "assets: unchanged" in out
    assert rom_hash(project) == first
    make_wav(project / "sfx" / "beep.wav", n=1200)
    rc, out = run_build(project)
    assert rc == 0, out
    assert "sound: unchanged" not in out
