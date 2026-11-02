import random
import re

import gel_peephole as pp


def old_reference_count(lines, name):
    """The original per-name implementation, kept as the reference for the fast version."""
    pat = re.compile(rf"(?<![\w$]){re.escape(name)}(?!\w)")
    n = 0
    for l in lines:
        if pp.label_of(l) == name:
            continue
        if pat.search(l.split(";")[0]):
            n += 1
    return n


SAMPLE = [
    "_L1:",
    "    LDA $10",
    "    BEQ _L2",
    "    JMP _L1 ; loop _L9",
    "_L2:",
    "    LDA #_L3",
    "    STA $0400,X",
    "_L3:",
    "_L10:",
    "    BRA _L10",
    "    JSR _L33",
    "    LDA $L1",
    "foo_L1:",
    "    JMP foo_L1",
]


def test_reference_counts_match_the_per_name_version():
    counts = pp.reference_counts(SAMPLE)
    for name in ("_L1", "_L2", "_L3", "_L10", "_L33", "_L9", "foo_L1", "L1"):
        assert counts.get(name, 0) == old_reference_count(SAMPLE, name), name


def test_reference_counts_match_on_random_programs():
    rng = random.Random(7)
    names = [f"_L{i}" for i in range(12)] + ["main"]
    for _ in range(200):
        lines = []
        for _ in range(rng.randint(5, 40)):
            kind = rng.random()
            n = rng.choice(names)
            if kind < 0.25 and n.startswith("_L"):
                lines.append(f"{n}:")
            elif kind < 0.5:
                lines.append(f"    JMP {n}")
            elif kind < 0.7:
                lines.append(f"    LDA ${n[-1:]}FF,X ; see {rng.choice(names)}")
            else:
                lines.append(f"    LDA #${rng.randint(0, 255):02X}")
        counts = pp.reference_counts(lines)
        for n in names:
            assert counts.get(n, 0) == old_reference_count(lines, n), (n, lines)


def test_relax_pass_converts_skip_jump_and_drops_label():
    lines = ["    LDA $10", "    BEQ _L1", "    JMP target", "_L1:", "    RTS", "target:", "    RTS"]
    out, changed = pp.relax_pass(lines)
    assert changed
    assert "    BNE target" in out
    assert "_L1:" not in out
