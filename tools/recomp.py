#!/usr/bin/env python3
"""Static recompiler: 65816 -> C.

Reads the verified disassembly products (asm/codemap.txt, asm/functions.txt)
and writes one C function per game function into gen/.

Model
-----
* All CPU state lives in the emulator core's Cpu struct (C->a, C->x, ... flags).
* Each instruction keeps the exact bus behaviour of the reference interpreter
  (same reads/writes/idle cycles in the same order), so the PPU, DMA, sound and
  interrupt timing stay identical. Operand semantics come from the core's
  cpu_doOpcode() for that one opcode; the recompiler owns all control flow:
  branches become goto, JSR/JSL become C calls, RTS/RTL/RTI become return.
* Anything that can't be proven statically (indirect jumps to unknown places,
  register widths that differ from the ones observed, a callee that returns
  somewhere unexpected, code running from RAM) falls back to the reference
  interpreter until the current function returns. Correctness never depends
  on the disassembly being perfect; speed does.

usage: recomp.py ROM ASMDIR OUTDIR
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opcodes import OPS  # noqa: E402

BRANCH_COND = {0x10: "!C->n", 0x30: "C->n", 0x50: "!C->v", 0x70: "C->v",
               0x90: "!C->c", 0xB0: "C->c", 0xD0: "!C->z", 0xF0: "C->z"}


def off(ad):
    return ((ad >> 16) & 0x7F) * 0x8000 + (ad & 0xFFFF) - 0x8000


def norm(ad):
    bank = ad >> 16
    if (bank & 0x7F) < 0x40 and (ad & 0xFFFF) >= 0x8000:
        bank |= 0x80
    return (bank << 16) | (ad & 0xFFFF)


def main():
    rom_path, asmdir, outdir = sys.argv[1:4]
    rom = open(rom_path, "rb").read()
    inst = {}                                   # addr24 -> (m8, x8, n)
    for line in open(os.path.join(asmdir, "codemap.txt")):
        p = line.split()
        if len(p) < 4:
            continue
        inst[int(p[0], 16)] = (p[1] == "M8", p[2] == "X8", len(p[3]) // 2)
    funcs = {}                                  # addr24 -> name
    for line in open(os.path.join(asmdir, "functions.txt")):
        m = re.match(r"^([0-9A-F]{6}) (\S+)", line)
        if m:
            funcs[int(m.group(1), 16)] = m.group(2)
    cname = {a: f"f_{a:06X}" for a in funcs}

    def opnd_of(a, n):
        o = off(a)
        return int.from_bytes(rom[o + 1:o + n], "little")

    def branch_target(a, op, n):
        v = opnd_of(a, n)
        if n == 2:
            d = v - 256 if v & 0x80 else v
        else:
            d = v - 65536 if v & 0x8000 else v
        return (a & 0xFF0000) | ((a + n + d) & 0xFFFF)

    def body_of(entry):
        body, todo = set(), [entry]
        while todo:
            a = todo.pop()
            while a in inst and a not in body and (a == entry or a not in funcs):
                body.add(a)
                _, _, n = inst[a]
                op = rom[off(a)]
                mn, mode = OPS[op]
                if op in BRANCH_COND or op in (0x80, 0x82):
                    todo.append(branch_target(a, op, n))
                    if op in (0x80, 0x82):
                        break
                elif op == 0x4C:
                    todo.append((a & 0xFF0000) | opnd_of(a, n))
                    break
                elif mn in ("rts", "rtl", "rti", "jml", "stp") or op in (0x6C, 0x7C):
                    break
                a = (a & 0xFF0000) | ((a + n) & 0xFFFF)
        return body

    os.makedirs(outdir, exist_ok=True)
    owner = {}                                  # instruction addr -> a function whose body has it
    stats = {"funcs": 0, "insts": 0, "guards": 0}

    def target_code(t, body, entry):
        """Code that continues execution at static address t."""
        t = norm(t)
        if t in body:
            return f"goto L_{t:06X};"
        if t in funcs:
            return f"{{ {cname[t]}(); return; }}"
        return "{ rt_interpret_until(spe); return; }"

    def gen_function(entry):
        body = body_of(entry)
        out = [f"void {cname[entry]}(void) {{  /* {funcs[entry]} */",
               "  const uint16_t spe = C->sp; const uint8_t kk = C->k; (void)kk;"]
        if entry not in body:
            out.append("  rt_interpret_until(spe); }")
            return out
        # resume support: start at any instruction of this body (used after a
        # save-state load, and when the interpreter hands control back mid-function)
        out.append("  if (rt_resume_pc) { switch (rt_take_resume()) {")
        for a in sorted(body):
            out.append(f"    case 0x{a:06X}: goto L_{a:06X};")
        out.append("    default: rt_interpret_until(spe); return; } }")
        for a in body:
            owner.setdefault(a, entry)
        out.append(f"  goto L_{entry:06X};")
        for a in sorted(body):
            m8, x8, n = inst[a]
            op = rom[off(a)]
            mn, mode = OPS[op]
            a16 = a & 0xFFFF
            nxt = (a & 0xFF0000) | ((a + n) & 0xFFFF)
            out.append(f" L_{a:06X}: INS(0x{a16:04X});")
            if mode == "immm":
                out.append(f"  if (C->mf != {1 if m8 else 0}) {{ C->pc = 0x{a16:04X}; rt_interpret_until(spe); return; }}")
                stats["guards"] += 1
            elif mode == "immx":
                out.append(f"  if (C->xf != {1 if x8 else 0}) {{ C->pc = 0x{a16:04X}; rt_interpret_until(spe); return; }}")
                stats["guards"] += 1
            out.append("  TRACE(); FETCH();")
            fall = True
            if op in BRANCH_COND:
                t = branch_target(a, op, n)
                out.append(f"  {{ bool t = {BRANCH_COND[op]}; cpu_doBranch(C, t); if (t) {target_code(t, body, entry)} }}")
            elif op in (0x80, 0x82):
                out.append(f"  cpu_doOpcode(C, 0x{op:02X}); {target_code(branch_target(a, op, n), body, entry)}")
                fall = False
            elif op == 0x4C:
                out.append(f"  cpu_doOpcode(C, 0x4C); {target_code((a & 0xFF0000) | opnd_of(a, n), body, entry)}")
                fall = False
            elif op == 0x5C:
                t = norm(opnd_of(a, n))
                code = f"{{ {cname[t]}(); return; }}" if t in funcs else "RT_JUMP();"
                out.append(f"  cpu_doOpcode(C, 0x5C); {code}")
                fall = False
            elif op in (0x6C, 0x7C, 0xDC):
                out.append(f"  cpu_doOpcode(C, 0x{op:02X}); RT_JUMP();")
                fall = False
            elif op in (0x20, 0x22, 0xFC, 0x00, 0x02):
                if op == 0x20:
                    t = norm((a & 0xFF0000) | opnd_of(a, n))
                elif op == 0x22:
                    t = norm(opnd_of(a, n))
                else:
                    t = None
                call = f"{cname[t]}();" if t in funcs else "rt_call_pc();"
                out.append(f"  cpu_doOpcode(C, 0x{op:02X}); {call}")
                out.append(f"  if (C->pc != 0x{nxt & 0xFFFF:04X} || C->k != kk) {{ rt_interpret_until(spe); return; }}")
            elif op in (0x60, 0x6B, 0x40):
                out.append(f"  cpu_doOpcode(C, 0x{op:02X}); return;")
                fall = False
            elif op in (0x44, 0x54):     # MVP/MVN move one byte per execution and repeat
                out.append(f"  cpu_doOpcode(C, 0x{op:02X}); if (C->pc == 0x{a16:04X}) goto L_{a:06X};")
            elif op == 0xCB:
                out.append("  cpu_doOpcode(C, 0xCB); rt_wait();")
            elif op == 0xDB:
                out.append("  cpu_doOpcode(C, 0xDB); rt_stop();")
                fall = False
            else:
                out.append(f"  cpu_doOpcode(C, 0x{op:02X});")
            if fall and nxt not in body:
                out.append(f"  {target_code(nxt, body, entry)}")
            stats["insts"] += 1
        out.append("}")
        stats["funcs"] += 1
        return out

    # write functions grouped by bank
    by_bank = {}
    for e in sorted(funcs):
        by_bank.setdefault(e >> 16, []).append(e)
    header = ['#include "rt.h"', '#include "funcs.h"', ""]
    for bank, entries in sorted(by_bank.items()):
        lines = list(header)
        for e in entries:
            lines += gen_function(e)
            lines.append("")
        with open(os.path.join(outdir, f"recomp_{bank:02X}.c"), "w") as f:
            f.write("\n".join(lines) + "\n")
    with open(os.path.join(outdir, "funcs.h"), "w") as f:
        f.write("#pragma once\n")
        for e in sorted(funcs):
            f.write(f"void {cname[e]}(void);  /* {funcs[e]} */\n")
    with open(os.path.join(outdir, "dispatch.c"), "w") as f:
        f.write('#include "rt.h"\n#include "funcs.h"\n\nconst RecompEntry rt_table[] = {\n')
        for e in sorted(funcs):
            f.write(f"  {{0x{e:06X}, {cname[e]}}},\n")
        f.write(f"}};\nconst int rt_table_size = {len(funcs)};\n")
    with open(os.path.join(outdir, "resume.c"), "w") as f:
        f.write('#include "rt.h"\n#include "funcs.h"\n\n/* every compiled instruction -> a function that can resume there */\n')
        f.write("const RecompEntry rt_resume_table[] = {\n")
        for a in sorted(owner):
            f.write(f"  {{0x{a:06X}, {cname[owner[a]]}}},\n")
        f.write(f"}};\nconst int rt_resume_table_size = {len(owner)};\n")
    print(f"{stats['funcs']} functions, {stats['insts']} instructions emitted, {stats['guards']} width guards")


if __name__ == "__main__":
    main()
