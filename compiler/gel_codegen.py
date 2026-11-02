# ==============================================================================
# gel_codegen.py - SNES-GEL -> 65C816 (asar) code generator
#
# Conventions
#   * Expressions are evaluated into the 16-bit accumulator (8-bit values are
#     zero/sign extended). Every expression leaves the CPU in 16-bit A mode.
#   * X/Y are always 16-bit. X is only touched by Table.create() (it returns the
#     slot in X as well as in A, so `reg(X)` works); table accesses use Y.
#   * Variables, parameters and locals live in static RAM slots (no recursion).
#   * Memory map (WRAM): $00-$0F scratch, $10-$1FF variables, $200-$41F OAM
#     shadow (declared in snes.gel), $600-$1EFF struct tables, $1F00+ stack.
# ==============================================================================
from lark import Tree, Token
import re
import sys
import gel_peephole as peephole

SIZES = {"u8": 1, "i8": 1, "u16": 2, "i16": 2, "f16": 2}
SIGNED = {"i8", "i16", "f16"}

DP_START, DP_END = 0x0010, 0x01FF
SOUND_DP = (0x00E8, 0x00FF)   # direct page reserved for the sound driver (stdlib/snesmod.asm, sound.gel)
TABLE_START, TABLE_END = 0x0600, 0x1EFF
STACK_TOP = 0x1FFF
# Hot-reload staging in WRAM (outside the range the boot code clears): the Lua hook copies
# direct-page bytes to STAGE_BASE+addr and writes STAGE_MAGIC at STAGE_MAGIC_ADDR.
STAGE_BASE = 0x7E3000
STAGE_MAGIC_ADDR = 0x7E2FFE
STAGE_MAGIC = 0xA55A

SCRATCH = "$00"       # binary-op operand scratch ($00-$01)
SCRATCH2 = "$02"      # helper scratch ($02-$03)

FIXED_OPS = {
    "logic_or": "||", "logic_and": "&&",
    "bitwise_or": "|", "bitwise_xor": "^", "bitwise_and": "&",
}
OP_RULES = {"equality", "relational", "shift", "additive", "multiplicative"}
CHAIN_RULES = {"bitwise_or", "bitwise_xor", "bitwise_and", "shift", "additive", "multiplicative"}
ALU = {"+": "ADC", "-": "SBC", "&": "AND", "|": "ORA", "^": "EOR"}


class CompileError(Exception):
    pass


class Var:
    def __init__(self, name, vtype, addr, mmio=False):
        self.name, self.type, self.addr, self.mmio = name, vtype, addr, mmio

    @property
    def size(self):
        return SIZES[self.type]


class Field:
    def __init__(self, ftype, addr, step):
        self.type, self.addr, self.step = ftype, addr, step

    @property
    def size(self):
        return SIZES[self.type]


class Table:
    def __init__(self, name, size, fields, mask):
        self.name, self.size, self.fields, self.mask = name, size, fields, mask


def addr_str(addr):
    return f"${addr:02X}" if addr < 0x100 else f"${addr:04X}"


def parse_number(text):
    text = str(text)
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def combine(a, b):
    if "f16" in (a, b):
        return "f16"
    if a in SIGNED or b in SIGNED:
        return "i16"
    return "u16"


def fold(op, a, b):
    (x, tx), (y, ty) = a, b
    if op in ("<<", ">>"):
        return (x << y if op == "<<" else x >> y), tx
    t = combine(tx, ty)
    if op == "+":
        v = x + y
    elif op == "-":
        v = x - y
    elif op == "*":
        v = (x * y) >> 8 if tx == ty == "f16" else x * y
    elif op == "/":
        if y == 0:
            raise CompileError("Division by zero in constant expression")
        v = int(((x << 8) / y) if tx == ty == "f16" else x / y)
    elif op == "&":
        v = x & y
    elif op == "|":
        v = x | y
    elif op == "^":
        v = x ^ y
    else:
        return None
    if v < 0 and t == "u16":
        t = "i16"
    return v, t


