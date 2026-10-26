// Headless test driver: runs the game either on the reference interpreter or
// on the recompiled code, with the same scripted controller input, and logs a
// checksum of all memory (WRAM, VRAM, palette, OAM) at the start of every vblank.
//
// usage: mmpr_test interp|recomp ROM SCRIPT FRAMES LOGFILE [SHOTPREFIX]
//   SCRIPT uses the covrun syntax: wait N / hold N BTN / tap BTN / mash N BTN / repeat N .. end
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#include <time.h>
#include "rt.h"
#include "snes/ppu.h"
#include "snes/apu.h"

static uint16_t* masks; static int nmasks, cap;
static void push(int n, int m) { for (int i = 0; i < n; i++) { if (nmasks == cap) { cap = cap ? cap * 2 : 4096; masks = realloc(masks, cap * sizeof *masks); } masks[nmasks++] = m; } }
static const char* bn[] = {"B","Y","SELECT","START","UP","DOWN","LEFT","RIGHT","A","X","L","R"};
static int parse(const char* s) { int m = 0; char b[128]; strncpy(b, s, 127); b[127] = 0; for (char* t = strtok(b, "+"); t; t = strtok(0, "+")) for (int i = 0; i < 12; i++) if (!strcmp(t, bn[i])) m |= 1 << i; return m; }
static char* L[20000]; static int nl;
static void run(int from, int to) {
  for (int i = from; i < to; i++) {
    char c[32] = "", a[128] = "", b[128] = "";
    if (sscanf(L[i], "%31s %127s %127s", c, a, b) < 1 || c[0] == '#') continue;
    if (!strcmp(c, "wait")) push(atoi(a), 0);
    else if (!strcmp(c, "hold")) push(atoi(a), parse(b));
    else if (!strcmp(c, "tap")) { push(2, parse(a)); push(8, 0); }
    else if (!strcmp(c, "mash")) { int n = atoi(a), m = parse(b); for (int k = 0; k < n; k += 2) { push(1, m); push(1, 0); } }
    else if (!strcmp(c, "repeat")) {
      int d = 0, e = i + 1;
      for (; e < nl; e++) { char x[32] = ""; sscanf(L[e], "%31s", x); if (!strcmp(x, "repeat")) d++; if (!strcmp(x, "end")) { if (!d) break; d--; } }
      for (int k = 0; k < atoi(a); k++) run(i + 1, e);
      i = e;
    }
  }
}

static uint32_t crc(const uint8_t* p, size_t n, uint32_t c) {
  c = ~c;
  for (size_t i = 0; i < n; i++) { c ^= p[i]; for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320 & -(c & 1)); }
  return ~c;
}

static jmp_buf done;
static int traceFrom = -1, traceTo = -1, frameNo;
static FILE* tracef;
static void trace(uint32_t adr, bool mf, bool xf, bool e) {
  if (frameNo < traceFrom || frameNo > traceTo) return;
  fprintf(tracef, "%06X A%04X X%04X Y%04X S%04X D%04X B%02X %c%c%c%c%c%c%c%c\n", adr, C->a, C->x, C->y, C->sp, C->dp, C->db,
          C->n ? 'N' : 'n', C->v ? 'V' : 'v', mf ? 'M' : 'm', xf ? 'X' : 'x', C->d ? 'D' : 'd', C->i ? 'I' : 'i', C->z ? 'Z' : 'z', C->c ? 'C' : 'c');
}
static int frame, maxFrames;
static FILE* logf;
static const char* shotPrefix;
static uint8_t pixels[512 * 480 * 4];

static void shot(int f) {
  char path[512]; snprintf(path, sizeof path, "%s_%05d.ppm", shotPrefix, f);
  snes_setPixels(g_snes, pixels);
  FILE* o = fopen(path, "wb"); if (!o) return;
  fprintf(o, "P6\n256 224\n255\n");
  for (int y = 0; y < 224; y++) { uint8_t* row = pixels + (16 + y * 2) * 2048; for (int x = 0; x < 256; x++) { uint8_t* p = row + x * 8; uint8_t rgb[3] = {p[3], p[2], p[1]}; fwrite(rgb, 1, 3, o); } }
  fclose(o);
}

