#include <stdio.h>
#include <stdlib.h>
#include "rt.h"

Snes* g_snes;
Cpu* C;
uint64_t rt_native_insts, rt_interp_insts, rt_fallbacks;
volatile int rt_pending;
void (*rt_boundary_hook)(void);
jmp_buf rt_top;

void rt_boundary(void) {
  if (rt_pending) {
    rt_pending = 0;
    if (rt_boundary_hook) rt_boundary_hook();
  }
  while (C->intWanted) rt_interrupt();
}

static uint32_t norm(uint32_t a) {
  uint8_t bank = a >> 16;
  if ((bank & 0x7f) < 0x40 && (a & 0xffff) >= 0x8000) bank |= 0x80;
  return ((uint32_t)bank << 16) | (a & 0xffff);
}

uint32_t rt_resume_pc;

static RecompFn find(const RecompEntry* t, int n, uint32_t a) {
  int lo = 0, hi = n - 1;
  while (lo <= hi) {
    int mid = (lo + hi) / 2;
    if (t[mid].addr == a) return t[mid].fn;
    if (t[mid].addr < a) lo = mid + 1; else hi = mid - 1;
  }
  return 0;
}

bool rt_enter_native(void) {
  uint32_t a = norm((C->k << 16) | C->pc);
  RecompFn f = find(rt_table, rt_table_size, a);
  if (f) { f(); return true; }
  f = find(rt_resume_table, rt_resume_table_size, a);
  if (f) { rt_resume_pc = a; f(); return true; }
  return false;
}

RecompFn rt_lookup(uint32_t addr24) {
  uint32_t a = norm(addr24);
  int lo = 0, hi = rt_table_size - 1;
  while (lo <= hi) {
    int mid = (lo + hi) / 2;
    if (rt_table[mid].addr == a) return rt_table[mid].fn;
    if (rt_table[mid].addr < a) lo = mid + 1; else hi = mid - 1;
  }
  return 0;
}

#define FB_SLOTS 4096
static uint32_t fb_addr[FB_SLOTS]; static uint64_t fb_count[FB_SLOTS], fb_insts[FB_SLOTS];
void rt_report_fallbacks(FILE* f) {
  for (int k = 0; k < 15; k++) {
    int best = -1;
    for (int i = 0; i < FB_SLOTS; i++) if (fb_count[i] && (best < 0 || fb_insts[i] > fb_insts[best])) best = i;
    if (best < 0) break;
    fprintf(f, "  fallback at %06X: %llu times, %llu instructions\n", fb_addr[best],
            (unsigned long long)fb_count[best], (unsigned long long)fb_insts[best]);
    fb_count[best] = 0;
  }
}
void rt_interpret_until(uint16_t spe) {
  rt_fallbacks++;
  uint32_t at = (C->k << 16) | C->pc; int slot = (at * 2654435761u) >> 20 & (FB_SLOTS - 1);
  while (fb_count[slot] && fb_addr[slot] != at) slot = (slot + 1) & (FB_SLOTS - 1);
  fb_addr[slot] = at; fb_count[slot]++;
  uint64_t before = rt_interp_insts;
  // run the reference interpreter until the return address of the current
  // native function has been popped (stack pointer rises above spe)
  bool first = true;   // never hand the very first instruction back (it's why we're here)
  while ((uint16_t)C->sp <= spe) {
    if (rt_pending) { rt_pending = 0; if (rt_boundary_hook) rt_boundary_hook(); }
    // whenever execution reaches the start of a recompiled function, run that
    // natively: it executes exactly the same instructions up to its return,
    // after which the interpreter carries on from wherever it returned to
    if (!first && !C->waiting && !C->stopped && !C->intWanted && rt_enter_native()) continue;
    first = false;
    cpu_runOpcode(C);
    rt_interp_insts++;
  }
  fb_insts[slot] += rt_interp_insts - before;
}

void rt_call_pc(void) {
  RecompFn f = rt_lookup((C->k << 16) | C->pc);
  if (f) f();
  else rt_interpret_until(C->sp);
}

void rt_interrupt(void) {
  // same sequence as cpu_runOpcode() when an interrupt is pending
  cpu_read(C, (C->k << 16) | C->pc);
  cpu_doInterrupt(C);
  rt_call_pc();  // handler ends with RTI, which restores pc/k/flags
}

void rt_wait(void) {
  while (C->waiting) {
    if (C->irqWanted || C->nmiWanted) {
      C->waiting = false;
      cpu_idle(C);
      cpu_checkInt(C);
      cpu_idle(C);
    } else {
      cpu_idleWait(C);
    }
  }
}

void rt_stop(void) {
  for (;;) cpu_idleWait(C);
}

void rt_resume(void) {
  // used after loading a save state: the C call stack of the old execution is
  // gone, so run from the restored CPU state, going native at function starts
  for (;;) {
    if (rt_pending) { rt_pending = 0; if (rt_boundary_hook) rt_boundary_hook(); }
    if (!C->waiting && !C->stopped && !C->intWanted && !C->resetWanted && rt_enter_native()) continue;
    cpu_runOpcode(C);
    rt_interp_insts++;
  }
}