class Compiler:
    def __init__(self, prev_layout=None, hot_reload=True, header_cfg=None):
        self.header_cfg = header_cfg
        self.out = []
        self.vars = {}
        self.tables = {}
        self.enums = {}
        self.funcs = {}
        self.func_ast = {}
        self.inline_ok = {}
        self.inlined = set()
        self.inline_end = None
        self.inline_stack = []
        self.loop_depth = 0
        self.chunks = {}
        self.inline_enabled = True
        self.externs = set()
        self.inits = []
        self.dp_next = DP_START
        self.table_next = TABLE_START
        self.explicit_ranges = []
        self.scope = {}
        self.fn_name = None
        self.m = None            # known accumulator width (16/8) or None
        self.label_id = 0
        self.uses_mul = False
        self.uses_fmul = False
        self.strings = []
        self.cur_node = None
        self.prev_layout = prev_layout or {}
        self.hot_reload = hot_reload
        self.reserved = []
        self.persist_plan = {}
        self.persist_vars = []
        self.optimize = True
    def error(self, msg, node=None):
        node = node or self.cur_node
        line = None
        try:
            if node is not None and not node.meta.empty:
                line = node.meta.line
        except AttributeError:
            pass
        where = f" in '{self.fn_name}'" if self.fn_name else ""
        at = f" (line {line} of the preprocessed source{where})" if line else where
        raise CompileError(f"{msg}{at}")

    def emit(self, text):
        self.out.append("    " + text)

    def raw(self, text):
        self.out.append(text)

    def new_label(self):
        self.label_id += 1
        return f"_L{self.label_id}"

    def label(self, name, keep=False):
        self.raw(f"{name}:")
        if not keep:
            self.m = None

    def want_m(self, bits):
        if self.m != bits:
            self.emit("REP #$20" if bits == 16 else "SEP #$20")
            self.m = bits

    def alloc_dp(self, size, what):
        addr = self.dp_next
        for lo, hi in sorted(self.reserved + [SOUND_DP]):
            if addr <= hi and addr + size - 1 >= lo:
                addr = hi + 1
        if addr + size - 1 > DP_END:
            self.error(f"Out of variable RAM allocating '{what}'")
        self.dp_next = addr + size
        return addr

    def plan_persist(self, tree):
        """Give every 'persist' global a stable address so hot-reloads can carry its value over."""
        decls = []
        for item in tree.children:
            if item.data == "global_decl" and isinstance(item.children[0], Tree) \
                    and item.children[0].data == "st_persist":
                kids = item.children[1:]
                decls.append((str(kids[1]), str(kids[0]), any(isinstance(k, Token) for k in kids[2:])))
        taken, plan = [], {}
        for name, vtype, explicit in decls:
            if explicit:
                self.error(f"persist variable '{name}' cannot have a fixed address")
            self.check_type(vtype)
            old = self.prev_layout.get(name)
            if old and old[0] == vtype:
                lo, hi = old[1], old[1] + SIZES[vtype] - 1
                if DP_START <= lo and hi <= DP_END and not any(lo <= h and a <= hi for a, h in taken) \
                        and not (lo <= SOUND_DP[1] and SOUND_DP[0] <= hi):
                    plan[name] = (lo, True)
                    taken.append((lo, hi))
        self.reserved = list(taken)
        for name, vtype, _ in decls:
            if name in plan:
                continue
            addr = self.alloc_dp(SIZES[vtype], name)
            plan[name] = (addr, False)
            self.reserved.append((addr, addr + SIZES[vtype] - 1))
        self.dp_next = DP_START
        self.persist_plan = plan

    def lookup(self, name):
        return self.scope.get(name) or self.vars.get(name)

    def get_table(self, name):
        t = self.tables.get(name)
        if t is None:
            self.error(f"Unknown struct_table '{name}'")
        return t

    def get_field(self, table, fname):
        f = table.fields.get(fname)
        if f is None:
            self.error(f"Table '{table.name}' has no field '{fname}'")
        return f

    @staticmethod
    def check_type(t):
        if t not in SIZES:
            raise CompileError(f"Type '{t}' is not supported yet")

    # ------------------------------------------------------- pass 1: declarations
    def declare(self, item):
        self.cur_node = item
        d = item.data
        if d == "global_decl":
            self.decl_global(item)
        elif d == "struct_table_decl":
            self.decl_table(item)
        elif d == "enum_decl":
            self.decl_enum(item)
        elif d == "extern_decl":
            self.externs.add(str(item.children[0]))
        elif d == "function_decl":
            self.decl_function(item)

    def decl_global(self, item):
        kids = list(item.children)
        storage = None
        if isinstance(kids[0], Tree):
            storage = kids.pop(0).data
        vtype, name = str(kids[0]), str(kids[1])
        self.check_type(vtype)
        addr, init = None, None
        for k in kids[2:]:
            if isinstance(k, Token):
                addr = parse_number(k)
            else:
                init = k
        if name in self.vars or name in self.tables or name in self.enums:
            self.error(f"Duplicate symbol '{name}'")
        if storage == "st_mmio":
            if addr is None:
                self.error(f"mmio '{name}' needs an address (@ 0x....)")
            self.vars[name] = Var(name, vtype, addr, mmio=True)
            return
        if addr is None:
            if storage == "st_persist":
                addr = self.persist_plan[name][0]
            else:
                addr = self.alloc_dp(SIZES[vtype], name)
        self.vars[name] = Var(name, vtype, addr)
        if storage == "st_persist":
            self.persist_vars.append((self.vars[name], self.persist_plan[name][1]))
        if init is not None:
            self.inits.append((self.vars[name], init))

    def decl_table(self, item):
        kids = list(item.children)
        name, size = str(kids[0]), parse_number(kids[1])
        addr, interleaved, fields = None, False, []
        for k in kids[2:]:
            if isinstance(k, Token):
                addr = parse_number(k)
            elif k.data == "interleaved":
                interleaved = True
            elif k.data == "table_field":
                self.check_type(str(k.children[0]))
                fields.append((str(k.children[0]), str(k.children[1])))
        if name in self.vars or name in self.tables or name in self.enums:
            self.error(f"Duplicate symbol '{name}'")
        elem_bytes = [SIZES[t] for t, _ in fields]
        total = size * sum(elem_bytes)
        if addr is None:
            addr = self.table_next
            if addr + total - 1 > TABLE_END:
                self.error(f"Out of table RAM allocating '{name}'")
            self.table_next += total
        else:
            self.explicit_ranges.append((addr, addr + total - 1, name))
        stride = sum(elem_bytes)
        tfields, offset = {}, 0
        for (ftype, fname), eb in zip(fields, elem_bytes):
            if interleaved:
                tfields[fname] = Field(ftype, addr + offset, stride)
                offset += eb
            else:
                tfields[fname] = Field(ftype, addr + offset, eb)
                offset += size * eb
        # Tables of up to 16 slots are pools: a 16-bit bitmask tracks live slots.
        mask = self.alloc_dp(2, f"{name}.mask") if size <= 16 else None
        self.tables[name] = Table(name, size, tfields, mask)
        for lo, hi, other in self.explicit_ranges:
            if other != name and lo <= addr + total - 1 and addr <= hi:
                self.error(f"Table '{name}' overlaps explicitly placed table '{other}'")

    def decl_enum(self, item):
        value = 0
        for member in item.children[1:]:
            mname = str(member.children[0])
            if len(member.children) > 1:
                value = parse_number(member.children[1])
            self.enums[mname] = value
            value += 1

    def decl_function(self, item):
        kids = [k for k in item.children if not (isinstance(k, Tree) and k.data == "static_kw")]
        name = str(kids[0])
        params = []
        for k in kids[1:-1]:
            if isinstance(k, Tree) and k.data == "param_list":
                for p in k.children:
                    ptype, pname = str(p.children[0]), str(p.children[1])
                    self.check_type(ptype)
                    if len(p.children) > 2:
                        self.error(f"reg() parameter hints are not supported (param '{pname}')", p)
                    params.append(Var(pname, ptype, self.alloc_dp(SIZES[ptype], f"{name}.{pname}")))
        if name in self.funcs:
            self.error(f"Duplicate function '{name}'")
        self.funcs[name] = params
        self.func_ast[name] = kids[-1]

    INLINE_MAX_NODES = 25       # always inline bodies up to this size
    INLINE_LOOP_NODES = 60      # ...or up to this size when the call site is inside a loop
    INLINE_STMTS = {"assign_stmt", "if_stmt", "return_stmt", "expr_stmt", "while_stmt"}
    BUILTIN_CALLS = {"bit", "bit_set", "bit_clear", "bit_test", "peek8", "peek8l"}

    def can_inline(self, name):
        """Small functions (no locals or asm) are expanded in place. They may call other
        functions, which are in turn inlined or called normally; recursion never inlines."""
        body = self.func_ast.get(name)
        ok = (body is not None and name not in ("init_game",)
              and name not in self.inline_stack and len(self.inline_stack) < 3)
        if ok:
            nodes = list(body.iter_subtrees())
            limit = self.INLINE_LOOP_NODES if self.loop_depth else self.INLINE_MAX_NODES
            ok = len(nodes) <= limit
            for t in nodes:
                if t.data == "block":
                    ok = ok and all(isinstance(s, Tree) and s.data in self.INLINE_STMTS for s in t.children)
                elif t.data == "func_call":
                    callee = str(t.children[0])
                    ok = ok and (callee in self.BUILTIN_CALLS or (callee in self.funcs and callee != name))
                elif t.data in ("reg_read", "table_create"):
                    ok = False
        self.inline_ok[name] = ok
        return ok

    def inline_call(self, name):
        end = self.new_label()
        saved = (self.scope, self.fn_name, self.inline_end, self.cur_node)
        self.scope = {p.name: p for p in self.funcs[name]}
        self.fn_name = name
        self.inline_end = end
        self.inlined.add(name)
        self.inline_stack.append(name)
        self.raw(f"    ; inline {name}")
        self.block(self.func_ast[name])
        self.inline_stack.pop()
        self.label(end)
        self.scope, self.fn_name, self.inline_end, self.cur_node = saved

    # -------------------------------------------------------------- constants
    def const(self, n):
        """Return (value, type) when the expression is a compile-time constant."""
        if not isinstance(n, Tree):
            return None
        d = n.data
        if d == "num":
            return parse_number(n.children[0]), "u16"
        if d == "float_num":
            return round(float(n.children[0]) * 256), "f16"
        if d == "char_num":
            return ord(str(n.children[0])[1]), "u8"
        if d == "var_ref":
            name = str(n.children[0])
            if self.lookup(name) is None and name in self.enums:
                return self.enums[name], "u16"
            return None
        if d == "func_call" and str(n.children[0]) == "bit":
            args = n.children[1].children if len(n.children) > 1 else []
            c = self.const(args[0]) if len(args) == 1 else None
            if c is not None and 0 <= c[0] <= 15:
                return 1 << c[0], "u16"
            return None
        if d in ("neg", "bit_not"):
            c = self.const(n.children[0])
            if c is None:
                return None
            if d == "neg":
                return -c[0], ("f16" if c[1] == "f16" else "i16")
            return ~c[0] & 0xFFFF, c[1]
        if d in FIXED_OPS and d in CHAIN_RULES:
            op = FIXED_OPS[d]
            acc = self.const(n.children[0])
            for k in n.children[1:]:
                c = self.const(k)
                if acc is None or c is None:
                    return None
                acc = fold(op, acc, c)
            return acc
        if d in CHAIN_RULES:
            acc = self.const(n.children[0])
            for i in range(1, len(n.children), 2):
                c = self.const(n.children[i + 1])
                if acc is None or c is None:
                    return None
                acc = fold(str(n.children[i].children[0]), acc, c)
            return acc
        return None

    @staticmethod
    def contains_call(node):
        return any(t.data == "func_call" for t in node.iter_subtrees())

    # ------------------------------------------------------------ var access
    def load_var(self, v):
        if v.size == 2:
            self.want_m(16)
            self.emit(f"LDA {addr_str(v.addr)}")
            return
        if v.mmio:
            self.want_m(8)
            self.emit(f"LDA {addr_str(v.addr)}")
            self.want_m(16)
        else:
            self.want_m(16)
            self.emit(f"LDA {addr_str(v.addr)}")
        self.emit("AND.w #$00FF")
        if v.type == "i8":
            self.sign_extend8()

    def sign_extend8(self):
        skip = self.new_label()
        self.emit("CMP.w #$0080")
        self.emit(f"BCC {skip}")
        self.emit("ORA.w #$FF00")
        self.label(skip, keep=True)

    def store_var(self, v):
        self.want_m(v.size * 8)
        self.emit(f"STA {addr_str(v.addr)}")

    def index_to_y(self, field, idx):
        c = self.const(idx)
        if c is not None:
            self.emit(f"LDY.w #${(c[0] * field.step) & 0xFFFF:04X}")
        else:
            self.expr(idx)
            self.mul_const(field.step)
            self.emit("TAY")

    def mul_const(self, k):
        k &= 0xFFFF
        self.want_m(16)
        if k == 0:
            self.emit("LDA.w #$0000")
        elif k > 1:
            tail = bin(k)[3:]
            if "1" in tail:
                self.emit(f"STA {SCRATCH}")
            for bit in tail:
                self.emit("ASL A")
                if bit == "1":
                    self.emit("CLC")
                    self.emit(f"ADC {SCRATCH}")

    Y_SAFE_NODES = {"num", "float_num", "char_num", "var_ref", "additive", "add_op",
                    "bitwise_or", "bitwise_xor", "bitwise_and", "bit_not", "neg"}

    def y_safe(self, n):
        """True when evaluating n can't clobber Y (no table reads, calls, shifts or multiplies)."""
        if self.const(n) is not None:
            return True
        return all(t.data in self.Y_SAFE_NODES for t in n.iter_subtrees())

    def assign_to(self, target, producer, rhs=None, compound_op=None):
        if target.data == "var_ref":
            name = str(target.children[0])
            v = self.lookup(name)
            if v is None:
                self.error(f"Cannot assign to undeclared variable '{name}'", target)
            producer()
            self.store_var(v)
            return
        table = self.get_table(str(target.children[0]))
        field = self.get_field(table, str(target.children[2]))
        idx = target.children[1]
        if rhs is not None and self.y_safe(rhs):
            # The value never touches Y, so the slot offset is computed once and kept in Y
            # (no PHA/PLA round trip, and a compound assignment reuses it for its read).
            self.index_to_y(field, idx)
            if compound_op is not None:
                producer = lambda: self.binop(compound_op, self.load_field_y(field), rhs)
            producer()
            self.want_m(field.size * 8)
            self.emit(f"STA ${field.addr:04X},Y")
            return
        if self.const(idx) is not None:
            producer()
            self.index_to_y(field, idx)
        else:
            producer()
            self.want_m(16)
            self.emit("PHA")
            self.index_to_y(field, idx)
            self.want_m(16)
            self.emit("PLA")
        self.want_m(field.size * 8)
        self.emit(f"STA ${field.addr:04X},Y")

    def target_type(self, target):
        if target.data == "var_ref":
            v = self.lookup(str(target.children[0]))
            if v is None:
                self.error(f"Undeclared variable '{target.children[0]}'", target)
            return v.type
        table = self.get_table(str(target.children[0]))
        return self.get_field(table, str(target.children[2])).type

    # ------------------------------------------------------------ expressions
    def expr(self, n):
        self.cur_node = n
        c = self.const(n)
        if c is not None:
            self.want_m(16)
            self.emit(f"LDA.w #${c[0] & 0xFFFF:04X}")
            return c[1]
        handler = getattr(self, "x_" + n.data, None)
        if handler is None:
            self.error(f"Unsupported expression '{n.data}'", n)
        ty = handler(n)
        self.want_m(16)
        return ty

    def x_var_ref(self, n):
        name = str(n.children[0])
        v = self.lookup(name)
        if v is not None:
            self.load_var(v)
            return v.type
        if name in ("X", "Y", "A"):
            return self.read_register(name)
        if name in self.externs:
            self.want_m(16)
            self.emit(f"LDA.w #{name}")
            return "u16"
        if name in self.tables:
            self.error(f"'{name}' is a table; index it as {name}[i].field", n)
        self.error(f"Undeclared identifier '{name}'", n)

    def read_register(self, reg):
        self.want_m(16)
        if reg == "X":
            self.emit("TXA")
        elif reg == "Y":
            self.emit("TYA")
        return "u16"

    def x_reg_read(self, n):
        reg = str(n.children[0]).replace("reg", "").strip("() \t\r\n")
        return self.read_register(reg)

    def x_field_ref(self, n):
        table = self.get_table(str(n.children[0]))
        field = self.get_field(table, str(n.children[2]))
        self.index_to_y(field, n.children[1])
        return self.load_field_y(field)

    def load_field_y(self, field):
        """Load a table field whose slot offset is already in Y."""
        self.want_m(16)
        self.emit(f"LDA ${field.addr:04X},Y")
        if field.size == 1:
            self.emit("AND.w #$00FF")
            if field.type == "i8":
                self.sign_extend8()
        return field.type

    def x_neg(self, n):
        ty = self.expr(n.children[0])
        self.emit("EOR.w #$FFFF")
        self.emit("INC A")
        return "f16" if ty == "f16" else "i16"

    def x_bit_not(self, n):
        ty = self.expr(n.children[0])
        self.emit("EOR.w #$FFFF")
        return ty

    def x_logic_not(self, n):
        return self.bool_value(n)

    def x_logic_and(self, n):
        return self.bool_value(n)

    def x_logic_or(self, n):
        return self.bool_value(n)

    def x_equality(self, n):
        return self.bool_value(n)

    def x_relational(self, n):
        return self.bool_value(n)

    def bool_value(self, n):
        true_l, end_l = self.new_label(), self.new_label()
        self.branch(n, true_l, True)
        self.want_m(16)
        self.emit("LDA.w #$0000")
        self.emit(f"BRA {end_l}")
        self.label(true_l, keep=True)
        self.emit("LDA.w #$0001")
        self.label(end_l, keep=True)
        return "u16"

    def chain(self, n, fixed_op=None):
        kids = n.children
        ty = self.expr(kids[0])
        if fixed_op:
            for r in kids[1:]:
                ty = self.binop(fixed_op, ty, r)
        else:
            for i in range(1, len(kids), 2):
                ty = self.binop(str(kids[i].children[0]), ty, kids[i + 1])
        return ty

    def x_bitwise_or(self, n):
        return self.chain(n, "|")

    def x_bitwise_xor(self, n):
        return self.chain(n, "^")

    def x_bitwise_and(self, n):
        return self.chain(n, "&")

    def x_shift(self, n):
        return self.chain(n)

    def x_additive(self, n):
        return self.chain(n)

    def x_multiplicative(self, n):
        return self.chain(n)

    def simple_operand(self, n):
        """(text, type, is_immediate) when the operand can be used directly by an ALU op."""
        c = self.const(n)
        if c is not None:
            return f"#${c[0] & 0xFFFF:04X}", c[1], True
        if n.data == "var_ref":
            name = str(n.children[0])
            v = self.lookup(name)
            if v is not None and v.size == 2 and not v.mmio:
                return addr_str(v.addr), v.type, False
            if v is None and name in self.externs:
                return f"#{name}", "u16", True
        return None

    def alu(self, mnemonic, text, imm):
        self.emit(f"{mnemonic}.w {text}" if imm else f"{mnemonic} {text}")

    def operand_via_scratch(self, rnode):
        """Evaluate rnode with the current A preserved; leaves left in A, right in scratch."""
        self.want_m(16)
        self.emit("PHA")
        rty = self.expr(rnode)
        self.emit(f"STA {SCRATCH}")
        self.emit("PLA")
        return rty

    def binop(self, op, lty, rnode):
        if op in ("<<", ">>"):
            return self.shift_op(op, lty, rnode)
        if op == "*":
            return self.multiply(lty, rnode)
        if op == "/":
            return self.divide(lty, rnode)
        opnd = self.simple_operand(rnode)
        if opnd is None:
            rty = self.operand_via_scratch(rnode)
            opnd = (SCRATCH, rty, False)
        text, rty, imm = opnd
        self.want_m(16)
        if op == "+":
            self.emit("CLC")
        elif op == "-":
            self.emit("SEC")
        self.alu(ALU[op], text, imm)
        return combine(lty, rty)

    def shift_step(self, op, signed):
        if op == "<<":
            self.emit("ASL A")
        elif signed:
            self.emit("CMP.w #$8000")
            self.emit("ROR A")
        else:
            self.emit("LSR A")

    def shift_const(self, op, n, signed):
        self.want_m(16)
        if n <= 0:
            return
        if op == ">>" and signed:
            n = min(n, 15)
        elif n >= 16:
            self.emit("LDA.w #$0000")
            return
        if op == "<<" and n >= 8:
            self.emit("XBA")
            self.emit("AND.w #$FF00")
            n -= 8
        elif op == ">>" and n >= 8:
            self.emit("XBA")
            self.emit("AND.w #$00FF")
            if signed:
                self.sign_extend8()
            n -= 8
        for _ in range(n):
            self.shift_step(op, signed)

    def shift_op(self, op, lty, rnode):
        signed = lty in SIGNED
        c = self.const(rnode)
        if c is not None:
            self.shift_const(op, c[0], signed)
        else:
            self.want_m(16)
            self.emit("PHA")
            self.expr(rnode)
            self.emit("TAY")
            self.emit("PLA")
            loop, done = self.new_label(), self.new_label()
            self.emit("CPY.w #$0000")
            self.emit(f"BEQ {done}")
            self.label(loop, keep=True)
            self.shift_step(op, signed)
            self.emit("DEY")
            self.emit(f"BNE {loop}")
            self.label(done, keep=True)
        return lty if lty == "f16" or signed else "u16"

    def multiply(self, lty, rnode):
        c = self.const(rnode)
        if c is not None and c[1] != "f16":
            self.mul_const(c[0])
            return combine(lty, c[1])
        if c is not None and lty != "f16":
            self.error("Multiplying an integer by a fixed-point constant is not supported", rnode)
        rty = self.operand_via_scratch(rnode)
        if lty == "f16" and rty == "f16":
            self.emit("JSR _gel_fmul")
            self.m = 16
            self.uses_fmul = True
            return "f16"
        self.emit("JSR _gel_mul16")
        self.m = 16
        self.uses_mul = True
        return combine(lty, rty)

    def divide(self, lty, rnode):
        c = self.const(rnode)
        if c is None or c[0] <= 0 or c[0] & (c[0] - 1) or c[1] == "f16":
            self.error("Division is only supported by constant powers of two", rnode)
        self.shift_const(">>", c[0].bit_length() - 1, lty in SIGNED)
        return lty if lty in SIGNED else "u16"

    def x_pre_inc(self, n):
        return self.step_target(n, "+", post=False)

    def x_pre_dec(self, n):
        return self.step_target(n, "-", post=False)

    def x_post_inc(self, n):
        return self.step_target(n, "+", post=True)

    def x_post_dec(self, n):
        return self.step_target(n, "-", post=True)

    def step_target(self, n, op, post):
        target = n.children[0]
        one = Tree("num", [Token("NUMBER", "1")])
        self.compound(target, op, one)
        if post:
            self.emit("DEC A" if op == "+" else "INC A")
        return self.target_type(target)

    def compound(self, target, op, rhs):
        y_ops = ("+", "-", "&", "|", "^")
        self.assign_to(target, lambda: self.binop(op, self.expr(target), rhs),
                       rhs=rhs, compound_op=op if op in y_ops else None)

    def x_func_call(self, n):
        name = str(n.children[0])
        args = n.children[1].children if len(n.children) > 1 else []
        return self.call(name, args, n)

    def x_str_lit(self, n):
        text = str(n.children[0])[1:-1]
        label = f"_S{len(self.strings)}"
        self.strings.append((label, text))
        self.want_m(16)
        self.emit(f"LDA.w #{label}")
        return "u16"

    # ---------------------------------------------------- bit intrinsics
    # bit(n) folds to a constant; bit_set/bit_clear/bit_test compile inline
    # (TSB/TRB/AND), so they never add a subroutine or extra ROM data.
    def bit_mask(self, name, args, node):
        if len(args) != 2:
            self.error(f"'{name}' takes 2 arguments (target, bit)", node)
        target = args[0]
        if target.data not in ("var_ref", "field_ref"):
            self.error(f"First argument of '{name}' must be a variable or table field", node)
        c = self.const(args[1])
        limit = 8 if self.target_type(target) in ("u8", "i8") else 16
        if c is None or not 0 <= c[0] < limit:
            self.error(f"Bit index of '{name}' must be a constant 0-{limit - 1} for this target", node)
        return target, 1 << c[0]

    def bit_modify(self, name, args, node):
        target, mask = self.bit_mask(name, args, node)
        setting = name == "bit_set"
        v = self.lookup(str(target.children[0])) if target.data == "var_ref" else None
        if v is not None:
            if v.mmio:
                self.error(f"'{name}' cannot be used on hardware register '{v.name}' (read-modify-write)", node)
            op = "TSB" if setting else "TRB"
            if v.size == 1 or (mask < 0x100 and self.m == 8):
                self.want_m(8)
                self.emit(f"LDA.b #${mask:02X}")
            else:
                self.want_m(16)
                self.emit(f"LDA.w #${mask:04X}")
            self.emit(f"{op} {addr_str(v.addr)}")
        else:
            def producer():
                self.expr(target)
                self.want_m(16)
                if setting:
                    self.emit(f"ORA.w #${mask:04X}")
                else:
                    self.emit(f"AND.w #${~mask & 0xFFFF:04X}")
            self.assign_to(target, producer)
        return "u16"

    def bit_test(self, args, node):
        """Leaves A = (target & mask) with Z set accordingly: zero when clear, non-zero when set."""
        target, mask = self.bit_mask("bit_test", args, node)
        v = self.lookup(str(target.children[0])) if target.data == "var_ref" else None
        if v is not None:
            if v.size == 1 and v.mmio:
                self.want_m(8)
                self.emit(f"LDA {addr_str(v.addr)}")
            else:
                self.want_m(16)
                self.emit(f"LDA {addr_str(v.addr)}")
        else:
            self.expr(target)
        self.want_m(16)
        self.emit(f"AND.w #${mask:04X}")
        return "u16"

    def call(self, name, args, node):
        if name == "bit":
            c = self.const(node)
            if c is None:
                self.error("'bit' takes 1 constant argument (0-15)", node)
            self.want_m(16)
            self.emit(f"LDA.w #${c[0]:04X}")
            return "u16"
        if name in ("bit_set", "bit_clear"):
            return self.bit_modify(name, args, node)
        if name == "bit_test":
            return self.bit_test(args, node)
        if name == "peek8l":
            c = self.const(args[0]) if len(args) == 2 else None
            if c is None:
                self.error("'peek8l' takes 2 arguments: a constant bank number and a 16-bit address", node)
            self.expr(args[1])
            self.emit("TAX")
            self.want_m(8)
            self.emit(f"LDA.l ${c[0] & 0xFF:02X}0000,X")
            self.want_m(16)
            self.emit("AND.w #$00FF")
            return "u8"
        if name == "peek8":
            if len(args) != 1:
                self.error("'peek8' takes 1 argument (a bank-0 address)", node)
            self.expr(args[0])
            self.emit("TAX")
            self.want_m(8)
            self.emit("LDA.w $0000,X")
            self.want_m(16)
            self.emit("AND.w #$00FF")
            return "u8"
        params = self.funcs.get(name)
        if params is None:
            self.error(f"Call to undefined function '{name}'", node)
        if len(args) != len(params):
            self.error(f"'{name}' takes {len(params)} argument(s), got {len(args)}", node)
        if any(self.contains_call(a) for a in args):
            # Nested calls could overwrite parameter slots, so stage values on the stack.
            for a in args:
                self.expr(a)
                self.emit("PHA")
            for p in reversed(params):
                self.want_m(16)
                self.emit("PLA")
                self.store_var(p)
        else:
            for a, p in zip(args, params):
                self.expr(a)
                self.store_var(p)
        if self.optimize and self.can_inline(name):
            self.inline_call(name)
            self.m = None
        else:
            self.want_m(16)
            self.emit(f"JSR {name}")
            self.m = 16
        return "u16"

    def x_table_create(self, n):
        t = self.get_table(str(n.children[0]))
        if t.mask is None:
            self.error(f"'{t.name}' has more than 16 slots, so create()/free() are unavailable", n)
        mask = addr_str(t.mask)
        full, done, lp = self.new_label(), self.new_label(), self.new_label()
        self.want_m(16)
        self.emit(f"; --- {t.name}.create() ---")
        self.emit(f"LDA {mask}")
        self.emit("EOR.w #$FFFF")
        if t.size < 16:
            self.emit(f"AND.w #${(1 << t.size) - 1:04X}")
        self.emit(f"BEQ {full}")
        self.emit(f"STA {SCRATCH}")
        self.emit("EOR.w #$FFFF")
        self.emit("INC A")
        self.emit(f"AND {SCRATCH}")
        self.emit(f"STA {SCRATCH2}")
        self.emit(f"ORA {mask}")
        self.emit(f"STA {mask}")
        self.emit("LDX.w #$FFFF")
        self.emit(f"LDA {SCRATCH2}")
        self.label(lp, keep=True)
        self.emit("INX")
        self.emit("LSR A")
        self.emit(f"BCC {lp}")
        self.emit(f"BRA {done}")
        self.label(full, keep=True)
        self.emit("LDX.w #$FFFF")
        self.label(done, keep=True)
        self.emit("TXA")
        return "u16"

    def x_table_free(self, n):
        t = self.get_table(str(n.children[0]))
        if t.mask is None:
            self.error(f"'{t.name}' has more than 16 slots, so create()/free() are unavailable", n)
        mask = addr_str(t.mask)
        lp, done = self.new_label(), self.new_label()
        self.expr(n.children[1])
        self.emit("TAY")
        self.emit(f"; --- {t.name}[i].free() ---")
        self.emit("LDA.w #$0001")
        self.emit("CPY.w #$0000")
        self.emit(f"BEQ {done}")
        self.label(lp, keep=True)
        self.emit("ASL A")
        self.emit("DEY")
        self.emit(f"BNE {lp}")
        self.label(done, keep=True)
        self.emit("EOR.w #$FFFF")
        self.emit(f"AND {mask}")
        self.emit(f"STA {mask}")
        return "u16"

    # -------------------------------------------------------------- branching
    INVERSE = {"eq": "BNE", "ne": "BEQ", "cs": "BCC", "cc": "BCS", "mi": "BPL", "pl": "BMI"}
    FLIP = {"eq": "ne", "ne": "eq", "cs": "cc", "cc": "cs", "mi": "pl", "pl": "mi"}

    def jump_if(self, cond, target):
        """Jump to target when the CPU condition holds (range-safe: inverted branch over a JMP)."""
        self.want_m(16)
        skip = self.new_label()
        self.emit(f"{self.INVERSE[cond]} {skip}")
        self.emit(f"JMP {target}")
        self.label(skip, keep=True)

    def finish_compare(self, true_l, target, when):
        """Code before this falls through on 'false' and branches to true_l on 'true'."""
        if when:
            end = self.new_label()
            self.emit(f"BRA {end}")
            self.label(true_l, keep=True)
            self.emit(f"JMP {target}")
            self.label(end, keep=True)
        else:
            self.emit(f"JMP {target}")
            self.label(true_l, keep=True)

    def branch(self, n, target, when):
        """Jump to target if truthiness(n) == when, otherwise fall through."""
        d = n.data
        if d == "logic_not":
            return self.branch(n.children[0], target, not when)
        if d in ("logic_and", "logic_or"):
            kids = n.children
            short_when = d == "logic_or"       # the value that decides the outcome early
            if when == short_when:
                for k in kids:
                    self.branch(k, target, when)
            else:
                skip = self.new_label()
                for k in kids[:-1]:
                    self.branch(k, skip, short_when)
                self.branch(kids[-1], target, when)
                self.label(skip, keep=True)
            return
        if d in ("equality", "relational") and self.const(n) is None:
            if len(n.children) != 3:
                self.error("Chained comparisons are not supported", n)
            op = str(n.children[1].children[0])
            return self.compare(op, n.children[0], n.children[2], target, when)
        if d == "func_call" and str(n.children[0]) == "bit_test":
            # AND already sets Z, so no CMP is needed
            self.cur_node = n
            self.bit_test(n.children[1].children if len(n.children) > 1 else [], n)
            self.jump_if("ne" if when else "eq", target)
            return
        self.expr(n)
        self.emit("CMP.w #$0000")
        self.jump_if("ne" if when else "eq", target)

    def compare(self, op, lnode, rnode, target, when):
        lty = self.expr(lnode)
        opnd = self.simple_operand(rnode)
        if opnd is None:
            rty = self.operand_via_scratch(rnode)
            opnd = (SCRATCH, rty, False)
        text, rty, imm = opnd
        signed = op not in ("==", "!=") and (lty in SIGNED or rty in SIGNED)
        self.want_m(16)
        if not signed:
            self.alu("CMP", text, imm)
            if op in ("==", "!="):
                cond = "eq" if op == "==" else "ne"
            elif op in ("<", ">="):
                cond = "cc" if op == "<" else "cs"
            else:
                return self.compare_unsigned_compound(op, target, when)
            return self.jump_if(cond if when else self.FLIP[cond], target)
        # Signed: SBC sets V, so fix N up when the subtraction overflowed.
        true_l, fix = self.new_label(), self.new_label()
        self.emit("SEC")
        self.alu("SBC", text, imm)
        if op == ">":
            false_l = self.new_label()
            self.emit(f"BEQ {false_l}")
        if op == "<=":
            self.emit(f"BEQ {true_l}")
        self.emit(f"BVC {fix}")
        self.emit("EOR.w #$8000")
        self.label(fix, keep=True)
        self.emit(f"BPL {true_l}" if op in (">=", ">") else f"BMI {true_l}")
        if op == ">":
            self.label(false_l, keep=True)
        self.finish_compare(true_l, target, when)

    def compare_unsigned_compound(self, op, target, when):
        true_l = self.new_label()
        if op == ">":       # C=1 and Z=0
            false_l = self.new_label()
            self.emit(f"BEQ {false_l}")
            self.emit(f"BCS {true_l}")
            self.label(false_l, keep=True)
        else:               # '<=': C=0 or Z=1
            self.emit(f"BEQ {true_l}")
            self.emit(f"BCC {true_l}")
        self.finish_compare(true_l, target, when)

    # ------------------------------------------------------------- statements
    def block(self, node):
        for s in node.children:
            self.stmt(s)

    def stmt(self, s):
        self.cur_node = s
        handler = getattr(self, "s_" + s.data, None)
        if handler is None:
            self.error(f"Unsupported statement '{s.data}'", s)
        handler(s)

    def s_var_decl_stmt(self, s):
        vtype, name = str(s.children[0]), str(s.children[1])
        self.check_type(vtype)
        v = self.scope.get(name)
        if v is None or v.type != vtype:
            v = Var(name, vtype, self.alloc_dp(SIZES[vtype], name))
            self.scope[name] = v
        if len(s.children) > 2:
            self.expr(s.children[2])
            self.store_var(v)

    def s_assign_stmt(self, s):
        target, op_node, rhs = s.children
        op = str(op_node.children[0])
        if op == "=":
            self.assign_to(target, lambda: self.expr(rhs), rhs=rhs)
        else:
            self.compound(target, op[:-1], rhs)

    def s_expr_stmt(self, s):
        e = s.children[0]
        if e.data in ("post_inc", "post_dec"):
            self.step_target(e, "+" if e.data == "post_inc" else "-", post=False)
        else:
            self.expr(e)

    def s_return_stmt(self, s):
        if s.children:
            self.expr(s.children[0])
        if self.inline_end:
            self.emit(f"JMP {self.inline_end}")
        else:
            self.want_m(16)
            self.emit("RTS")
        self.m = None

    def s_inline_asm_stmt(self, s):
        self.emit("; Inline ASM")
        for line in str(s.children[0]).strip().splitlines():
            code = line.strip()
            if not code:
                continue
            is_label = code[0] in "-+" or (":" in code.split(";")[0] and not code.startswith(";"))
            self.raw(code if is_label else "    " + code)
        self.m = None

    def s_if_stmt(self, s):
        clauses, else_block = [(s.children[0], s.children[1])], None
        for k in s.children[2:]:
            if k.data == "else_if_clause":
                clauses.append((k.children[0], k.children[1]))
            else:
                else_block = k.children[0]
        end = self.new_label()
        for i, (cond, body) in enumerate(clauses):
            nxt = self.new_label()
            self.branch(cond, nxt, False)
            self.block(body)
            if i < len(clauses) - 1 or else_block is not None:
                self.emit(f"JMP {end}")
            self.label(nxt)
        if else_block is not None:
            self.block(else_block)
        self.label(end)

    def s_while_stmt(self, s):
        top, end = self.new_label(), self.new_label()
        self.label(top)
        self.branch(s.children[0], end, False)
        self.loop_depth += 1
        self.block(s.children[1])
        self.loop_depth -= 1
        self.emit(f"JMP {top}")
        self.label(end)

    def s_switch_stmt(self, s):
        self.expr(s.children[0])
        cases = [k for k in s.children[1:] if k.data == "case_clause"]
        default = next((k for k in s.children[1:] if k.data == "default_clause"), None)
        end = self.new_label()
        labels = []
        for c in cases:
            val = str(c.children[0])
            if val in self.enums:
                num = self.enums[val]
            elif val[0].isdigit():
                num = parse_number(val)
            else:
                self.error(f"Unknown case constant '{val}'", c)
            lbl = self.new_label()
            labels.append(lbl)
            self.emit(f"CMP.w #${num & 0xFFFF:04X}")
            self.jump_if("eq", lbl)
        default_l = self.new_label() if default is not None else end
        self.emit(f"JMP {default_l}")
        for c, lbl in zip(cases, labels):
            self.label(lbl)
            for st in c.children[1:]:
                self.stmt(st)
            self.emit(f"JMP {end}")
        if default is not None:
            self.label(default_l)
            for st in default.children:
                self.stmt(st)
        self.label(end)

    def s_table_iter_stmt(self, s):
        t = self.get_table(str(s.children[0]))
        vname = str(s.children[1])
        v = Var(vname, "u16", self.alloc_dp(2, vname))
        self.scope[vname] = v
        bit = self.alloc_dp(2, "for_each bit") if t.mask is not None else None
        top, nxt = self.new_label(), self.new_label()
        self.want_m(16)
        self.emit(f"; --- {t.name}.for_each({vname}) ---")
        self.emit("LDA.w #$0000")
        self.emit(f"STA {addr_str(v.addr)}")
        if bit is not None:
            self.emit("LDA.w #$0001")
            self.emit(f"STA {addr_str(bit)}")
        self.label(top)
        if bit is not None:
            self.want_m(16)
            self.emit(f"LDA {addr_str(bit)}")
            self.emit(f"AND {addr_str(t.mask)}")
            self.jump_if("eq", nxt)
        self.loop_depth += 1
        self.block(s.children[2])
        self.loop_depth -= 1
        self.label(nxt)
        self.want_m(16)
        self.emit(f"INC {addr_str(v.addr)}")
        if bit is not None:
            self.emit(f"ASL {addr_str(bit)}")
        self.emit(f"LDA {addr_str(v.addr)}")
        self.emit(f"CMP.w #${t.size:04X}")
        self.jump_if("cc", top)

    def emit_persist_restore(self):
        """Dev builds only: copy persist vars and unchanged pool tables back from the staging
        area the Lua hook filled."""
        keep = [v for v, restorable in self.persist_vars if restorable]
        tables = list(self.table_layout.values())
        if not self.hot_reload or not (keep or tables):
            return
        self.want_m(16)
        self.emit(f"LDA.l ${STAGE_MAGIC_ADDR:06X}")
        self.emit(f"CMP.w #${STAGE_MAGIC:04X}")
        self.emit("BEQ _gel_do_restore")
        self.emit("JMP _gel_no_restore")
        self.label("_gel_do_restore")
        for v in keep:
            self.want_m(v.size * 8)
            self.emit(f"LDA.l ${STAGE_BASE + v.addr:06X}")
            self.emit(f"STA {addr_str(v.addr)}")
        self.want_m(16)
        for t, lo, hi in tables:
            self.emit(f"LDA.l ${STAGE_BASE + t.mask:06X}")
            self.emit(f"STA {addr_str(t.mask)}")
            loop = f"_gel_tbl_{t.name}"
            self.emit(f"LDX.w #${lo:04X}")
            self.label(loop, keep=True)
            self.emit(f"LDA.l ${STAGE_BASE:06X},X")
            self.emit("STA.l $7E0000,X")
            self.emit("INX")
            self.emit("INX")
            self.emit(f"CPX.w #${(hi + 1) & ~1:04X}")
            self.emit(f"BNE {loop}")
        self.want_m(16)
        self.emit("LDA.w #$0000")
        self.emit(f"STA.l ${STAGE_MAGIC_ADDR:06X}")
        self.label("_gel_no_restore")

    # ---------------------------------------------------------------- top level
    def function(self, item):
        self.cur_node = item
        kids = [k for k in item.children if not (isinstance(k, Tree) and k.data == "static_kw")]
        name = str(kids[0])
        self.fn_name = name
        self.scope = {p.name: p for p in self.funcs[name]}
        self.m = 16          # calling convention: functions are entered and left in 16-bit A
        start = len(self.out)
        self.raw(f"\n; --- Function: {name} ---")
        self.raw(f"{name}:")
        self.block(kids[-1])
        self.want_m(16)
        self.emit("RTS")
        self.chunks[name] = (start, len(self.out))

    def drop_inlined(self):
        """Remove functions nothing reaches (fully-inlined bodies, unused library code)."""
        in_chunk = {}
        for n, (a, b) in self.chunks.items():
            for i in range(a, b):
                in_chunk[i] = n
        names = list(self.chunks)
        pat = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r")\b") if names else None

        def refs(indices):
            found = set()
            for i in indices:
                l = self.out[i]
                if pat and not l.lstrip().startswith(";"):
                    found.update(pat.findall(l))
            return found

        live = refs([i for i in range(len(self.out)) if i not in in_chunk])
        if "init_game" in self.chunks:
            live.add("init_game")
        work = list(live)
        while work:
            n = work.pop()
            for m in refs(range(*self.chunks[n])):
                if m not in live:
                    live.add(m)
                    work.append(m)
        removed = set()
        for n in names:
            if n not in live:
                removed.update(range(*self.chunks[n]))
        self.out = [l for i, l in enumerate(self.out) if i not in removed]

    def compile(self, tree):
        self.plan_persist(tree)
        for item in tree.children:
            self.declare(item)
        self.layout = {v.name: [v.type, v.addr] for v, _ in self.persist_vars}
        # Pool tables are carried across hot reloads too, but only when their layout is
        # unchanged (same address, mask and fields); otherwise their bytes would be garbage.
        self.table_layout = {}
        for t in self.tables.values():
            if t.mask is None:
                continue
            lo = min(f.addr for f in t.fields.values())
            hi = max(f.addr + f.step * t.size if f.step else f.addr for f in t.fields.values())
            sig = ["table", t.mask, lo, t.size,
                   [[n, f.type, f.addr, f.step] for n, f in sorted(t.fields.items())]]
            self.layout["table:" + t.name] = sig
            prev = self.prev_layout.get("table:" + t.name)
            if prev == sig:
                self.table_layout[t.name] = (t, lo, hi)

        summary = ["; --- Symbols ---"]
        for v in self.vars.values():
            kind = "mmio" if v.mmio else "ram"
            summary.append(f"; {kind:4} {v.type:3} {v.name} @ {addr_str(v.addr)}")
        for t in self.tables.values():
            mask = f", pool mask @ {addr_str(t.mask)}" if t.mask is not None else ""
            summary.append(f"; table {t.name}[{t.size}]{mask}")
            for fname, f in t.fields.items():
                summary.append(f";   .{fname} {f.type} @ ${f.addr:04X} (+{f.step}/slot)")

        self.fn_name = "_gel_init_globals"
        self.raw("\n; --- Global initialisers ---")
        self.raw("_gel_init_globals:")
        self.m = 16
        for v, node in self.inits:
            c = self.const(node) if self.optimize else None
            if c is not None and c[0] == 0 and not v.mmio and v.addr < 0x1F00 and v.name not in self.layout:
                continue  # WRAM is already zero from the boot-time clear
            self.cur_node = node
            self.expr(node)
            self.store_var(v)
        self.emit_persist_restore()
        self.want_m(16)
        self.emit("RTS")

        for item in tree.children:
            if item.data == "function_decl":
                self.function(item)

        if self.uses_mul:
            self.raw(MUL_HELPER)
        if self.uses_fmul:
            self.raw(FMUL_HELPER)
        if self.optimize:
            self.drop_inlined()

        if self.strings:
            self.raw("\n; --- String literals ---")
            for label, text in self.strings:
                self.raw(f"{label}:")
                self.raw(f'    db "{text}", 0')

        # Native-mode NMI vector: bumps the `nmi_ticks` counter (if the program declares it)
        # so game loops can run logic on a fixed 60 Hz step. RTI restores P, so the SEP is safe.
        tick = self.vars.get("nmi_ticks")
        if tick is not None and not tick.mmio:
            self.raw("\n; --- NMI handler: fixed-timestep tick counter ---")
            self.raw("_gel_nmi:")
            self.emit("SEP #$20")
            self.emit("BIT $4210            ; acknowledge NMI")
            self.emit(f"INC ${tick.addr:02X}")
            self.emit("RTI")
            nmi_vec = "_gel_nmi"
        else:
            nmi_vec = "$0000"
        call_main = "    JSR init_game" if "init_game" in self.funcs else "    ; (no init_game() defined)"
        boot = BOOT.replace("{STACK}", f"${STACK_TOP:04X}").replace("{CALL_MAIN}", call_main)
        volatile = {tick.addr} if tick is not None else set()
        flat = "\n".join(self.out).split("\n")
        body = flat if not self.optimize else peephole.optimize(flat, volatile)
        for problem in peephole.verify_modes(body):
            print(f"  gel warning: accumulator-mode check: {problem}", file=sys.stderr)
        header = make_header(self.header_cfg).replace("{NMI}", nmi_vec)
        return header + boot + "\n".join(summary) + "\n" + "\n".join(body) + "\n"


