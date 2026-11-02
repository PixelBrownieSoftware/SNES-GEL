"""Packs a folder of WAV files into one Impulse Tracker module so smconv can BRR-compress them.

Each WAV is mixed down to mono, resampled to the target rate and peak-normalised. The module
has no music, only one sample per sound, so smconv's source index == the sample's position.
"""
import os
import re
import struct
import wave
from array import array


def load_mono(path):
    with wave.open(path, "rb") as w:
        ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(frames)
    if width == 1:
        data = [(b - 128) * 256 for b in raw]
    elif width == 2:
        data = array("h")
        data.frombytes(raw)
        data = list(data)
    elif width == 3:
        data = [int.from_bytes(raw[i:i + 3], "little", signed=True) >> 8 for i in range(0, len(raw), 3)]
    else:
        raise ValueError(f"{path}: unsupported sample width {width * 8} bits")
    if ch > 1:
        data = [sum(data[i:i + ch]) // ch for i in range(0, len(data) - ch + 1, ch)]
    return data, rate


def resample(data, src_rate, dst_rate):
    """Area-averaging resample: also acts as the low-pass filter when going down."""
    if src_rate <= dst_rate or not data:
        if src_rate == dst_rate or not data:
            return list(data)
        step = src_rate / dst_rate          # upsampling: linear interpolation
        out = []
        for i in range(int((len(data) - 1) / step) + 1):
            pos = i * step
            j = int(pos)
            frac = pos - j
            nxt = data[min(j + 1, len(data) - 1)]
            out.append(int(data[j] + (nxt - data[j]) * frac))
        return out
    step = src_rate / dst_rate
    csum = [0]
    for v in data:
        csum.append(csum[-1] + v)

    def at(x):
        i = int(x)
        if i >= len(data):
            return csum[-1]
        return csum[i] + data[i] * (x - i)

    out = []
    for i in range(int(len(data) / step)):
        a, b = i * step, (i + 1) * step
        out.append(int((at(b) - at(a)) / step))
    return out


def normalise(data, peak=0.95):
    m = max((abs(v) for v in data), default=0)
    if m == 0:
        return data
    k = 32767 * peak / m
    return [max(-32768, min(32767, int(v * k))) for v in data]


def sfx_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return "SFX_" + re.sub(r"\W+", "_", stem).strip("_").upper()


def write_it(path, samples, rate, title="GEL sfx", message=""):
    """samples: list of (name, int16 list). Writes a sample-mode IT with one empty pattern.

    `message` becomes the song message (SNESMOD reads its [[SNESMOD]] echo settings from it)."""
    n = len(samples)
    orders = bytes([0, 255])
    sample_ptr0 = 192 + len(orders)
    pat_ptr0 = sample_ptr0 + 4 * n
    hdr_base = pat_ptr0 + 4
    pattern = struct.pack("<HH4x", 64, 64) + bytes(64)
    pattern_off = hdr_base + 80 * n
    data_off = pattern_off + len(pattern)

    head = b"IMPM" + title.encode("ascii", "replace")[:25].ljust(26, b"\0")
    msg = message.replace("\n", "\r").encode("ascii", "replace") + b"\0" if message else b""
    msg_off = data_off + sum(2 * len(d) for _, d in samples)
    head += struct.pack("<HHHHHHHHH", 0, len(orders), 0, n, 1, 0x0217, 0x0200, 0x0008, 1 if msg else 0)
    head += struct.pack("<BBBBBBHII", 128, 48, 6, 125, 128, 0, len(msg), msg_off if msg else 0, 0)
    head += bytes([32] * 64) + bytes([64] * 64)
    assert len(head) == 192

    blob = head + orders
    blob += b"".join(struct.pack("<I", hdr_base + 80 * i) for i in range(n))
    blob += struct.pack("<I", pattern_off)
    pcm = b""
    for i, (name, data) in enumerate(samples):
        ptr = data_off + len(pcm)
        blob += b"IMPS" + os.path.basename(name).encode("ascii", "replace")[:11].ljust(12, b"\0")
        blob += struct.pack("<BBBB", 0, 64, 3, 64)
        blob += name.encode("ascii", "replace")[:25].ljust(26, b"\0")
        blob += struct.pack("<BBIIIIIIIBBBB", 1, 32, len(data), 0, 0, rate, 0, 0, ptr, 0, 0, 0, 0)
        pcm += array("h", data).tobytes()
    blob += pattern + pcm + msg
    with open(path, "wb") as f:
        f.write(blob)


def convert_folder(folder, out_it, rate):
    """Returns [(NAME, sample_index)] and writes out_it, or returns [] if there are no WAVs."""
    wavs = sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(".wav"))
    samples, seen, names = [], {}, []
    for p in wavs:
        data, src = load_mono(p)
        data = normalise(resample(data, src, rate))
        key = tuple(data)
        if key not in seen:
            seen[key] = len(samples)
            samples.append((sfx_name(p), data))
        names.append((sfx_name(p), seen[key], len(data)))
    if not samples:
        return []
    write_it(out_it, samples, rate)
    return names
