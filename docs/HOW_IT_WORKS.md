# How it works

This page goes deeper than the README: the ROM, the naming work, the recompiler and the PC frontend.

## The pipeline

1. **Coverage.** The game was played (by people and by bots) in an instrumented emulator that records, for every ROM byte, whether an instruction started there and with which 8/16-bit register widths. The result is `coverage/all.cov`. It's flags only, no game bytes.
2. **Disassembly.** `tools/disasm.py`:
   - Trusts the recorded instructions first, then traces statically from them and from the hardware vectors.
   - Adds hand-confirmed entry points from `config/entrypoints.txt`.
   - Labels every instruction `ran` or `static` in `build/asm/codemap.txt`.
   - Produces asar source that reassembles to the exact original ROM.
3. **Recompilation.** `tools/recomp.py` turns each function into C (below).
4. **Build.** gcc compiles the generated C together with the runtime and the SNES chip emulation.

## The ROM

| Field | Value |
|---|---|
| Internal title | `POWER RANGERS MOVIE` |
| Region / version | USA, v1.0 |
| Size | 1,572,864 bytes (12 Mbit), no copier header |
| Mapping | LoROM, SlowROM (map mode `$20`), 48 banks |
| Cartridge | ROM only. No save RAM and no enhancement chip (no SuperFX, SA-1 or DSP) |
| Checksum | `$F20B` / complement `$0DF4`, valid |
| SHA-1 | `82fbe365bc96fe3f03c3eb12e1620910b15f01f9` |
| CRC32 | `E3EF6201` |
| Vectors | RESET `$00:8000`, NMI `$00:8421`, IRQ `$00:8B5C`, BRK `$00:FA96` |

There's no coprocessor, so only two things need to run on PC: the 65816 CPU code, which gets recompiled, and the PPU/APU hardware, which gets emulated.

## Names