DEFAULT_HEADER = {
    "title": "GEL GAME TITLE",
    "rom_size_kb": 32,    # minimum size; the build grows it (power of two) if the data needs more
    "sram_kb": 0,
    "region": 1,          # 0 = Japan, 1 = USA, 2 = Europe
    "developer": 0,
    "version": 0,
}


def size_code(kb):
    """Header size byte: the ROM/SRAM size is 2^n KB."""
    return max(kb, 1).bit_length() - 1 if kb else 0


def make_header(cfg=None):
    c = {**DEFAULT_HEADER, **(cfg or {})}
    title = "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(c["title"]))[:21].ljust(21)
    return HEADER.replace("{TITLE}", title.replace('"', "'")) \
        .replace("{ROMTYPE}", "$02" if c["sram_kb"] else "$00") \
        .replace("{ROMSIZE}", f"${size_code(c['rom_size_kb']):02X}") \
        .replace("{RAMSIZE}", f"${size_code(c['sram_kb']):02X}") \
        .replace("{REGION}", f"${int(c['region']):02X}") \
        .replace("{DEVELOPER}", f"${int(c['developer']):02X}") \
        .replace("{VERSION}", f"${int(c['version']):02X}")


HEADER = """lorom

; --- SNES ROM Header ---
org $00FFC0
    db "{TITLE}" ; 21 bytes title
    db $30                      ; Mode 30 (FastROM LoROM)
    db {ROMTYPE}                      ; ROM type (ROM, or ROM + battery SRAM)
    db {ROMSIZE}                      ; ROM size, 2^n KB
    db {RAMSIZE}                      ; SRAM size, 2^n KB
    db {REGION}                      ; Country
    db {DEVELOPER}                      ; Developer ID
    db {VERSION}                      ; Version
    dw $FFFF                    ; Checksum complement (patched by the build)
    dw $0000                    ; Checksum (patched by the build)

; --- Native Mode Interrupt Vectors ---
org $00FFE0
    dw $0000, $0000, $0000, $0000
    dw $0000, {NMI}, $0000, $0000

; --- Emulation Mode Interrupt Vectors ---
org $00FFF0
    dw $0000, $0000, $0000, $0000
    dw $0000, $0000
    dw _gel_boot                ; Reset Vector -> Points to hardware bootstrap
    dw $0000

"""

