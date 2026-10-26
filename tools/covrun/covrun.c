// covrun: headless SNES runner that records which ROM bytes execute as
// instructions, and with which register widths (M/X flags).
//
// usage: covrun ROM SCRIPT COVERAGE_OUT [SHOT_DIR]
//
// The coverage file is one byte per ROM byte. Bits:
//   0 = executed with M8 X8      1 = M8 X16
//   2 = M16 X8                   3 = M16 X16
//   4 = executed in emulation mode
// Existing coverage files are OR-merged, so runs accumulate.
//
// Script commands (one per line, # comments):
//   wait N                  run N frames with no buttons
//   hold N BTN[+BTN...]     hold buttons for N frames
//   tap BTN[+BTN...]        hold 2 frames, release 8 frames
//   mash N BTN[+BTN...]     alternate press/release for N frames
//   shot NAME               write SHOT_DIR/NAME.ppm
//   save NAME / load NAME   save/load emulator state (in SHOT_DIR)
//   repeat N ... end        repeat a block
//   poke ADDR VAL           write one byte (hex) once
//   freeze ADDR VAL         write one byte before every frame (cheat)
//   unfreeze                clear all freezes
//   dumpram NAME            write 128 KiB WRAM to SHOT_DIR/NAME.ram
// Buttons: B Y SELECT START UP DOWN LEFT RIGHT A X L R
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include "snes.h"

extern void (*cpu_trace_hook)(uint32_t adr, bool mf, bool xf, bool e);

static uint8_t* cov;
static long romSize;
static long ramExec;
static uint8_t ramSeen[0x20000];

static void hook(uint32_t adr, bool mf, bool xf, bool e) {
  uint8_t bank = adr >> 16;
  uint16_t lo = adr & 0xffff;
  uint8_t b = bank & 0x7f;
  if (b < 0x7e && lo >= 0x8000) {
    long off = (long)b * 0x8000 + (lo - 0x8000);
    off %= romSize;
    uint8_t bit = 1 << ((mf ? 0 : 2) + (xf ? 0 : 1));
    if (e) bit |= 0x10;
    cov[off] |= bit;
  } else if (bank == 0x7e || bank == 0x7f || (b < 0x40 && lo < 0x2000)) {
    uint32_t r = bank == 0x7f ? 0x10000 + lo : lo;
    if (bank != 0x7e && bank != 0x7f) r = lo;
    if (!ramSeen[r]) { ramSeen[r] = 1; ramExec++; }
  }
}

static Snes* snes;
static uint8_t pixels[512 * 480 * 4];
static const char* shotDir = ".";
static long frameCount;

static const char* names[] = {"B","Y","SELECT","START","UP","DOWN","LEFT","RIGHT","A","X","L","R"};

static int parseButtons(const char* s) {
  int mask = 0;
  char buf[128]; strncpy(buf, s, 127); buf[127] = 0;
  for (char* t = strtok(buf, "+"); t; t = strtok(NULL, "+")) {
    for (int i = 0; i < 12; i++) if (!strcmp(t, names[i])) mask |= 1 << i;
  }
  return mask;
}

static void setButtons(int mask) {
  for (int i = 0; i < 12; i++) snes_setButtonState(snes, 1, i, (mask >> i) & 1);
}

static int nfreeze; static uint32_t freezeAdr[64]; static uint8_t freezeVal[64];
static void applyFreeze(void) {
  for (int i = 0; i < nfreeze; i++) snes_write(snes, freezeAdr[i], freezeVal[i]);
}
static void frames(int n, int mask) {
  setButtons(mask);
  for (int i = 0; i < n; i++) { applyFreeze(); snes_runFrame(snes); frameCount++; }
  setButtons(0);
}

static void shot(const char* name) {
  char path[512]; snprintf(path, sizeof path, "%s/%s.ppm", shotDir, name);
  snes_setPixels(snes, pixels);
  FILE* f = fopen(path, "wb");
  if (!f) return;
  fprintf(f, "P6\n256 224\n255\n");
  for (int y = 0; y < 224; y++) {
    uint8_t* row = pixels + (16 + y * 2) * 2048;
    for (int x = 0; x < 256; x++) {
      uint8_t* p = row + x * 8;   // XBGR byte order: [x][b][g][r]? output as RGB
      uint8_t rgb[3] = { p[3], p[2], p[1] };
      fwrite(rgb, 1, 3, f);
    }
  }
  fclose(f);
}