static uint16_t* movie; static uint16_t* movie2; static int movieFrames, roundtripAt = -1;
static uint8_t* movieState; static uint32_t movieStateSize;
static uint8_t stateBuf[1 << 20];

static void loadMovie(const char* path) {
  // MMPRINP1: one uint16 per frame (player 1).  MMPRINP2: two per frame (P1, P2).
  // Bit 15 of a player's mask = that player's infinite-health cheat was on.
  FILE* f = fopen(path, "rb"); if (!f) { perror(path); exit(1); }
  char magic[8]; fread(magic, 1, 8, f);
  int players = !memcmp(magic, "MMPRINP1", 8) ? 1 : !memcmp(magic, "MMPRINP2", 8) ? 2 : 0;
  if (!players) { fprintf(stderr, "not a movie\n"); exit(1); }
  fread(&movieStateSize, 4, 1, f);
  if (movieStateSize) { movieState = malloc(movieStateSize); fread(movieState, 1, movieStateSize, f); }
  long pos = ftell(f); fseek(f, 0, SEEK_END); movieFrames = (ftell(f) - pos) / (2 * players); fseek(f, pos, SEEK_SET);
  movie = calloc(movieFrames + 1, 2); movie2 = calloc(movieFrames + 1, 2);
  for (int i = 0; i < movieFrames; i++) {
    fread(&movie[i], 2, 1, f);
    if (players == 2) fread(&movie2[i], 2, 1, f);
  }
  fclose(f);
}

// state round-trip test: at the next instruction boundary, save and immediately
// reload the full machine state, discarding the native call stack
static const char* saveStatePath;
static void roundtrip(void) {
  int n = snes_saveState(g_snes, stateBuf);
  if (getenv("RESET_FIRST")) {   // soft reset (like the PC build's Reset key), then save
    snes_reset(g_snes, false);
    n = snes_saveState(g_snes, stateBuf);
    FILE* o = fopen(saveStatePath, "wb"); fwrite(stateBuf, 1, n, o); fclose(o);
    fprintf(stderr, "reset + saved state at frame %d\n", frame);
    longjmp(rt_top, 1);
  }
  if (saveStatePath) {           // SAVE_STATE_AT: write the state out and keep going
    FILE* o = fopen(saveStatePath, "wb"); fwrite(stateBuf, 1, n, o); fclose(o);
    fprintf(stderr, "saved state at frame %d to %s\n", frame, saveStatePath);
    return;
  }
  fprintf(stderr, "state round-trip at frame %d (%d bytes)\n", frame, n);
  snes_loadState(g_snes, stateBuf, n);
  longjmp(rt_top, 1);
}

static void vblank(Snes* s) {
  snes_catchupApu(s);
  Ppu* p = s->ppu;
  uint32_t h = crc(s->ram, sizeof s->ram, 0);
  uint32_t v = crc((uint8_t*)p->vram, sizeof p->vram, 0);
  uint32_t g = crc((uint8_t*)p->cgram, sizeof p->cgram, 0);
  uint32_t o = crc((uint8_t*)p->oam, sizeof p->oam, 0);
  static int overFrames; if (p->timeOver || p->rangeOver) { overFrames++; if (getenv("SPRITESTATS") && overFrames % 500 == 1) fprintf(stderr, "sprite limit hit in %d frames so far (frame %d)\n", overFrames, frame); }
  fprintf(logf, "%05d wram=%08X vram=%08X cgram=%08X oam=%08X\n", frame, h, v, g, o);
  if (shotPrefix && frame % 300 == 0) shot(frame);
  static int dumpEvery = -1;
  if (dumpEvery < 0) dumpEvery = getenv("DUMPRAM_EVERY") ? atoi(getenv("DUMPRAM_EVERY")) : 0;
  if (dumpEvery && shotPrefix && frame % dumpEvery == 0) {
    char rp[512]; snprintf(rp, sizeof rp, "%s_%05d.ram", shotPrefix, frame);
    FILE* r = fopen(rp, "wb"); fwrite(s->ram, 1, sizeof s->ram, r); fclose(r);
  }
  frame++; frameNo = frame;
  if (frame >= maxFrames) longjmp(done, 1);
  if (frame == roundtripAt) rt_pending = 1;
  int m;
  if (movie) m = frame - 1 < movieFrames ? movie[frame - 1] : 0;
  else m = frame < nmasks ? masks[frame] : 0;
  int m2 = (movie && frame - 1 < movieFrames) ? movie2[frame - 1] : 0;
  if (m & 0x8000) snes_write(s, 0x7E0628, 5);    // P1 infinite-health cheat, recorded in movies
  if (m2 & 0x8000) snes_write(s, 0x7E062C, 5);   // P2 infinite-health cheat
  for (int i = 0; i < 12; i++) { snes_setButtonState(s, 1, i, (m >> i) & 1); snes_setButtonState(s, 2, i, (m2 >> i) & 1); }
}

