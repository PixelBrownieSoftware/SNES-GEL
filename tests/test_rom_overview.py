import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rom_overview", ROOT / "tools" / "rom-overview" / "rom.py")
rom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rom)


class RomOverviewTests(unittest.TestCase):
    def project(self, config=None, layout=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name)
        if config is not None:
            (path / "gel.json").write_text(json.dumps(config), encoding="utf-8")
        if layout is not None:
            (path / "build").mkdir()
            (path / "build" / "rom-layout.json").write_text(json.dumps(layout), encoding="utf-8")
        return path

    def test_apply_keeps_other_keys_and_sets_values(self):
        path = self.project({"header": {"title": "X"}, "sfx_echo": {"a": 1}})
        target, text = rom.apply_settings(path, {"rom_size_kb": 512, "asset_start": 0xD000})
        data = json.loads(text)
        self.assertEqual(data["header"]["rom_size_kb"], 512)
        self.assertEqual(data["header"]["title"], "X")
        self.assertEqual(data["sfx_echo"], {"a": 1})
        self.assertEqual(data["layout"]["asset_start"], 0xD000)

    def test_default_asset_start_is_removed(self):
        path = self.project({"layout": {"asset_start": 0xD000}})
        _, text = rom.apply_settings(path, {"asset_start": 0xC000})
        self.assertNotIn("layout", json.loads(text))

    def test_rejects_bad_values(self):
        path = self.project({})
        for payload in ({"rom_size_kb": 100}, {"rom_size_kb": True}, {"sram_kb": 3},
                        {"asset_start": 0xC001}, {"asset_start": 0x1000}, {"title": ""},
                        {"title": "x" * 22}, {"region": 99}, {"version": -1}, []):
            with self.assertRaises(ValueError, msg=str(payload)):
                rom.apply_settings(path, payload)

    def test_describe_without_build(self):
        result = rom.describe(self.project({}))
        self.assertIsNone(result["build"])
        self.assertEqual(result["asset_start"], 0xC000)

    def test_describe_roles(self):
        layout = {"rom_kb": 256, "spc_bank": 0x81, "soundbank_bank": 0x82, "first_free_bank": 0x84,
                  "banks": [{"bank": 0x80 + i, "used": 100} for i in range(5)] +
                            [{"bank": 0x85, "used": 0}]}
        result = rom.describe(self.project({}, layout))
        roles = [b["role"] for b in result["build"]["banks"]]
        self.assertEqual(roles, ["code", "spc", "soundbank", "soundbank", "data", "data"])
        self.assertEqual(result["build"]["needed_kb"], 256)


if __name__ == "__main__":
    unittest.main()
