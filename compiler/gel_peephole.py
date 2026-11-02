"""Peephole optimiser for the 65816 assembly emitted by gel_codegen.

Works on the list of emitted lines. Anything it does not understand (labels,
directives, inline asm with unknown mnemonics) is treated as a barrier that
resets all tracked state, so rewrites only happen inside straight-line code.
"""
import re

INSTR = re.compile(r"^\s*([A-Za-z]{3})(?:\.([bwlBWL]))?(?:\s+([^;]*?))?\s*(;.*)?$")
HEX = re.compile(r"^\$([0-9A-Fa-f]{2,4})(,[Xx])?$")
IMM = re.compile(r"^#\$([0-9A-Fa-f]+)$")

# Leave A (both bytes) untouched.
KEEPS_A = {"STA", "STZ", "STX", "STY", "LDX", "LDY", "CPX", "CPY", "CLC", "SEC",
           "REP", "SEP", "NOP", "CMP", "BIT", "BEQ", "BNE", "BCC", "BCS", "BMI",
           "BPL", "BVC", "BVS", "BRA"}
# Known not to change the M/X flags or other state we track.
KEEPS_MODE = KEEPS_A | {"LDA", "AND", "ORA", "EOR", "ADC", "SBC", "INC", "DEC",
                        "ASL", "LSR", "ROL", "ROR", "TAX", "TAY", "TXA", "TYA",
                        "INX", "INY", "DEX", "DEY", "XBA", "TCD", "TDC", "TCS",
                        "TSC", "TXS", "TSX", "TXY", "TYX", "PHA", "PHX", "PHY",
                        "PLX", "PLY", "PHB", "PHK", "PHD", "WAI", "STP", "CLI",
                        "SEI", "CLD", "SED", "CLV"}
BRANCHES = {"BEQ", "BNE", "BCC", "BCS", "BMI", "BPL", "BVC", "BVS", "BRA"}
# Instructions that set N/Z (or fully redefine flags) without reading them.
FLAG_SETTERS = {"LDA", "LDX", "LDY", "CMP", "CPX", "CPY", "AND", "ORA", "EOR",
                "ADC", "SBC", "CLC", "SEC", "JSR", "JSL", "RTS", "RTL", "BIT",
                "INX", "INY", "DEX", "DEY", "TAX", "TAY", "TXA", "TYA"}
# Neither read nor write A or N/Z.
TRANSPARENT = {"REP", "SEP", "STZ", "STX", "STY"}


def parse(line):
    m = INSTR.match(line)
    if not m or line.lstrip().startswith(";"):
        return None
    op, suf, arg, _ = m.groups()
    return op.upper(), (suf or "").lower(), (arg or "").strip()


def is_comment(line):
    s = line.strip()
    return not s or s.startswith(";")


def find_label(lines, name):
    for k, l in enumerate(lines):
        if label_of(l) == name:
            return k
    return None


def next_instr(lines, i, skip):
    """First instruction on the path after line i whose mnemonic is not in `skip`.
    Labels are crossed and unconditional jumps are followed (the question is always
    about what this path does next). None means the path could not be resolved."""
    j = i + 1
    hops = 0
    while j < len(lines):
        l = lines[j]
        if is_comment(l) or label_of(l):
            j += 1
            continue
        p = parse(l)
        if p is None:
            return None
        if p[0] in skip:
            j += 1
            continue
        if p[0] in ("BRA", "JMP") and p[1] == "" and re.match(r"^[A-Za-z_]\w*$", p[2]) and hops < 8:
            t = find_label(lines, p[2])
            if t is None:
                return None
            j = t + 1
            hops += 1
            continue
        return p[0]
    return None


def flags_dead(lines, i):
    return next_instr(lines, i, TRANSPARENT | {"STA"}) in FLAG_SETTERS


def a_dead(lines, i):
    return next_instr(lines, i, TRANSPARENT) == "LDA"


def mask_effect(op, arg, m):
    im = IMM.match(arg)
    if not im:
        return None
    v = int(im.group(1), 16)
    if v & 0x20:
        return 16 if op == "REP" else 8
    return m