The code now uses names instead of raw numbers wherever we know what a number means. The disassembly still reassembles byte-identically (with [asar](https://github.com/RPGHacker/asar)).

- **Hardware registers:** all SNES PPU, DMA, joypad, math and sound-port registers, e.g. `sta.w !INIDISP` instead of `sta.w $2100`.
- **RAM:** these were confirmed by poking values into memory and watching the game react:

| Name | Address | Meaning |
|---|---|---|
| `player_health` | `$0628` | health segments 0–5 (P2 at `$062C`; player variables are 4 bytes apart) |
| `player_lives` | `$060A` | the "×2" on the HUD |
| `player_score` | `$0610` | BCD score (`$1234` shows as 1234) |
| `credits` | `$0602` | the "CREDIT-4" counter |
| `joy1_held` / `joy1_pressed` | `$0182` / `$0184` | controller 1 buttons held / newly pressed |
| `nmi_frame_count`, `frame_count` | `$0C`, `$0244` | count up every frame |
| `obj_*` | `$0F00`–`$16DF` | the object system (below) |

- **Objects:** enemies, the player and effects live in 36 slots (X = slot × 4). Each property sits in its own table: `obj_active` (`$0F00`), `obj_x` (`$1020`, 16.8 fixed point; whole pixels at `$1021`), `obj_y` (`$10B0` / `$10B1`), `obj_frame_ptr` (`$1680`). Tables whose purpose we haven't pinned down are named by address, e.g. `obj_0F90`.
- **Functions:** all 2,745 entry points have a name that describes what they do, based on what they touch:

| Prefix | What it does |
|---|---|
| `dma_`, `dmacfg_`, `hdma_` | moves data to the PPU |
| `sound_` | talks to the sound CPU |
| `input_` | reads the controllers |
| `vram_` / `palette_` / `oam_` / `ppu_` | graphics |
| `math_` | uses the hardware multiplier/divider |
| `player_` | player stats |
| `obj_` | object logic |
| `state_` | entries in a jump table (state machines) |
| `handler_` | script commands reached through the interpreters |
| `sub_` | not classified yet |

  Hand-picked names (`reset`, `nmi_handler`, `read_joypads`, `script_dispatch_A`–`E`, …) live in `config/names.txt`.
- `build/asm/functions.txt` is the recompiler's input: every function, its register widths on entry, its size, what it calls, and which named hardware/RAM it touches.

## Recompiler

`tools/recomp.py` turns every function in `build/asm/functions.txt` into a C function in `build/gen/` (2,745 functions, 46,623 instructions).

- **Control flow is native.** Branches become `goto`, `JSR`/`JSL` become C calls, and `RTS`/`RTL`/`RTI` become `return`. Jump tables and other computed jumps go through an address → function table (`build/gen/dispatch.c`).
- **Each instruction keeps the exact bus behaviour of the reference interpreter.** It does the same reads, writes and idle cycles in the same order, so graphics, DMA, sound and interrupt timing stay identical. The operand work reuses the emulator core's implementation of that opcode (`cpu_doOpcode` with the opcode fixed at compile time).
- **Safety net.** If something can't be proven ahead of time, the runtime hands that piece to the reference interpreter. That covers register widths that differ from the ones observed, a callee returning somewhere unexpected, a jump to an unknown address, and code running from RAM. The interpreter gives control back to native code at the next function start. Correctness never depends on the disassembly being perfect.
- **The emulated parts.** `runtime/snes/` is the LakeSnes core (MIT), with its CPU interpreter kept only as that safety net. PPU, DMA, SPC700 and DSP are still emulated.

### Verified

`build/mmpr_test interp|recomp` (built with `python build.py ROM --test`) runs the game headless with scripted input and logs a checksum of all WRAM, VRAM, palette and OAM at every frame.

- **16,000 frames identical.** That's about 4½ minutes of boot, title, character select, Stage 1 gameplay, deaths, continue, Game Over and back to the title. Every frame's checksums match the interpreter.
- **99.8% native.** 195.9M instructions ran natively and 0.39M were interpreted. Most of the interpreted ones are a 2-instruction "jump to address in A" trampoline the game keeps in RAM at `$1F00`.

### Full-game verification

A complete recorded playthrough (`tests/movies/full_playthrough.inp`, 169,285 frames, 47 minutes) runs from power-on through every stage to the ending credits. Replayed on the reference interpreter and on the recompiled code, **all 169,286 frames are identical**: WRAM, VRAM, palette and OAM every frame. 2,025M instructions ran natively and 6.4M (0.3%) were interpreted, almost all of them the game's RAM trampoline at `$1F00`.

## PC frontend

- **Remappable controls.** `settings.ini` covers keyboard (plus an optional second key per button), gamepad and hotkeys, plus window scale, fullscreen and smooth/sharp scaling.
- **Save states:** 10 slots (F5 save, F9 load, F6/F7 to change slot).
  - **How they work in a recompiled game.** The request waits for the next instruction boundary, so the machine state is complete (`rt_pending` → `rt_boundary_hook`). Loading discards the native C call stack with `longjmp` and continues from the restored CPU state via `rt_resume()`. Every function can start at any of its instructions (`rt_resume_pc` + a per-function `switch`), so execution is native again right away.
  - **Verified.** Save → load mid-game → continue gives frame-identical results to never loading. A movie started from a mid-game state replays identically on both interpreter and recompiled code.
- **Pause, fast-forward, fullscreen.**
- **Rewind (hold R).** A full machine snapshot is taken every 6 frames at an instruction boundary, into a ring buffer (default 20 s, about 2.6 MB per second). Holding R loads one snapshot per frame, which plays back at roughly 5× speed. Recording pauses while rewinding and starts a new movie from the resulting state on release.
- **Reset (F8)**, **screenshots (F12**, 1024×896 PNG via stb_image_write), and video options: integer scaling, 4:3 TV aspect, and scanlines (0–100).
- **No sprite-limit hack.** The game hits the SNES's 32-sprite / 34-tile per-line limit in only 1 frame of 8,000 tested, so it isn't needed.
- **Quick taps aren't lost.** A button pressed and released between two frames still counts for one frame.
- **Input recording.** Every session writes `movies/movie_*.inp`: a header, the save state it started from (none = power-on), then one 16-bit button mask per frame. Any human play session can be checked with:

  ```sh
  tools/verify_movie.sh ROM path/to/movie.inp     # interpreter vs recompiled, every frame
  ```