int main(int argc, char** argv) {
  if (argc < 6) { fprintf(stderr, "usage: mmpr_test interp|recomp ROM SCRIPT FRAMES LOG [SHOTPREFIX]\n"); return 1; }
  bool recomp = !strcmp(argv[1], "recomp");
  FILE* f = fopen(argv[2], "rb"); if (!f) { perror("rom"); return 1; }
  fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
  uint8_t* rom = malloc(n); fread(rom, 1, n, f); fclose(f);
  size_t al = strlen(argv[3]);
  bool isMovie = al > 4 && !strcmp(argv[3] + al - 4, ".inp");
  if (isMovie) loadMovie(argv[3]);
  FILE* sc = isMovie ? NULL : fopen(argv[3], "r"); char buf[512];
  if (sc) { while (fgets(buf, sizeof buf, sc) && nl < 20000) L[nl++] = strdup(buf); fclose(sc); run(0, nl); }
  maxFrames = atoi(argv[4]);
  if (maxFrames <= 0 && movie) maxFrames = movieFrames + 1;
  if (getenv("STATE_ROUNDTRIP_AT")) roundtripAt = atoi(getenv("STATE_ROUNDTRIP_AT"));
  if (getenv("SAVE_STATE_AT")) { roundtripAt = atoi(getenv("SAVE_STATE_AT")); saveStatePath = getenv("SAVE_STATE_FILE"); }
  rt_boundary_hook = roundtrip;
  logf = fopen(argv[5], "w");
  shotPrefix = argc > 6 ? argv[6] : NULL;

  g_snes = snes_init();
  C = g_snes->cpu;
  if (!snes_loadRom(g_snes, rom, n)) return 1;
  snes_vblank_hook = vblank;
  if (getenv("TRACE_FROM")) {
    traceFrom = atoi(getenv("TRACE_FROM")); traceTo = atoi(getenv("TRACE_TO"));
    char tp[512]; snprintf(tp, sizeof tp, "%s.trace", argv[5]); tracef = fopen(tp, "w");
    cpu_trace_hook = trace;
  }
  clock_t t0 = clock();
  if (!setjmp(done)) {
    if (setjmp(rt_top)) rt_resume();  // after a state round-trip
    if (movieState) {
      if (!snes_loadState(g_snes, movieState, movieStateSize)) { fprintf(stderr, "bad state in movie\n"); return 1; }
      if (recomp) rt_resume();
      else for (;;) { cpu_runOpcode(C); rt_interp_insts++; }
    }
    cpu_runOpcode(C);                 // performs the reset sequence (loads the reset vector)
    if (recomp) { rt_call_pc(); fprintf(stderr, "reset function returned?\n"); }
    else for (;;) { cpu_runOpcode(C); rt_interp_insts++; }
  }
  double secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
  fclose(logf);
  fprintf(stderr, "%s: %d frames in %.2fs (%.0f fps). native instrs %llu, interpreted %llu, fallbacks %llu\n",
          argv[1], frame, secs, frame / secs, (unsigned long long)rt_native_insts,
          (unsigned long long)rt_interp_insts, (unsigned long long)rt_fallbacks);
  if (recomp) rt_report_fallbacks(stderr);
  return 0;
}