BOOT = """; --- Hardware Bootstrap Entry Point ---
org $808000
_gel_boot:
    SEI                         ; Disable IRQs
    CLC
    XCE                         ; Switch 65C816 to Native 16-bit Mode
    SEP #$20
    LDA #$01
    STA $420D                   ; FastROM: banks $80+ run ROM at 3.58 MHz
    JML _gel_boot_fast          ; Leave bank $00 (slow) for its bank $80 mirror
_gel_boot_fast:
    REP #$10                    ; 16-bit Index Registers (X/Y)
    LDX.w #{STACK}
    TXS                         ; Stack at top of low WRAM
    PHK
    PLB                         ; Data bank = program bank ($80)
    REP #$20
    LDA.w #$0000
    TCD                         ; Direct page = $0000
    LDX.w #$1EFE                ; Clear WRAM $0000-$1EFF (variables, tables, OAM shadow)
_gel_clear:
    STZ $0000,X
    DEX
    DEX
    BPL _gel_clear
    REP #$20                    ; 16-bit A: GEL functions are entered and left in 16-bit A
    JSR _gel_init_globals
{CALL_MAIN}

_gel_halt:
    BRA _gel_halt               ; Fallback loop

"""

MUL_HELPER = """
; --- Runtime: A = A * $00 (16-bit) ---
_gel_mul16:
    REP #$20
    STA $02
    LDA.w #$0000
_gel_mul_loop:
    LSR $02
    BCC _gel_mul_skip
    CLC
    ADC $00
_gel_mul_skip:
    ASL $00
    LDY $02
    BNE _gel_mul_loop
    RTS
"""

