#!/usr/bin/env python3
"""Build Mighty Morphin Power Rangers: The Movie (PC) from your own ROM.

    python build.py "Mighty Morphin Power Rangers - The Movie (USA).sfc"

Steps:
  1. check the ROM is the USA version (CRC32 E3EF6201; a 512-byte copier header is fine)
  2. disassemble it            -> build/asm/   (tools/disasm.py)
  3. translate the code to C   -> build/gen/   (tools/recomp.py)
  4. compile everything        -> dist/MMPR_Movie_PC(.exe)

Nothing taken from the ROM is ever written outside build/ and dist/, and
both folders are git-ignored.

Options:
  --cc COMPILER   C compiler to use (default: gcc). For a Windows build from
                  Linux use --cc x86_64-w64-mingw32-gcc
  --sdl2 DIR      SDL2 development files (Windows: the x86_64-w64-mingw32 folder
                  of SDL2-devel-<ver>-mingw). Downloaded automatically if missing.
  --test          also build the headless test driver (build/mmpr_test)
  --jobs N        parallel compile jobs (default: number of CPUs)
"""
import argparse
import concurrent.futures
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.abspath(__file__))
EXPECTED_CRC = 0xE3EF6201
EXPECTED_SHA1 = "82fbe365bc96fe3f03c3eb12e1620910b15f01f9"
SDL_VER = "2.30.9"
SDL_URL = f"https://github.com/libsdl-org/SDL/releases/download/release-{SDL_VER}/SDL2-devel-{SDL_VER}-mingw.tar.gz"


def step(msg):
    print(f"\n== {msg}", flush=True)


def die(msg):
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd, **kw):
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        die("command failed: " + " ".join(cmd))


def load_rom(path):
    if not os.path.isfile(path):
        die(f"ROM not found: {path}")
    data = open(path, "rb").read()
    if len(data) % 1024 == 512:
        data = data[512:]                   # copier header
    crc = zlib.crc32(data) & 0xFFFFFFFF
    if crc != EXPECTED_CRC or hashlib.sha1(data).hexdigest() != EXPECTED_SHA1:
        die(f"this isn't the expected ROM (CRC32 {crc:08X}). It must be\n"
            f"  Mighty Morphin Power Rangers - The Movie (USA)  CRC32 {EXPECTED_CRC:08X}")
    return data


def is_windows_target(cc):
    return "mingw" in cc or platform.system() == "Windows"


def find_sdl2(args):
    if args.sdl2:
        return args.sdl2
    deps = os.path.join(ROOT, "deps")
    target = os.path.join(deps, f"SDL2-{SDL_VER}", "x86_64-w64-mingw32")
    if os.path.isdir(target):
        return target
    step(f"downloading SDL2 {SDL_VER} development files (one time)")
    os.makedirs(deps, exist_ok=True)
    tgz = os.path.join(deps, "sdl2.tar.gz")
    urllib.request.urlretrieve(SDL_URL, tgz)
    with tarfile.open(tgz) as t:
        try:
            t.extractall(deps, filter="data")
        except TypeError:                   # Python < 3.12
            t.extractall(deps)
    os.remove(tgz)
    return target


def compile_all(cc, sources, objdir, cflags, jobs):
    os.makedirs(objdir, exist_ok=True)

    def one(src):
        obj = os.path.join(objdir, os.path.relpath(src, ROOT).replace(os.sep, "_").replace("/", "_") + ".o")
        if os.path.exists(obj) and os.path.getmtime(obj) >= os.path.getmtime(src):
            return obj, None
        r = subprocess.run([cc] + cflags + ["-c", src, "-o", obj], capture_output=True, text=True)
        return obj, (r.stderr if r.returncode else None)

    objs = []
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        for i, (obj, err) in enumerate(ex.map(one, sources)):
            if err:
                die(f"compiling {sources[i]} failed:\n{err}")
            objs.append(obj)
            print(f"  compiled {i + 1}/{len(sources)}", end="\r", flush=True)
    print()
    return objs


