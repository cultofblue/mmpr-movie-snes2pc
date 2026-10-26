#!/usr/bin/env python3
"""Tracing 65816 disassembler for LoROM SNES images.

Follows code from the hardware vectors (plus any extra entry points listed in
config/entrypoints.txt), tracking the M and X register-width flags along each
path so immediate operands are decoded at the correct size. Everything the
trace does not reach is emitted as data, so the output always reassembles to a
byte-identical ROM.

Outputs (in --out):
  main.asm             asar entry file (includes every bank)
  bank_XX.asm          one file per 32 KiB LoROM bank
  codemap.txt          per-instruction trace (addr, flags, bytes, text)
  report.txt           coverage, flag conflicts, unresolved indirect jumps
  labels.txt           every discovered entry point / branch target
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opcodes import OPS, operand_size, BRANCHES, TERMINATORS  # noqa: E402

BANK = 0x8000
OPCODE_OF = {v: i for i, v in enumerate(OPS)}


class Rom:
    def __init__(self, data, bank_base):
        self.d = data
        self.nbanks = len(data) // BANK
        self.bank_base = bank_base  # bank number used for org/labels ($00 or $80)

    def off(self, addr):
        """SNES address -> file offset, or None if not ROM."""
        bank, lo = addr >> 16, addr & 0xFFFF
        b = bank & 0x7F
        if b >= 0x7E or lo < 0x8000:
            return None
        o = (b % 0x80) * BANK + (lo - 0x8000)
        return o if o < len(self.d) else None

    def addr(self, off):
        return ((self.bank_base + off // BANK) << 16) | (0x8000 + off % BANK)


class Tracer:
    def __init__(self, rom):
        self.rom = rom
        self.inst = {}            # off -> (m8, x8, length)
        self.owner = {}           # byte off -> instruction start off
        self.labels = {}          # off -> kind
        self.conflicts = []
        self.unresolved = []
        self.tables = {}          # off -> (entry count, kind)
        self.data_marks = set()   # byte offsets forced to data (tables)
        self.work = collections.deque()
        self.forced = {}          # off -> (m8, x8) observed in the emulator

    def add_entry(self, addr, m8, x8, kind="sub"):
        o = self.rom.off(addr)
        if o is None:
            return
        if o not in self.labels or kind == "sub":
            self.labels[o] = kind
        self.work.append((o, m8, x8, ()))

    def run(self):
        while self.work:
            self.trace(*self.work.popleft())

    def trace(self, o, m8, x8, php):
        rom = self.rom
        d = rom.d
        while True:
            if o >= len(d):
                return
            if o in self.forced and self.forced[o] != (m8, x8):
                m8, x8 = self.forced[o]       # the emulator saw it run this way
            if o in self.inst:
                pm, px, _ = self.inst[o]
                if (pm, px) != (m8, x8):
                    self.conflicts.append((rom.addr(o), (pm, px), (m8, x8)))
                return
            if o in self.owner or o in self.data_marks:
                self.conflicts.append((rom.addr(o), "overlap", None))
                return
            op = d[o]
            mn, mode = OPS[op]
            n = 1 + operand_size(mode, m8, x8)
            if (o % BANK) + n > BANK:      # instruction would cross bank end
                return
            if any((o + i) in self.owner or (o + i) in self.data_marks for i in range(n)):
                self.conflicts.append((rom.addr(o), "overlap", None))
                return
            if mn in ("brk", "cop", "wdm", "stp") or op == 0xFF and d[o+1:o+4] == b"\xff\xff\xff":
                # almost always means we walked into data; stop here without claiming
                if mn != "stp":
                    self.conflicts.append((rom.addr(o), "hit-" + mn, None))
                    return
            self.inst[o] = (m8, x8, n)
            for i in range(n):
                self.owner[o + i] = o
            opnd = int.from_bytes(d[o+1:o+n], "little")
            here = rom.addr(o)
            bank = here & 0xFF0000

            if mn == "rep":
                if opnd & 0x20: m8 = False
                if opnd & 0x10: x8 = False
            elif mn == "sep":
                if opnd & 0x20: m8 = True
                if opnd & 0x10: x8 = True
            elif mn == "php":
                php = php + ((m8, x8),)
            elif mn == "plp":
                if php:
                    m8, x8 = php[-1]
                    php = php[:-1]
            elif mn == "xce":
                m8 = x8 = True   # conservative: either direction leaves 8-bit after reset idiom

            if mn in BRANCHES:
                if mode == "rel":
                    tgt = (here & 0xFF0000) | ((here + 2 + (opnd - 256 if opnd & 0x80 else opnd)) & 0xFFFF)
                else:
                    tgt = (here & 0xFF0000) | ((here + 3 + (opnd - 65536 if opnd & 0x8000 else opnd)) & 0xFFFF)
                to = rom.off(tgt)
                if to is not None:
                    self.labels.setdefault(to, "loc")
                    self.work.append((to, m8, x8, php))
                if mn in ("bra", "brl"):
                    return
            elif mn == "jsr" and mode == "abs":
                self.add_entry(bank | opnd, m8, x8)
            elif mn == "jsl":
                self.add_entry(opnd, m8, x8)
            elif mn == "jmp" and mode == "abs":
                to = rom.off(bank | opnd)
                if to is not None:
                    self.labels.setdefault(to, "loc")
                    self.work.append((to, m8, x8, php))
                return
            elif mn == "jml" and mode == "long":
                to = rom.off(opnd)
                if to is not None:
                    self.labels.setdefault(to, "loc")
                    self.work.append((to, m8, x8, php))
                return
            elif mode == "absxi":            # jmp (tbl,x) / jsr (tbl,x)
                self.jump_table(bank | opnd, o + n, m8, x8, here, mn)
                if mn == "jmp":
                    return
            elif mn in ("jmp", "jml"):       # (abs) / [abs]
                self.unresolved.append((here, f"{mn} {mode} ${opnd:04X}"))
                return
            elif mn in TERMINATORS:
                return
            o += n

    def jump_table(self, taddr, after_off, m8, x8, site, mn):
        """Conservative table walk: 16-bit pointers into the same bank."""
        rom = self.rom
        to = rom.off(taddr)
        if to is None:
            self.unresolved.append((site, f"{mn} (${taddr & 0xFFFF:04X},x) table outside ROM"))
            return
        bank = taddr & 0xFF0000
        targets = []
        zeros = 0
        p = to
        while len(targets) < 128 and p + 1 < len(rom.d) and (p % BANK) < BANK - 1:
            if p in self.owner or p in self.labels and targets:
                break
            w = struct.unpack_from("<H", rom.d, p)[0]
            if w == 0 and zeros < 1:          # null slot (e.g. unused index 0)
                zeros += 1
                p += 2
                continue
            if w < 0x8000:
                break
            zeros = 0
            t = rom.off(bank | w)
            if t is None or rom.d[t] in (0x00, 0x02, 0x42, 0xDB, 0xFF):
                break
            targets.append(t)
            p += 2
        while p > to and struct.unpack_from("<H", rom.d, p - 2)[0] == 0:
            p -= 2                            # don't swallow trailing zero words
        if not targets:
            self.unresolved.append((site, f"{mn} (${taddr & 0xFFFF:04X},x) empty table"))
            return
        self.tables[to] = ((p - to) // 2, mn)
        for i in range(to, p):
            self.data_marks.add(i)
        for t in targets:
            self.labels.setdefault(t, "jt")
            self.work.append((t, m8, x8, ()))


HW = {}      # 16-bit address -> name, valid in banks $00-$3F/$80-$BF
RAM = {}     # WRAM offset ($0000-$1FFFF) -> name
SYMDESC = {}


def load_symbols(cfgdir):
    hw = os.path.join(cfgdir, "hardware.sym")
    if os.path.exists(hw):
        for line in open(hw):
            p = line.split("#")[0].split(None, 2)
            if len(p) >= 2:
                HW[int(p[0], 16)] = p[1]
                SYMDESC[p[1]] = p[2].strip() if len(p) > 2 else ""
    names = ["DMAP", "BBAD", "A1TL", "A1TH", "A1B", "DASL", "DASH", "DASB", "A2AL", "A2AH", "NLTR"]
    for ch in range(8):
        for i, n in enumerate(names):
            nm = f"{n}{ch}"
            HW[0x4300 + ch * 16 + i] = nm
            SYMDESC[nm] = f"DMA channel {ch}"
    ram = os.path.join(cfgdir, "ram.sym")
    if os.path.exists(ram):
        for line in open(ram):
            p = line.split("#")[0].split(None, 2)
            if len(p) >= 2:
                a = int(p[0], 16)
                RAM[a - 0x7E0000 if a >= 0x7E0000 else a] = p[1]
                SYMDESC[p[1]] = p[2].strip() if len(p) > 2 else ""


def sym_abs(v):
    if v in HW:
        return "!" + HW[v]
    if v < 0x2000 and v in RAM:
        return "!" + RAM[v]
    return None


def sym_long(v):
    bank, lo = v >> 16, v & 0xFFFF
    if (bank & 0x7F) < 0x40:
        n = sym_abs(lo)
        if n:
            return n if bank == 0 else f"${bank:02X}0000+{n}"
    if bank in (0x7E, 0x7F):
        w = v - 0x7E0000
        if w in RAM:
            return f"$7E0000+!{RAM[w]}"
    return None

TAG_RULES = [   # (tag, predicate on the set of symbols/mnemonics a function touches) - first match names it
    ("dma",     lambda u: "!MDMAEN" in u["w"]),
    ("hdma",    lambda u: "!HDMAEN" in u["w"]),
    ("dmacfg",  lambda u: any(x[1:5] in ("DMAP", "BBAD", "A1TL", "A2AL", "NLTR") for x in u["w"])),
    ("sound",   lambda u: any(x.startswith("!APUIO") for x in u["rw"])),
    ("input",   lambda u: any(x.startswith("!JOY") or x.startswith("!joy") for x in u["r"])),
    ("vram",    lambda u: any(x in u["w"] for x in ("!VMDATAL", "!VMDATAH", "!VMADDL", "!VMADDH"))),
    ("palette", lambda u: any(x in u["w"] for x in ("!CGDATA", "!CGADD"))),
    ("oam",     lambda u: any(x in u["w"] for x in ("!OAMDATA", "!OAMADDL"))),
    ("ppu",     lambda u: any(x.startswith("!") and x[1:].isupper() and x[1:] in PPU_REGS for x in u["w"])),
    ("math",    lambda u: any(x in u["w"] for x in ("!WRMPYB", "!WRDIVB"))),
    ("player",  lambda u: sum(1 for x in u["rw"] if x.startswith("!player_")) >= 1),
    ("obj",     lambda u: sum(1 for x in u["rw"] if x.startswith("!obj_")) >= 3),
]
PPU_REGS = {"INIDISP", "OBSEL", "BGMODE", "MOSAIC", "BG1SC", "BG2SC", "BG3SC", "BG4SC", "BG12NBA",
            "BG34NBA", "BG1HOFS", "BG1VOFS", "BG2HOFS", "BG2VOFS", "BG3HOFS", "BG3VOFS", "TM", "TS",
            "CGWSEL", "CGADSUB", "COLDATA", "SETINI", "W12SEL", "W34SEL", "WOBJSEL", "TMW", "TSW"}
WRITES = {"sta", "stx", "sty", "stz", "inc", "dec", "asl", "lsr", "rol", "ror", "tsb", "trb"}


def analyse_functions(tr, rom, data, labels_by_off, vector_labels, cfgdir):
    entries = {o for o, k in tr.labels.items() if k in ("sub", "vec", "jt", "dyn") and o in tr.inst}
    entries |= {o for o in vector_labels if o in tr.inst}
    manual = {}
    mp = os.path.join(cfgdir, "names.txt")
    if os.path.exists(mp):
        for line in open(mp):
            p = line.split("#")[0].split()
            if len(p) >= 2:
                manual[rom.off(int(p[0], 16))] = p[1]
                entries.add(rom.off(int(p[0], 16)))
    entries.discard(None)
    funcs = {}
    for e in sorted(entries):
        body, calls, todo = set(), set(), [e]
        use = {"r": set(), "w": set(), "rw": set()}
        while todo:
            o = todo.pop()
            while o in tr.inst and o not in body and (o == e or o not in entries):
                body.add(o)
                m8, x8, n = tr.inst[o]
                mn, mode = OPS[data[o]]
                opnd = int.from_bytes(data[o + 1:o + n], "little")
                here = rom.addr(o)
                sym = None
                if mode in ("abs", "absx", "absy") and mn not in ("jsr", "jmp"):
                    sym = sym_abs(opnd)
                elif mode in ("long", "longx") and mn not in ("jsl", "jml"):
                    sym = sym_long(opnd)
                    if sym and "+" in sym:
                        sym = sym.split("+", 1)[1]
                elif mode in ("dp", "dpx", "dpy") and opnd in RAM:
                    sym = "!" + RAM[opnd]
                if sym:
                    use["rw"].add(sym)
                    use["w" if mn in WRITES else "r"].add(sym)
                if mn == "jsr" and mode == "abs":
                    t = rom.off((here & 0xFF0000) | opnd)
                    if t is not None: calls.add(t)
                elif mn == "jsl":
                    t = rom.off(opnd)
                    if t is not None: calls.add(t)
                if mn in BRANCHES or (mn in ("jmp", "jml") and mode in ("abs", "long")):
                    if mode == "rel":
                        tgt = (here & 0xFF0000) | ((here + 2 + (opnd - 256 if opnd & 0x80 else opnd)) & 0xFFFF)
                    elif mode == "rell":
                        tgt = (here & 0xFF0000) | ((here + 3 + (opnd - 65536 if opnd & 0x8000 else opnd)) & 0xFFFF)
                    elif mode == "abs":
                        tgt = (here & 0xFF0000) | opnd
                    else:
                        tgt = opnd
                    t = rom.off(tgt)
                    if t is not None:
                        if t in entries and t != e:
                            calls.add(t)          # tail call into another function
                        else:
                            todo.append(t)
                    if mn in ("bra", "brl", "jmp", "jml"):
                        break
                if mn in TERMINATORS:
                    break
                o += n
        tag = "sub"
        for tname, pred in TAG_RULES:
            if pred(use):
                tag = tname
                break
        kind = tr.labels.get(e)
        if e in manual:
            name = manual[e]
        elif e in vector_labels:
            name = f"{vector_labels[e]}_{rom.addr(e):06X}"
        else:
            if tag == "sub" and kind == "jt":
                tag = "state"
            elif tag == "sub" and kind == "dyn":
                tag = "handler"
            name = f"{tag}_{rom.addr(e):06X}"
        m8, x8, _ = tr.inst[e]
        funcs[e] = {"name": name, "tag": tag, "body": body, "calls": calls, "m8": m8, "x8": x8,
                    "use": sorted(use["rw"]), "manual": e in manual}
    return funcs


def write_functions(funcs, rom, labels_by_off, path):
    with open(path, "w") as f:
        f.write("# Function list for the recompiler. One block per function entry point.\n")
        f.write("# ADDR NAME  entry-widths  instructions  [manual]\n#   calls: ...\n#   uses:  ...\n\n")
        for e in sorted(funcs):
            fn = funcs[e]
            f.write(f"{rom.addr(e):06X} {fn['name']}  {'M8' if fn['m8'] else 'M16'} {'X8' if fn['x8'] else 'X16'}"
                    f"  {len(fn['body'])} instrs{'  [manual]' if fn['manual'] else ''}\n")
            if fn["calls"]:
                names = [funcs[c]["name"] if c in funcs else f"${rom.addr(c):06X}" for c in sorted(fn["calls"])]
                f.write("  calls: " + " ".join(names) + "\n")
            if fn["use"]:
                f.write("  uses:  " + " ".join(x.lstrip("!") for x in fn["use"]) + "\n")


def fmt_operand(mn, mode, opnd, n, here, m8, x8, labels_by_addr):
    h2 = f"${opnd:02X}"
    h4 = f"${opnd:04X}"
    h6 = f"${opnd:06X}"
    if mode == "imp":
        return mn
    if mode == "acc":
        return f"{mn} a"
    if mode in ("immm", "immx"):
        return f"{mn}.b #{h2}" if n == 2 else f"{mn}.w #{h4}"
    if mode == "imm8":
        return f"{mn} #{h2}"
    dpn = ("!" + RAM[opnd]) if opnd in RAM else h2    # D is always 0 in this game
    if mode == "dp":    return f"{mn}.b {dpn}"
    if mode == "dpx":   return f"{mn}.b {dpn},x"
    if mode == "dpy":   return f"{mn}.b {dpn},y"
    if mode == "dpi":   return f"{mn} ({h2})"
    if mode == "dpxi":  return f"{mn} ({h2},x)"
    if mode == "dpiy":  return f"{mn} ({h2}),y"
    if mode == "dpil":  return f"{mn} [{h2}]"
    if mode == "dpily": return f"{mn} [{h2}],y"
    if mode == "sr":    return f"{mn} {h2},s"
    if mode == "sriy":  return f"{mn} ({h2},s),y"
    if mode == "abs":
        if mn in ("jsr", "jmp"):
            tgt = (here & 0xFF0000) | opnd
            lab = labels_by_addr.get(tgt)
            return f"{mn} {lab}" if lab else f"{mn}.w {h4}"
        return f"{mn}.w {sym_abs(opnd) or h4}"
    if mode == "absx":  return f"{mn}.w {sym_abs(opnd) or h4},x"
    if mode == "absy":  return f"{mn}.w {sym_abs(opnd) or h4},y"
    if mode == "absi":  return f"{mn} ({h4})"
    if mode == "absxi": return f"{mn} ({h4},x)"
    if mode == "absil": return f"{mn} [{h4}]"
    if mode == "long":
        if mn in ("jsl", "jml"):
            lab = labels_by_addr.get(opnd)
            return f"{mn} {lab}" if lab else f"{mn}.l {h6}"
        return f"{mn}.l {sym_long(opnd) or h6}"
    if mode == "longx": return f"{mn}.l {sym_long(opnd) or h6},x"
    if mode in ("rel", "rell"):
        if mode == "rel":
            tgt = (here & 0xFF0000) | ((here + 2 + (opnd - 256 if opnd & 0x80 else opnd)) & 0xFFFF)
        else:
            tgt = (here & 0xFF0000) | ((here + 3 + (opnd - 65536 if opnd & 0x8000 else opnd)) & 0xFFFF)
        lab = labels_by_addr.get(tgt)
        if lab:
            return f"{mn} {lab}"
        # target is not a traced instruction: emit raw bytes to stay exact
        raw = opnd.to_bytes(n - 1, "little")
        return "db " + ",".join(f"${x:02X}" for x in bytes([OPCODE_OF[(mn, mode)]]) + raw) + f"   ; {mn} ${tgt:06X}"
    if mode == "mv":
        dst, src = opnd & 0xFF, opnd >> 8
        return f"{mn} ${dst:02X},${src:02X}"   # asar: operands in byte order
    raise ValueError(mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bank-base", type=lambda s: int(s, 0), default=0x80)
    ap.add_argument("--entries", default=None, help="extra entry points file: ADDR [m8 x8]")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config"))
    ap.add_argument("--coverage", default=None,
                    help="covrun coverage file: executed instruction starts with observed M/X widths")
    a = ap.parse_args()

    load_symbols(a.config)
    data = open(a.rom, "rb").read()
    rom = Rom(data, a.bank_base)
    tr = Tracer(rom)

    dyn_seeds = []
    multi_width = []
    if a.coverage:
        cov = open(a.coverage, "rb").read()
        # pass 1: claim every executed instruction exactly as executed,
        # without following control flow (so a wrong static guess can never
        # claim bytes the hardware actually ran as a different instruction)
        for o, b in enumerate(cov):
            w = b & 0x0F
            if not w:
                continue
            bits = [i for i in range(4) if w >> i & 1]
            m8, x8 = bits[-1] < 2, bits[-1] % 2 == 0      # widest observed width
            mn, mode = OPS[data[o]]
            if len(bits) > 1 and (mode == "immm" and len({i < 2 for i in bits}) > 1
                                  or mode == "immx" and len({i % 2 == 0 for i in bits}) > 1):
                multi_width.append(rom.addr(o))
                # pick the width whose next instruction is also known to have run
                for bi in sorted(bits):          # tie: prefer the narrower width
                    bm, bx = bi < 2, bi % 2 == 0
                    nxt = o + 1 + operand_size(mode, bm, bx)
                    if nxt < len(cov) and cov[nxt] & 0x0F:
                        m8, x8 = bm, bx
                        break
            dyn_seeds.append((o, m8, x8))
        for o, m8, x8 in dyn_seeds:
            tr.forced[o] = (m8, x8)
            tr.work.append((o, m8, x8, ()))
        tr.run()

    vec = struct.unpack_from("<16H", data, 0x7FE0)
    names = ["", "", "COP", "BRK", "ABORT", "NMI", "", "IRQ",
             "", "", "eCOP", "", "eABORT", "eNMI", "RESET", "eIRQ"]
    vector_labels = {}
    for i, v in enumerate(vec):
        if names[i] and v >= 0x8000:
            # native vectors run with whatever widths were active; assume 8-bit
            ad = (a.bank_base << 16) | v
            tr.add_entry(ad, True, True, "vec")
            vector_labels.setdefault(rom.off(ad), names[i])
    if a.entries and os.path.exists(a.entries):
        for line in open(a.entries):
            line = line.split("#")[0].split()
            if not line:
                continue
            ad = int(line[0], 16)
            m8 = line[1] != "16" if len(line) > 1 else True
            x8 = line[2] != "16" if len(line) > 2 else True
            tr.add_entry(ad, m8, x8, "sub")
    tr.run()
    # dynamic entry points that are not plain fall-through get a label
    for o, _, _ in dyn_seeds:
        if o in tr.inst and o not in tr.labels:
            prev = tr.owner.get(o - 1)
            if prev is None or prev + tr.inst[prev][2] != o or OPS[data[prev]][0] in TERMINATORS:
                tr.labels[o] = "dyn"      # reached some way other than falling through

    dyn_offsets = {o for o, _, _ in dyn_seeds}

    # ---- labels
    labels_by_off = {}
    for o, kind in tr.labels.items():
        if o not in tr.inst:
            continue
        ad = rom.addr(o)
        if o in vector_labels:
            labels_by_off[o] = f"{vector_labels[o]}_{ad:06X}"
        else:
            prefix = {"sub": "sub", "vec": "vec", "jt": "jt", "loc": "L", "dyn": "code"}[kind]
            labels_by_off[o] = f"{prefix}_{ad:06X}"
    # ---- functions: entry points, bodies, calls, behaviour tags, names
    os.makedirs(a.out, exist_ok=True)
    funcs = analyse_functions(tr, rom, data, labels_by_off, vector_labels, a.config)
    for o, f in funcs.items():
        labels_by_off[o] = f["name"]
    labels_by_addr = {rom.addr(o): n for o, n in labels_by_off.items()}
    write_functions(funcs, rom, labels_by_off, os.path.join(a.out, "functions.txt"))
    # mirror lookups ($00xxxx <-> $80xxxx) must NOT resolve to labels, because
    # the label would re-encode the other mirror's bank byte.

    os.makedirs(a.out, exist_ok=True)
    codemap = open(os.path.join(a.out, "codemap.txt"), "w")
    main_asm = [
        "; Mighty Morphin Power Rangers: The Movie (USA) - generated disassembly",
        "; Regenerate with tools/disasm.py; do not hand-edit generated banks yet.",
        "lorom",
        "check bankcross off",
        "incsrc \"symbols.asm\"",
        "",
    ]
    with open(os.path.join(a.out, "symbols.asm"), "w") as f:
        f.write("; hardware registers (config/hardware.sym) and RAM variables (config/ram.sym)\n")
        for addr, nm in sorted(HW.items()):
            f.write(f"!{nm} = ${addr:04X}{'':<6}; {SYMDESC.get(nm, '')}\n")
        f.write("\n; RAM (offsets into WRAM; use $7E0000+!name for long addressing)\n")
        for addr, nm in sorted(RAM.items()):
            f.write(f"!{nm} = ${addr:04X}{'':<6}; {SYMDESC.get(nm, '')}\n")
    for b in range(rom.nbanks):
        fn = f"bank_{rom.bank_base + b:02X}.asm"
        main_asm.append(f"incsrc \"{fn}\"")
        lines = [f"org ${rom.bank_base + b:02X}8000", ""]
        o = b * BANK
        end = o + BANK
        pending = []

        def flush():
            for i in range(0, len(pending), 16):
                chunk = pending[i:i+16]
                lines.append("    db " + ",".join(f"${x:02X}" for x in chunk))
            pending.clear()

        while o < end:
            if o in tr.inst:
                flush()
                m8, x8, n = tr.inst[o]
                ad = rom.addr(o)
                if o in labels_by_off:
                    if labels_by_off[o].startswith(("sub_", "vec_", "jt_", "code_")) or o in vector_labels:
                        lines.append("")
                    lines.append(f"{labels_by_off[o]}:")
                op = data[o]
                mn, mode = OPS[op]
                opnd = int.from_bytes(data[o+1:o+n], "little")
                text = fmt_operand(mn, mode, opnd, n, ad, m8, x8, labels_by_addr)
                if mn in ("brk", "cop", "wdm"):
                    text = "db " + ",".join(f"${x:02X}" for x in data[o:o+n]) + f"   ; {mn}"
                flags = ("M8" if m8 else "M16") + (" X8" if x8 else " X16")
                lines.append(f"    {text:<28}; {ad:06X} {flags}")
                codemap.write(f"{ad:06X} {flags:<7} {data[o:o+n].hex():<8} {text:<40} {'ran' if o in dyn_offsets else 'static'}\n")
                o += n
            else:
                if o in tr.tables:
                    flush()
                    cnt, kind = tr.tables[o]
                    lines.append(f"tbl_{rom.addr(o):06X}:        ; {cnt}-entry {kind} table")
                    for i in range(cnt):
                        w = struct.unpack_from("<H", data, o + 2*i)[0]
                        tgt = (rom.addr(o) & 0xFF0000) | w
                        lab = labels_by_addr.get(tgt)
                        lines.append(f"    dw {lab if lab else f'${w:04X}'}")
                    o += 2 * cnt
                    continue
                pending.append(data[o])
                o += 1
                if len(pending) >= 16:
                    flush()
        flush()
        with open(os.path.join(a.out, fn), "w") as f:
            f.write("\n".join(lines) + "\n")
    codemap.close()
    with open(os.path.join(a.out, "main.asm"), "w") as f:
        f.write("\n".join(main_asm) + "\n")

    # ---- report
    code_bytes = sum(n for _, _, n in tr.inst.values())
    table_bytes = len(tr.data_marks)
    with open(os.path.join(a.out, "labels.txt"), "w") as f:
        for o in sorted(labels_by_off):
            f.write(f"{rom.addr(o):06X} {labels_by_off[o]}\n")
    per_bank = collections.Counter(o // BANK for o in tr.owner)
    with open(os.path.join(a.out, "report.txt"), "w") as f:
        f.write(f"ROM size: {len(data)} bytes, {rom.nbanks} LoROM banks\n")
        f.write(f"Instructions traced: {len(tr.inst)}\n")
        f.write(f"Code bytes: {code_bytes} ({100*code_bytes/len(data):.2f}% of ROM)\n")
        f.write(f"Jump-table bytes: {table_bytes} in {len(tr.tables)} tables\n")
        f.write(f"Labels: {len(labels_by_off)} (subs: {sum(1 for v in labels_by_off.values() if v.startswith('sub_'))})\n\n")
        f.write("Code bytes per bank:\n")
        for b in range(rom.nbanks):
            f.write(f"  ${rom.bank_base + b:02X}: {per_bank.get(b, 0):6d}\n")
        if a.coverage:
            dyn = sum(1 for o, _, _ in dyn_seeds)
            f.write(f"Instructions seen executing in emulator: {dyn}\n")
            f.write(f"Instructions found only by static tracing: {len(tr.inst) - dyn}\n")
            f.write(f"\nImmediate operands executed at BOTH widths ({len(multi_width)}) - need manual handling:\n")
            for ad in multi_width:
                f.write(f"  {ad:06X}\n")
        f.write(f"\nUnresolved indirect jumps ({len(tr.unresolved)}):\n")
        for ad, t in sorted(set(tr.unresolved)):
            f.write(f"  {ad:06X} {t}\n")
        f.write(f"\nFlag-width conflicts / trace stops ({len(tr.conflicts)}):\n")
        def fl(st):
            return "M8 X8" if st == (True, True) else ("M%d X%d" % (8 if st[0] else 16, 8 if st[1] else 16))
        for ad, a1, a2 in sorted(set(tr.conflicts), key=lambda c: c[0]):
            if a2 is None:
                f.write(f"  {ad:06X} trace stopped: {a1}\n")
            else:
                f.write(f"  {ad:06X} reached as {fl(a2)} but already traced as {fl(a1)}\n")
    print(open(os.path.join(a.out, "report.txt")).read()[:1500])


if __name__ == "__main__":
    main()
