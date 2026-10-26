#!/usr/bin/env python3
"""OR-merge coverage files: mergecov.py OUT IN1 [IN2 ...]"""
import sys
out, ins = sys.argv[1], sys.argv[2:]
d = bytearray(open(ins[0], "rb").read())
for p in ins[1:]:
    for i, b in enumerate(open(p, "rb").read()):
        d[i] |= b
open(out, "wb").write(d)
print(f"{sum(1 for b in d if b)} instruction starts covered")