static uint8_t stateBuf[1 << 20];
static void saveState(const char* name) {
  char path[512]; snprintf(path, sizeof path, "%s/%s.state", shotDir, name);
  int n = snes_saveState(snes, stateBuf);
  FILE* f = fopen(path, "wb"); fwrite(stateBuf, 1, n, f); fclose(f);
}
static void loadState(const char* name) {
  char path[512]; snprintf(path, sizeof path, "%s/%s.state", shotDir, name);
  FILE* f = fopen(path, "rb"); if (!f) { fprintf(stderr, "no state %s\n", path); return; }
  int n = fread(stateBuf, 1, sizeof stateBuf, f); fclose(f);
  snes_loadState(snes, stateBuf, n);
}

static char* lines[100000];
static int nlines;

static void runLines(int from, int to);

static int findEnd(int from) {
  int depth = 0;
  for (int i = from; i < nlines; i++) {
    char c[32] = "";
    sscanf(lines[i], "%31s", c);
    if (!strcmp(c, "repeat")) depth++;
    if (!strcmp(c, "end")) { if (depth == 0) return i; depth--; }
  }
  return nlines;
}

static void runLines(int from, int to) {
  for (int i = from; i < to; i++) {
    char cmd[32] = "", a[128] = "", b[128] = "";
    char* l = lines[i];
    char* hash = strchr(l, '#'); if (hash) *hash = 0;
    if (sscanf(l, "%31s %127s %127s", cmd, a, b) < 1) continue;
    if (!strcmp(cmd, "wait")) frames(atoi(a), 0);
    else if (!strcmp(cmd, "hold")) frames(atoi(a), parseButtons(b));
    else if (!strcmp(cmd, "tap")) { frames(2, parseButtons(a)); frames(8, 0); }
    else if (!strcmp(cmd, "mash")) {
      int n = atoi(a), m = parseButtons(b);
      for (int k = 0; k < n; k += 2) { frames(1, m); frames(1, 0); }
    }
    else if (!strcmp(cmd, "shot")) shot(a);
    else if (!strcmp(cmd, "poke")) snes_write(snes, strtoul(a, 0, 16), strtoul(b, 0, 16));
    else if (!strcmp(cmd, "freeze") && nfreeze < 64) { freezeAdr[nfreeze] = strtoul(a, 0, 16); freezeVal[nfreeze++] = strtoul(b, 0, 16); }
    else if (!strcmp(cmd, "unfreeze")) nfreeze = 0;
    else if (!strcmp(cmd, "dumpram")) {
      char path[512]; snprintf(path, sizeof path, "%s/%s.ram", shotDir, a);
      FILE* f = fopen(path, "wb");
      for (uint32_t i = 0; i < 0x20000; i++) fputc(snes_read(snes, 0x7e0000 + i), f);
      fclose(f);
    }
    else if (!strcmp(cmd, "save")) saveState(a);
    else if (!strcmp(cmd, "load")) loadState(a);
    else if (!strcmp(cmd, "repeat")) {
      int end = findEnd(i + 1), n = atoi(a);
      for (int k = 0; k < n; k++) runLines(i + 1, end);
      i = end;
    }
  }
}

int main(int argc, char** argv) {
  if (argc < 4) { fprintf(stderr, "usage: covrun ROM SCRIPT COVERAGE_OUT [SHOT_DIR]\n"); return 1; }
  if (argc > 4) shotDir = argv[4];
  FILE* f = fopen(argv[1], "rb");
  fseek(f, 0, SEEK_END); romSize = ftell(f); fseek(f, 0, SEEK_SET);
  uint8_t* rom = malloc(romSize); fread(rom, 1, romSize, f); fclose(f);
  cov = calloc(romSize, 1);
  FILE* c = fopen(argv[3], "rb");
  if (c) { fread(cov, 1, romSize, c); fclose(c); }

  FILE* s = fopen(argv[2], "r");
  if (!s) { perror("script"); return 1; }
  char buf[512];
  while (fgets(buf, sizeof buf, s) && nlines < 100000) lines[nlines++] = strdup(buf);
  fclose(s);

  snes = snes_init();
  if (!snes_loadRom(snes, rom, romSize)) { fprintf(stderr, "load failed\n"); return 1; }
  cpu_trace_hook = hook;
  runLines(0, nlines);

  c = fopen(argv[3], "wb"); fwrite(cov, 1, romSize, c); fclose(c);
  long n = 0; for (long i = 0; i < romSize; i++) if (cov[i]) n++;
  printf("frames %ld, instruction starts covered %ld, RAM code addresses %ld\n", frameCount, n, ramExec);
  return 0;
}