FMUL_HELPER = """
; --- Runtime: A = A * $00 (signed 8.8 fixed point) ---
_gel_fmul:
    REP #$20
    STA $02
    EOR $00
    STA $0C
    LDA $02
    BPL _gel_fmul_a
    EOR.w #$FFFF
    INC A
    STA $02
_gel_fmul_a:
    LDA $00
    BPL _gel_fmul_b
    EOR.w #$FFFF
    INC A
    STA $00
_gel_fmul_b:
    STZ $04
    STZ $06
    STZ $08
_gel_fmul_loop:
    LSR $02
    BCC _gel_fmul_skip
    LDA $04
    CLC
    ADC $00
    STA $04
    LDA $06
    ADC $08
    STA $06
_gel_fmul_skip:
    ASL $00
    ROL $08
    LDA $02
    BNE _gel_fmul_loop
    LDA $05
    BIT $0C
    BPL _gel_fmul_done
    EOR.w #$FFFF
    INC A
_gel_fmul_done:
    RTS
"""


def compile_gel(gel_code: str, prev_layout=None, hot_reload=True, base_dir=".", include_dirs=(), optimize=True,
                header=None):
    """Returns (assembly_text, persist_layout)."""
    from gel_parser import parse_gel_code
    ast = parse_gel_code(gel_code, base_dir, include_dirs)
    if ast is None:
        raise CompileError("Syntax error (see details above)")
    comp = Compiler(prev_layout, hot_reload, header)
    comp.optimize = optimize
    asm = comp.compile(ast)
    return asm, comp.layout


def compile_gel_to_asm(gel_code: str) -> str:
    return compile_gel(gel_code)[0]
