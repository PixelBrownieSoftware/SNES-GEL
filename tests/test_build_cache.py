import os

import build_cache as bc


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_key_changes_with_content_and_settings(tmp_path):
    a = tmp_path / "a.png"
    write(a, b"one")
    k1 = bc.make_key(tmp_path, [a], "s")
    assert bc.make_key(tmp_path, [a], "s") == k1
    assert bc.make_key(tmp_path, [a], "t") != k1
    write(a, b"two")
    assert bc.make_key(tmp_path, [a], "s") != k1


def test_key_changes_when_a_file_is_added(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a, b"1")
    write(b, b"1")
    assert bc.make_key(tmp_path, [a], "") != bc.make_key(tmp_path, [a, b], "")


def test_missing_input_does_not_crash_and_differs(tmp_path):
    a = tmp_path / "a"
    write(a, b"x")
    assert bc.make_key(tmp_path, [a, tmp_path / "gone"], "") != bc.make_key(tmp_path, [a], "")


def make_cache(tmp_path):
    build = tmp_path / "build"
    build.mkdir(exist_ok=True)
    return build, bc.StageCache(str(build))


def test_hit_requires_matching_key_and_intact_outputs(tmp_path):
    build, cache = make_cache(tmp_path)
    write(build / "out.bin", b"data")
    cache.store("s", "key1", ["out.bin"], {"x": 1})
    assert cache.lookup("s", "key1")["extra"] == {"x": 1}
    assert cache.lookup("s", "other") is None
    assert bc.StageCache(str(build)).lookup("s", "key1") is not None   # survives a reload


def test_tampered_or_deleted_output_is_a_miss(tmp_path):
    build, cache = make_cache(tmp_path)
    write(build / "out.bin", b"data")
    cache.store("s", "k", ["out.bin"])
    write(build / "out.bin", b"DATA")
    assert cache.lookup("s", "k") is None
    write(build / "out.bin", b"data")
    assert cache.lookup("s", "k") is not None
    os.remove(build / "out.bin")
    assert cache.lookup("s", "k") is None


def test_invalidate_drops_entry_and_persists(tmp_path):
    build, cache = make_cache(tmp_path)
    write(build / "out.bin", b"data")
    cache.store("s", "k", ["out.bin"])
    assert cache.invalidate("s") == ["out.bin"]
    assert bc.StageCache(str(build)).lookup("s", "k") is None


def test_disabled_cache_never_hits(tmp_path):
    build, cache = make_cache(tmp_path)
    write(build / "out.bin", b"d")
    cache.store("s", "k", ["out.bin"])
    assert bc.StageCache(str(build), enabled=False).lookup("s", "k") is None


def test_corrupt_or_old_cache_file_is_ignored(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / bc.CACHE_FILE).write_text("{not json")
    assert bc.StageCache(str(build)).data["stages"] == {}
    (build / bc.CACHE_FILE).write_text('{"version": -1, "stages": {"s": {}}}')
    assert bc.StageCache(str(build)).data["stages"] == {}
