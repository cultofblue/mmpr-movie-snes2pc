#!/usr/bin/env python3
"""List unmapped regions in code banks that look like code that never ran.

For each gap that follows a terminating instruction (rts/rtl/rti/jmp/bra),
decode linearly with the register widths in effect at the end of the
previous instruction, and score it. Prints candidates for manual review;
it never adds anything to the disassembly by itself.

usage: find_candidates.py ROM CODEMAP [--banks 80-86]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from opcodes import OPS, operand_size  # noqa: E402

GOOD_FIRST = {"php", "phb", "phk", "phd", "pha", "phx", "phy", "rep", "sep", "lda", "ldx",
              "ldy", "jsr", "jsl", "stz", "sta", "pea", "sei", "clc", "sec", "tdc", "tax",
              "tay", "inc", "dec", "bit", "cmp", "and", "pei", "tcd", "plb"}
BAD = {"brk", "cop", "wdm", "stp", "xce"}
END = {"rts", "rtl", "rti"}


def off(ad):
    return ((ad >> 16) & 0x7F) * 0x8000 + (ad & 0xFFFF) - 0x8000


def main():
    rom = open(sys.argv[1], "rb").read()
    inst = {}
    for line in open(sys.argv[2]):
        p = line.split()
        if len(p) < 4:
            continue
        ad = int(p[0], 16)
        inst[off(ad)] = (p[1] == "M8", p[2] == "X8", len(p[3]) // 2, p[4])
    covered = set()
    for o, (_, _, n, _) in inst.items():
        covered.update(range(o, o + n))
    out = []
    for o, (m8, x8, n, mn) in sorted(inst.items()):
        if mn not in ("rts", "rtl", "rti", "jmp", "jml", "bra", "brl"):
            continue
        g = o + n
        if g in covered or g % 0x8000 == 0:
            continue
        # decode linearly
        p, score, count, ok, rep_m, rep_x = g, 0, 0, False, m8, x8
        first = OPS[rom[g]][0]
        if first in GOOD_FIRST:
            score += 2
        while count < 200 and p not in covered:
            mn2, mode = OPS[rom[p]]
            if mn2 in BAD:
                score -= 10
                break
            size = 1 + operand_size(mode, rep_m, rep_x)
            opnd = int.from_bytes(rom[p + 1:p + size], "little")
            if mn2 == "rep":
                rep_m = rep_m and not opnd & 0x20
                rep_x = rep_x and not opnd & 0x10
            if mn2 == "sep":
                rep_m = rep_m or bool(opnd & 0x20)
                rep_x = rep_x or bool(opnd & 0x10)
            if mode in ("abs", "absx", "absy") and mn2 not in ("jsr", "jmp"):
                if opnd < 0x2000 or 0x2100 <= opnd < 0x2200 or 0x4200 <= opnd < 0x4400:
                    score += 1
                elif 0x2200 <= opnd < 0x4200 or 0x4400 <= opnd < 0x8000:
                    score -= 2
            if mn2 == "jsr" and mode == "abs":
                score += 3 if off(0x800000 | (o // 0x8000) << 16 | opnd) in inst else -1
            count += 1
            p += size
            if mn2 in END:
                ok = True
                score += 3
                break
        if ok and score >= 6 and count >= 3:
            bank = 0x80 + g // 0x8000
            out.append((g, p - g, count, score, bank, m8, x8))
    total = sum(x[1] for x in out)
    print(f"{'# ' if '--entries' in sys.argv else ''}{len(out)} candidate routines, {total} bytes")
    for g, ln, cnt, sc, bank, m8, x8 in out:
        if "--entries" in sys.argv:
            print(f"{bank:02X}{0x8000 + g % 0x8000:04X} {8 if m8 else 16} {8 if x8 else 16}"
                  f"   # unverified static candidate: {cnt} instrs, score {sc}")
        else:
            print(f"  {bank:02X}{0x8000 + g % 0x8000:04X}  {ln:4d} bytes  {cnt:3d} instrs  score {sc}")


if __name__ == "__main__":
    main()