def redundant_pass(lines):
    """Drop mode switches / LDA immediates that change nothing."""
    out = []
    m = None
    lo = hi = None
    changed = False
    for idx, line in enumerate(lines):
        if is_comment(line):
            out.append(line)
            continue
        p = parse(line)
        if p is None:
            m = lo = hi = None
            out.append(line)
            continue
        op, suf, arg = p
        if op in ("REP", "SEP"):
            nm = mask_effect(op, arg, m)
            if nm is not None and nm == m and IMM.match(arg) and int(IMM.match(arg).group(1), 16) == 0x20:
                changed = True
                continue
            m = nm if nm is not None else None
            out.append(line)
            continue
        if op == "LDA":
            im = IMM.match(arg)
            if im:
                v = int(im.group(1), 16)
                if suf == "w":
                    nlo, nhi = v & 0xFF, v >> 8
                    if (lo, hi) == (nlo, nhi) and _flags_ok(lines, idx):
                        changed = True
                        continue
                    lo, hi = nlo, nhi
                elif m == 8 and suf == "":
                    if lo == v and _flags_ok(lines, idx):
                        changed = True
                        continue
                    lo = v
                else:
                    lo = hi = None
            else:
                lo = hi = None
            out.append(line)
            continue
        if op == "JSR":
            lo = hi = None
            m = 16          # GEL convention: callees return in 16-bit A
            out.append(line)
            continue
        if op not in KEEPS_A:
            lo = hi = None
        if op not in KEEPS_MODE:
            m = None
        out.append(line)
    return out, changed


def _flags_ok(lines, idx):
    return flags_dead(lines, idx)


def stz_pass(lines):
    """LDA #0 ; STA addr  ->  STZ addr, when A and flags are dead."""
    changed = False
    i = 0
    while i < len(lines):
        p = parse(lines[i]) if not is_comment(lines[i]) else None
        if p and p[0] == "LDA" and p[2] in ("#$00", "#$0000") and flags_dead(lines, i):
            j = i + 1
            while j < len(lines):
                if is_comment(lines[j]):
                    j += 1
                    continue
                q = parse(lines[j])
                if q and q[0] in ("REP", "SEP"):
                    j += 1
                    continue
                break
            q = parse(lines[j]) if j < len(lines) and not is_comment(lines[j]) else None
            if (q and q[0] == "STA" and q[1] in ("", "b", "w") and HEX.match(q[2])
                    and a_dead(lines, j)):
                indent = re.match(r"\s*", lines[j]).group(0)
                suffix = f".{q[1]}" if q[1] else ""
                lines[j] = f"{indent}STZ{suffix} {q[2]}"
                del lines[i]
                changed = True
                continue
        i += 1
    return lines, changed


def store_load_pass(lines):
    """STA x ; LDA x  ->  STA x (RAM only, nothing in between)."""
    changed = False
    i = 0
    while i < len(lines) - 1:
        if is_comment(lines[i]):
            i += 1
            continue
        p = parse(lines[i])
        if p and p[0] == "STA" and p[1] in ("", "b", "w") and HEX.match(p[2]) and int(HEX.match(p[2]).group(1), 16) < 0x2000:
            j = i + 1
            while j < len(lines) and is_comment(lines[j]):
                j += 1
            if j < len(lines):
                q = parse(lines[j])
                if q and q[0] == "LDA" and q[1] == p[1] and q[2].upper() == p[2].upper() and flags_dead(lines, j - 1):
                    del lines[j]
                    changed = True
                    continue
        i += 1
    return lines, changed


def tail_call_pass(lines):
    """JSR f ; RTS  ->  JMP f."""
    changed = False
    i = 0
    while i < len(lines) - 1:
        if is_comment(lines[i]):
            i += 1
            continue
        p = parse(lines[i])
        if p and p[0] == "JSR" and p[1] == "" and re.match(r"^[A-Za-z_]\w*$", p[2]):
            j = i + 1
            while j < len(lines) and is_comment(lines[j]):
                j += 1
            if j < len(lines):
                q = parse(lines[j])
                if q and q[0] == "RTS":
                    indent = re.match(r"\s*", lines[i]).group(0)
                    lines[i] = f"{indent}JMP {p[2]}"
                    del lines[j]
                    changed = True
        i += 1
    return lines, changed