def main():
    ap = argparse.ArgumentParser(description="Build the PC version from your ROM")
    ap.add_argument("rom")
    ap.add_argument("--cc", default="gcc")
    ap.add_argument("--sdl2")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    args = ap.parse_args()

    if shutil.which(args.cc) is None:
        die(f"C compiler '{args.cc}' not found. On Windows, install w64devkit "
            f"(https://github.com/skeeto/w64devkit) and run this from its shell.")

    build = os.path.join(ROOT, "build")
    dist = os.path.join(ROOT, "dist")
    os.makedirs(build, exist_ok=True)

    step("checking ROM")
    rom = load_rom(args.rom)
    rom_path = os.path.join(build, "rom.sfc")
    open(rom_path, "wb").write(rom)
    print("  OK: Mighty Morphin Power Rangers - The Movie (USA)")

    py = sys.executable
    step("disassembling (tools/disasm.py)")
    run([py, os.path.join(ROOT, "tools", "disasm.py"), rom_path, "--out", os.path.join(build, "asm"),
         "--entries", os.path.join(ROOT, "config", "entrypoints.txt"),
         "--config", os.path.join(ROOT, "config"),
         "--coverage", os.path.join(ROOT, "coverage", "all.cov")], stdout=subprocess.DEVNULL)

    step("translating 65816 code to C (tools/recomp.py)")
    run([py, os.path.join(ROOT, "tools", "recomp.py"), rom_path, os.path.join(build, "asm"), os.path.join(build, "gen")])

    win = is_windows_target(args.cc)
    gen = os.path.join(build, "gen")
    core = [os.path.join(ROOT, "runtime", "snes", f) for f in sorted(os.listdir(os.path.join(ROOT, "runtime", "snes"))) if f.endswith(".c")]
    core.append(os.path.join(ROOT, "runtime", "rt.c"))
    gens = [os.path.join(gen, f) for f in sorted(os.listdir(gen)) if f.endswith(".c")]
    inc = ["-I" + os.path.join(ROOT, "runtime"), "-I" + os.path.join(ROOT, "runtime", "snes"), "-I" + gen, "-I" + build]
    base = ["-O2", "-w"] + inc
    objdir = os.path.join(build, "obj-win" if win else "obj")

    step(f"compiling {len(core) + len(gens)} files with {args.cc} ({args.jobs} at a time; the big generated files take a few minutes)")
    objs = compile_all(args.cc, core + gens, objdir, base, args.jobs)

    if args.test:
        step("building headless test driver")
        tobj = compile_all(args.cc, [os.path.join(ROOT, "runtime", "main_test.c")], objdir, base, 1)
        run([args.cc, "-o", os.path.join(build, "mmpr_test" + (".exe" if win else ""))] + tobj + objs)

    step("building the game")
    os.makedirs(dist, exist_ok=True)
    cflags = list(base)
    ldflags = []
    icon_rgba = os.path.join(ROOT, "assets", "icon64.rgba")
    if os.path.exists(icon_rgba):
        px = open(icon_rgba, "rb").read()
        with open(os.path.join(build, "icon64.h"), "w") as f:
            f.write("static const unsigned char icon64_rgba[64*64*4] = {\n")
            for i in range(0, len(px), 32):
                f.write("  " + ",".join(str(b) for b in px[i:i + 32]) + ",\n")
            f.write("};\n")
        cflags.append("-DHAVE_ICON")
    extra = []
    if win:
        sdl = find_sdl2(args)
        cflags += ["-I" + os.path.join(sdl, "include", "SDL2")]
        ldflags = ["-L" + os.path.join(sdl, "lib"), "-lmingw32", "-lSDL2main", "-lSDL2", "-mwindows", "-static-libgcc", "-s"]
        windres = args.cc.replace("gcc", "windres") if "gcc" in args.cc else "windres"
        if shutil.which(windres):
            rc = os.path.join(build, "app.rc")
            text = open(os.path.join(ROOT, "runtime", "app.rc")).read()
            ico = os.path.join(ROOT, "assets", "icon.ico")
            if os.path.exists(ico):
                text = f'1 ICON "{ico.replace(os.sep, "/")}"\n' + text
            open(rc, "w").write(text)
            res = os.path.join(objdir, "app_res.o")
            run([windres, rc, "-O", "coff", "-o", res])
            extra.append(res)
        exe = os.path.join(dist, "MMPR_Movie_PC.exe")
    else:
        try:
            cflags += subprocess.check_output(["sdl2-config", "--cflags"], text=True).split()
            ldflags = subprocess.check_output(["sdl2-config", "--libs"], text=True).split()
        except (OSError, subprocess.CalledProcessError):
            die("SDL2 development files not found (install libsdl2-dev or equivalent)")
        exe = os.path.join(dist, "MMPR_Movie_PC")
    mobj = os.path.join(objdir, "main_sdl.o")
    run([args.cc] + cflags + ["-c", os.path.join(ROOT, "runtime", "main_sdl.c"), "-o", mobj])
    run([args.cc, "-o", exe, mobj] + extra + objs + ldflags)
    if win:
        shutil.copy(os.path.join(sdl, "bin", "SDL2.dll"), dist)
    for doc in ("PLAYING.txt", "THIRD_PARTY_LICENSES.md"):
        if os.path.exists(os.path.join(ROOT, doc)):
            shutil.copy(os.path.join(ROOT, doc), dist)

    step("done")
    print(f"  {exe}")
    print("  Put your ROM next to it (or one folder up), or drag the ROM onto it, and play.")


if __name__ == "__main__":
    main()
