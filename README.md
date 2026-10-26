# Mighty Morphin Power Rangers: The Movie — PC

A native PC version of the 1995 Super Nintendo game *Mighty Morphin Power Rangers: The Movie*, made by **statically recompiling** the game's 65816 machine code into C.

- **The game's code runs as native code.** The SNES graphics (PPU) and sound (SPC700/DSP) chips are reproduced in software.
- **Verified frame-identical.** A complete 47-minute playthrough, from power-on to the ending credits, was replayed on this port and on a reference SNES emulator. Every one of its 169,286 frames matched: all memory, graphics, palette and sprite data.
- **PC extras:**
  - keyboard and two controllers, fully remappable
  - save states, rewind, fast forward
  - screenshots, scanlines, 4:3 / integer scaling
  - optional infinite health

> **No game data is included in this repository.** You need your own copy of the game. The build script reads your ROM and generates everything from it on your machine. Unofficial fan project; not affiliated with or endorsed by Bandai, Saban, Hasbro or Nintendo.

## Building

You need:

1. **Your ROM:** `Mighty Morphin Power Rangers - The Movie (USA).sfc` (CRC32 `E3EF6201`; a 512-byte copier header is fine).
2. **Python 3.8 or newer:** [python.org](https://www.python.org/downloads/).
3. **A C compiler (gcc):**
   - **Windows:** download [w64devkit](https://github.com/skeeto/w64devkit/releases), unzip it, run `w64devkit.exe`, and use that window for the command below. SDL2 is downloaded automatically.
   - **Linux:** `gcc` and SDL2 development files, e.g. `sudo apt install build-essential libsdl2-dev`.

Then, from this folder:

```sh
python build.py "path/to/Mighty Morphin Power Rangers - The Movie (USA).sfc"
```

The build takes a few minutes, mostly compiling the large generated files. The game ends up in `dist/`. Put your ROM next to the `.exe` (or drag the ROM onto it) and play. Controls and options are in [PLAYING.txt](PLAYING.txt).

## How it works

```
your ROM ──► tools/disasm.py ──► build/asm/ ──► tools/recomp.py ──► build/gen/*.c ──► gcc ──► dist/
             (traces code using             (one C function per game
              config/ + coverage/)           function, 46,623 instructions)
```

- **`tools/disasm.py`** is a tracing 65816 disassembler. It follows code from the reset and interrupt vectors, guided by:
  - `coverage/all.cov`: which instructions really ran, and at what register widths, recorded from human and bot playthroughs.
  - `config/`: names for hardware registers, RAM variables and functions.

  Its output reassembles to a byte-identical ROM.
- **`tools/recomp.py`** turns each of the game's 2,745 functions into a C function:
  - Branches become `goto`, calls become C calls, and returns become `return`.
  - Every instruction keeps the exact memory and timing behaviour of the original CPU, so graphics, sound and interrupts stay cycle-identical.
- **`runtime/`** holds:
  - the support code (`rt.c`)
  - the SNES hardware emulation (`runtime/snes/`, based on [LakeSnes](https://github.com/angelo-wf/LakeSnes))
  - the SDL2 frontend (`main_sdl.c`)
  - a headless test driver (`main_test.c`)
- **Safety net.** Anything that can't be decided ahead of time is handed to the reference interpreter until the current function returns. In the full playthrough that was 0.3% of instructions, nearly all of them a 2-instruction routine the game runs from RAM.

More detail: [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Verifying it yourself

```sh
python build.py path/to/rom.sfc --test
tools/verify_movie.sh build/rom.sfc tests/movies/full_playthrough.inp
```

`tests/movies/full_playthrough.inp` is a button recording of a complete run, using infinite health. `verify_movie.sh` replays it on both the reference interpreter and the recompiled code, compares a checksum of every frame, and reports the first difference if there is one. The PC build can record your own sessions too: set `record = 1` in `settings.ini`.

## Repository layout

| Path | What |
|---|---|
| `build.py` | one-command build: ROM → `dist/` |
| `tools/` | disassembler, recompiler, coverage and verification tools |
| `config/` | entry points, hardware/RAM symbol names, function names |
| `coverage/all.cov` | per-byte "this instruction ran at these widths" map (flags only, no game bytes) |
| `runtime/` | runtime, SNES chip emulation, PC frontend, test driver |
| `tests/movies/` | input recordings used for verification |
| `assets/` | optional icon (see [assets/README.md](assets/README.md)) |
| `build/`, `dist/`, `deps/` | created by the build; never committed |

## Credits

- **Port:** cultofblue, with Claude (Anthropic).
- **SNES chip emulation:** [LakeSnes](https://github.com/angelo-wf/LakeSnes) by angelo_wf (MIT).
- **Windowing, audio and input:** [SDL2](https://libsdl.org) (zlib).
- **PNG screenshots:** [stb_image_write](https://github.com/nothings/stb) (public domain / MIT).
- **The original game:** © 1995 Bandai / Saban Entertainment. All trademarks belong to their owners.

Licensing for this repository's own code: [LICENSE](LICENSE). Third-party notices: [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