def overridden_switch_pass(lines):
    """REP/SEP #$20 immediately followed by another #$20 switch is dead."""
    changed = False
    i = 0
    while i < len(lines):
        p = None if is_comment(lines[i]) else parse(lines[i])
        if p and p[0] in ("REP", "SEP") and p[2] == "#$20":
            j = i + 1
            while j < len(lines) and is_comment(lines[j]):
                j += 1
            q = parse(lines[j]) if j < len(lines) and not is_comment(lines[j]) else None
            if q and q[0] in ("REP", "SEP") and q[2] == "#$20":
                del lines[i]
                changed = True
                continue
        i += 1
    return lines, changed


def jump_to_next_pass(lines):
    """JMP/BRA L immediately followed by L: is a no-op."""
    changed = False
    i = 0
    while i < len(lines) - 1:
        p = None if is_comment(lines[i]) else parse(lines[i])
        if p and p[0] in ("JMP", "BRA") and re.match(r"^_?[A-Za-z]\w*$", p[2]):
            j = i + 1
            while j < len(lines) and (is_comment(lines[j]) or lines[j].strip().endswith(":")):
                if lines[j].strip() == p[2] + ":":
                    del lines[i]
                    changed = True
                    i -= 1
                    break
                j += 1
        i += 1
    return lines, changed


PRESERVE_A = {"STA", "STZ", "STX", "STY", "LDX", "LDY", "CPX", "CPY", "CLC", "SEC", "CMP",
              "BIT", "BEQ", "BNE", "BCC", "BCS", "BMI", "BPL", "BVC", "BVS", "BRA", "NOP",
              "TAX", "TAY", "TCD", "TCS", "PHA", "PHX", "PHY", "INX", "INY", "DEX", "DEY",
              "TXY", "TYX", "TXS", "TSX", "CLI", "SEI", "CLD", "SED", "CLV", "PHB", "PHD", "PHK"}
MEM_RMW = {"INC", "DEC", "ASL", "LSR", "ROL", "ROR", "TSB", "TRB"}
A_CHANGERS = {"ADC", "SBC", "AND", "ORA", "EOR", "TXA", "TYA", "TDC", "TSC", "PLA", "XBA"}
# Writes that can alias WRAM through hardware ports.
WRAM_PORTS = range(0x2180, 0x2184)


def plain_addr(arg):
    m = HEX.match(arg)
    if m and not m.group(2):
        return int(m.group(1), 16)
    return None


def reload_pass(lines, volatile=frozenset()):
    """Track which RAM word/byte A mirrors; drop `LDA addr` when A already holds it."""
    out = []
    m = None
    mirror = None            # (addr, width) A currently equals
    changed = False
    for idx, line in enumerate(lines):
        if is_comment(line):
            out.append(line)
            continue
        p = parse(line)
        if p is None:
            m = mirror = None
            out.append(line)
            continue
        op, suf, arg = p
        width = {16: 2, 8: 1}.get(m)

        def overlaps(addr, size):
            if mirror is None:
                return False
            ma, mw = mirror[0], 2 if mirror[1] == 16 else 1
            return addr < ma + mw and ma < addr + size

        if op in ("REP", "SEP"):
            im = IMM.match(arg)
            if im and int(im.group(1), 16) == 0x20:
                m = 16 if op == "REP" else 8
            elif im and int(im.group(1), 16) & 0x20:
                m = mirror = None
            out.append(line)
            continue
        if op == "LDA":
            addr = plain_addr(arg) if suf in ("", "w") else None
            if addr is not None and addr < 0x2000 and addr not in volatile and m:
                if mirror == (addr, m) and _flags_ok(lines, idx):
                    changed = True
                    continue
                mirror = (addr, m)
            else:
                mirror = None
            out.append(line)
            continue
        if op in ("STA", "STZ", "STX", "STY"):
            addr = plain_addr(arg) if suf in ("", "w") else None
            if addr is None:
                mirror = None  # indexed/long/indirect targets may alias tracked RAM
            elif addr in WRAM_PORTS or addr == 0x420B:
                mirror = None
            elif addr < 0x2000:
                if op == "STA" and m and addr not in volatile:
                    mirror = (addr, m)
                elif overlaps(addr, 2):
                    mirror = None
            out.append(line)
            continue
        if op in MEM_RMW:
            if arg.upper() in ("A", ""):
                mirror = None
            else:
                a = plain_addr(arg)
                if a is None or overlaps(a, 2):
                    mirror = None
            out.append(line)
            continue
        if op in A_CHANGERS:
            mirror = None
        elif op == "JSR":
            m = 16
            mirror = None
        elif op not in PRESERVE_A:
            m = mirror = None
        out.append(line)
    return out, changed


