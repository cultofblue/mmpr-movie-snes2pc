#!/bin/sh
# Replay a recorded play session (.inp from the PC build's movies folder) on both
# the reference interpreter and the recompiled code, and compare every frame.
# usage: tools/verify_movie.sh ROM MOVIE.inp
set -e
cd "$(dirname "$0")/.."
ROM=$1; MOV=$2; OUT=build/verify; mkdir -p $OUT
NAME=$(basename "$MOV" .inp)
./build/mmpr_test interp "$ROM" "$MOV" ${FRAMES:-0} $OUT/$NAME.interp.log &
./build/mmpr_test recomp "$ROM" "$MOV" ${FRAMES:-0} $OUT/$NAME.recomp.log
wait
if cmp -s $OUT/$NAME.interp.log $OUT/$NAME.recomp.log; then
  echo "$NAME: IDENTICAL ($(wc -l < $OUT/$NAME.recomp.log) frames)"
else
  echo "$NAME: DIFFERS - first differing frame:"
  diff $OUT/$NAME.interp.log $OUT/$NAME.recomp.log | sed -n 2p
  exit 1
fi
