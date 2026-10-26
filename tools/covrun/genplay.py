#!/usr/bin/env python3
"""Generate a semi-random beat-'em-up input script for covrun.
usage: genplay.py SECONDS SEED PREFIX [startstate]"""
import random, sys
secs, seed, prefix = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
start = sys.argv[4] if len(sys.argv) > 4 else None
r = random.Random(seed)
L = [f"load {start}"] if start else []
moves = ["RIGHT", "RIGHT", "RIGHT", "UP+RIGHT", "DOWN+RIGHT", "LEFT", "UP", "DOWN"]
attacks = ["Y", "B", "A", "X", "Y", "Y", "B+Y", "A+B", "L", "R"]
f = 0
shot = 0
while f < secs * 60:
    k = r.random()
    if k < 0.45:
        n = r.choice([10, 20, 40, 60])
        L.append(f"mash {n} {r.choice(moves)}+{r.choice(attacks)}")
    elif k < 0.75:
        n = r.choice([20, 40, 80])
        L.append(f"hold {n} {r.choice(moves)}")
    elif k < 0.9:
        n = r.choice([10, 30])
        L.append(f"mash {n} {r.choice(attacks)}")
    elif k < 0.96:
        L.append("tap START"); L.append("wait 20"); L.append("tap START")
        n = 30
    else:
        L.append(f"hold 30 {r.choice(attacks)}"); n = 30
    f += n
    if f // 600 > shot:
        shot = f // 600
        L.append(f"shot {prefix}_{shot:03d}")
L.append(f"save {prefix}_end")
print("\n".join(L))