LABEL_RE = re.compile(r"^([A-Za-z_]\w*):\s*(?:;.*)?$")
INVERT = {"BEQ": "BNE", "BNE": "BEQ", "BCC": "BCS", "BCS": "BCC",
          "BMI": "BPL", "BPL": "BMI", "BVC": "BVS", "BVS": "BVC"}
COND_BRANCHES = set(INVERT)
TERMINATORS = {"RTS", "RTL", "RTI", "JMP", "JML", "BRA", "BRL", "STP"}


def label_of(line):
    m = LABEL_RE.match(line.strip())
    return m.group(1) if m else None


def split_items(arg):
    items, cur, quoted = [], "", False
    for ch in arg:
        if ch == '"':
            quoted = not quoted
        if ch == "," and not quoted:
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    items.append(cur.strip())
    return items


def size_upper_bound(line):
    """Never underestimates the encoded size, so range checks based on it are safe."""
    s = line.strip()
    if not s or s.startswith(";") or label_of(line):
        return 0
    s = re.sub(r"^[-+]+\s+", "", s)
    body = s.split(";")[0].strip()
    word = body.split(None, 1)
    d = word[0].lower()
    if d in ("db", "dw", "dl", "dd"):
        unit = {"db": 1, "dw": 2, "dl": 3, "dd": 4}[d]
        total = 0
        for it in split_items(word[1] if len(word) > 1 else ""):
            total += len(it) if it.startswith('"') else unit
        return total
    if not re.match(r"^[A-Za-z]{3}(\.[bwlBWL])?(\s|$)", body):
        return 8
    op = body[:3].upper()
    suf = body[4].lower() if len(body) > 4 and body[3] == "." else ""
    arg = word[1].strip() if len(word) > 1 else ""
    if op in COND_BRANCHES or op == "BRA" or op in ("REP", "SEP"):
        return 2
    if op in ("JSL", "JML") or suf == "l":
        return 4
    if not arg or arg.upper() == "A":
        return 1
    if op in ("JMP", "JSR", "PEA", "MVN", "MVP") or arg.startswith("#"):
        return 3
    if arg.startswith("["):
        return 4
    head = arg.split(",")[0].strip()
    hx = re.match(r"^\$([0-9A-Fa-f]+)$", head)
    if not hx:
        return 4
    digits = len(hx.group(1))
    if digits <= 2 and suf != "w":
        return 2
    return 3 if digits <= 4 else 4


def layout(lines):
    offs, labels, pos = [], {}, 0
    for i, l in enumerate(lines):
        offs.append(pos)
        lb = label_of(l)
        if lb:
            labels[lb] = pos
        pos += size_upper_bound(l)
    return offs, labels


REF_TOKEN_RE = re.compile(r"(?<![\w$])\w+")


def reference_counts(lines):
    """name -> number of lines that mention it (a label's own definition line doesn't count).
    One pass over all lines; same matching rule as the per-name regex it replaces."""
    counts = {}
    for l in lines:
        own = label_of(l)
        for tok in set(REF_TOKEN_RE.findall(l.split(";")[0])):
            if tok != own:
                counts[tok] = counts.get(tok, 0) + 1
    return counts


def next_code(lines, i):
    j = i + 1
    while j < len(lines) and is_comment(lines[j]):
        j += 1
    return j


def relax_pass(lines):
    """Bcc skip / JMP T / skip:  ->  B!cc T   and   JMP T -> BRA T when T is in range."""
    offs, labels = layout(lines)
    changed = False
    drop, replace = set(), {}
    reach = 120

    def in_range(i, target):
        if target not in labels:
            return False
        d = labels[target] - (offs[i] + 2)
        return -reach <= d <= reach

    i = 0
    while i < len(lines):
        if is_comment(lines[i]) or i in drop:
            i += 1
            continue
        p = parse(lines[i])
        if p and p[0] in COND_BRANCHES and re.match(r"^[A-Za-z_]\w*$", p[2]):
            j = next_code(lines, i)
            k = next_code(lines, j) if j < len(lines) else len(lines)
            if j < len(lines) and k < len(lines):
                q = parse(lines[j])
                if (q and q[0] == "JMP" and q[1] == "" and re.match(r"^[A-Za-z_]\w*$", q[2])
                        and label_of(lines[k]) == p[2] and in_range(i, q[2])):
                    indent = re.match(r"\s*", lines[i]).group(0)
                    replace[i] = f"{indent}{INVERT[p[0]]} {q[2]}"
                    drop.add(j)
                    changed = True
                    i = k
                    continue
        elif p and p[0] == "JMP" and p[1] == "" and re.match(r"^[A-Za-z_]\w*$", p[2]) and in_range(i, p[2]):
            indent = re.match(r"\s*", lines[i]).group(0)
            replace[i] = f"{indent}BRA {p[2]}"
            changed = True
        i += 1
    if not changed:
        return lines, False
    out = [replace.get(i, l) for i, l in enumerate(lines) if i not in drop]
    # Drop the now-unused skip labels.
    refs = reference_counts(out)
    keep = []
    for l in out:
        lb = label_of(l)
        if lb and re.match(r"^_L\d+$", lb) and refs.get(lb, 0) == 0:
            continue
        keep.append(l)
    return keep, True


def zero_compare_pass(lines):
    """`LDA/AND/... ; CMP #0 ; BEQ/BNE/BMI/BPL` -> the load already set N and Z."""
    setters = {"LDA", "AND", "ORA", "EOR", "TXA", "TYA", "PLA", "TDC", "TSC"}
    consumers = {"BEQ", "BNE", "BMI", "BPL"}
    changed = False
    out = []
    for i, l in enumerate(lines):
        p = None if is_comment(l) else parse(l)
        if p and p[0] == "CMP" and p[2] in ("#$0000", "#$00"):
            k = len(out) - 1
            while k >= 0 and is_comment(out[k]):
                k -= 1
            prev = parse(out[k]) if k >= 0 and not is_comment(out[k]) else None
            j = next_code(lines, i)
            nxt = parse(lines[j]) if j < len(lines) and not is_comment(lines[j]) else None
            same_width = prev and p[1] == "w" and prev[1] in ("", "w", "l") and not (
                prev[2].startswith("#") and prev[1] != "w")
            uses_cv = False
            q = j
            while q < len(lines):
                if is_comment(lines[q]):
                    q += 1
                    continue
                pq = parse(lines[q])
                if not pq or pq[0] not in COND_BRANCHES:
                    break
                if pq[0] in ("BCS", "BCC", "BVS", "BVC"):
                    uses_cv = True
                q += 1
            if prev and prev[0] in setters and nxt and nxt[0] in consumers and same_width and not uses_cv:
                changed = True
                continue
        out.append(l)
    return out, changed


def unreachable_pass(lines):
    """Delete instructions that follow RTS/JMP/BRA with no label in between."""
    out = []
    dead = False
    changed = False
    for l in lines:
        if dead:
            if is_comment(l):
                out.append(l)
                continue
            if parse(l) is not None:
                changed = True
                continue
            dead = False
        out.append(l)
        p = None if is_comment(l) else parse(l)
        if p and p[0] in TERMINATORS:
            dead = True
    return out, changed


A_IMM_OPS = {"LDA", "ADC", "SBC", "AND", "ORA", "EOR", "CMP", "BIT"}
M_RESET_OPS = {"PLP", "RTI", "XCE"}


def analyze_modes(lines):
    """Forward dataflow of the accumulator width (16, 8, or "U" when paths disagree).
    Returns (parsed instructions, state before each line, label->line map)."""
    n = len(lines)
    instr = []
    labels = {}
    jsr_targets = set()
    fn_entries = set()
    for i, l in enumerate(lines):
        s = re.sub(r"^[-+]+\s+", "", l) if re.match(r"^[-+]+\s", l) else l
        instr.append(None if is_comment(s) else parse(s))
        lb = label_of(l)
        if lb:
            labels[lb] = i
            if i > 0 and (lines[i - 1].startswith("; --- Function:")
                          or lines[i - 1].startswith("; --- Global initialisers")):
                fn_entries.add(lb)
    for p in instr:
        if p and p[0] == "JSR":
            jsr_targets.add(p[2])
    state = [None] * n

    def join(a, b):
        if a is None:
            return b
        if b is None or a == b:
            return a
        return "U"

    work = []

    def push(i, m):
        if i >= n:
            return
        new = join(state[i], m)
        if new != state[i]:
            state[i] = new
            work.append(i)

    for lb, i in labels.items():
        if lb in jsr_targets or lb in fn_entries:
            push(i, 16 if lb in fn_entries else "U")
    for i, l in enumerate(lines):
        if re.match(r"^[-+]+\s", l):
            push(i, "U")
    # Labels used as data (address-of, tables, vectors) can be reached in any mode.
    flow_ops = COND_BRANCHES | {"BRA", "JMP", "JML", "JSR"}
    for i, l in enumerate(lines):
        if label_of(l) or is_comment(l):
            continue
        p = instr[i]
        code = l.split(";")[0]
        if p and p[0] in flow_ops:
            continue
        for tok in re.findall(r"[A-Za-z_]\w*", code):
            if tok in labels:
                push(labels[tok], "U")
    while work:
        i = work.pop()
        m = state[i]
        p = instr[i]
        nxt = i + 1
        if p is None:
            push(nxt, m)
            continue
        op, suf, arg = p
        if op in ("REP", "SEP"):
            im = IMM.match(arg)
            if im and int(im.group(1), 16) & 0x20:
                m = 16 if op == "REP" else 8
            elif not im:
                m = "U"
        elif op in M_RESET_OPS:
            m = "U"
        elif op == "JSR":
            m = 16          # GEL convention (verified separately at every RTS)
        if op in COND_BRANCHES or op == "BRA":
            if arg in labels:
                push(labels[arg], m)
            if op == "BRA":
                continue
        elif op in ("JMP", "JML"):
            if arg in labels:
                push(labels[arg], m)
            continue
        elif op in ("RTS", "RTL", "RTI", "STP"):
            continue
        push(nxt, m)
    return instr, state, labels


def mode_elim_pass(lines):
    """Remove REP/SEP #$20 when every path reaching it already has that mode."""
    instr, state, _ = analyze_modes(lines)
    drop = set()
    for i, p in enumerate(instr):
        if p and p[0] in ("REP", "SEP") and p[2] == "#$20":
            if state[i] == (16 if p[0] == "REP" else 8):
                drop.add(i)
    if not drop:
        return lines, False
    return [l for i, l in enumerate(lines) if i not in drop], True


def verify_modes(lines):
    """Returns human-readable problems for instructions whose immediate size cannot match
    the CPU's M flag, and for calls/returns that break the 16-bit calling convention."""
    instr, state, _ = analyze_modes(lines)
    problems = []
    for i, p in enumerate(instr):
        m = state[i]
        if not p or m in (None, "U"):
            continue
        op, suf, arg = p
        if op in A_IMM_OPS and arg.startswith("#"):
            want = 16 if suf == "w" else 8
            if m != want:
                problems.append(f"line {i + 1}: '{lines[i].strip()}' needs M={want} but CPU is in {m}-bit mode")
        elif op == "JSR" and m != 16:
            problems.append(f"line {i + 1}: '{lines[i].strip()}' called in {m}-bit mode (GEL expects 16)")
        elif op == "RTS" and m != 16:
            problems.append(f"line {i + 1}: RTS in {m}-bit mode (GEL expects 16)")
    return problems


def signed_zero_pass(lines):
    """Signed compare against 0: `SEC ; SBC.w #0 ; [BEQ x] ; BVC f` never overflows, so the
    preceding load's N/Z flags are already the answer."""
    setters = {"LDA", "AND", "ORA", "EOR", "TXA", "TYA", "PLA"}
    changed = False
    out = list(lines)
    i = 0
    while i < len(out):
        p = None if is_comment(out[i]) else parse(out[i])
        if p and p[0] == "SBC" and p[1] == "w" and p[2] == "#$0000":
            k = len(out[:i]) - 1
            while k >= 0 and is_comment(out[k]):
                k -= 1
            sec = parse(out[k]) if k >= 0 and not is_comment(out[k]) else None
            k2 = k - 1
            while k2 >= 0 and is_comment(out[k2]):
                k2 -= 1
            prev = parse(out[k2]) if k2 >= 0 and not is_comment(out[k2]) else None
            j = next_code(out, i)
            nxt = parse(out[j]) if j < len(out) and not is_comment(out[j]) else None
            vj = j
            if nxt and nxt[0] == "BEQ":
                vj = next_code(out, j)
            vb = parse(out[vj]) if vj < len(out) and not is_comment(out[vj]) else None
            if (sec and sec[0] == "SEC" and prev and prev[0] in setters
                    and prev[1] in ("", "w", "l") and not (prev[2].startswith("#") and prev[1] != "w")
                    and vb and vb[0] == "BVC"):
                indent = re.match(r"\s*", out[vj]).group(0)
                out[vj] = f"{indent}BRA {vb[2]}"
                del out[i]
                del out[k]
                changed = True
                i = k
                continue
        i += 1
    return out, changed


def thread_pass(lines):
    """A branch whose target is just another unconditional jump goes straight to the end."""
    changed = False
    out = list(lines)
    offs, off_labels = layout(out)
    labels = {}
    for i, l in enumerate(out):
        lb = label_of(l)
        if lb:
            labels[lb] = i
    for i, l in enumerate(out):
        p = None if is_comment(l) else parse(l)
        if not p or p[1] != "" or not re.match(r"^[A-Za-z_]\w*$", p[2] or ""):
            continue
        if p[0] not in COND_BRANCHES and p[0] not in ("BRA", "JMP"):
            continue
        t, seen = p[2], 0
        while t in labels and seen < 6:
            j = labels[t] + 1
            while j < len(out) and (is_comment(out[j]) or label_of(out[j])):
                j += 1
            q = parse(out[j]) if j < len(out) and not is_comment(out[j]) else None
            if q and q[0] in ("BRA", "JMP") and q[1] == "" and re.match(r"^[A-Za-z_]\w*$", q[2]) and q[2] != t:
                t = q[2]
                seen += 1
            else:
                break
        if t != p[2]:
            if p[0] != "JMP":
                if t not in off_labels or not -120 <= off_labels[t] - (offs[i] + 2) <= 120:
                    continue
            indent = re.match(r"\s*", l).group(0)
            out[i] = f"{indent}{p[0]} {t}"
            changed = True
    return out, changed


def optimize(lines, volatile=frozenset()):
    lines = list(lines)
    for _ in range(10):
        any_change = False
        for fn in (redundant_pass, reload_pass, stz_pass, overridden_switch_pass, store_load_pass,
                   tail_call_pass, thread_pass, jump_to_next_pass, zero_compare_pass, signed_zero_pass,
                   unreachable_pass, relax_pass, mode_elim_pass):
            lines, c = fn(lines, volatile) if fn is reload_pass else fn(lines)
            any_change |= c
        if not any_change:
            break
    return lines
