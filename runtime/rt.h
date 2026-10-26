// Runtime support for the recompiled game code.
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "snes/snes.h"
#include "snes/cpu.h"

// exported pieces of the emulator core (runtime/snes, made non-static)
uint8_t cpu_read(Cpu* cpu, uint32_t adr);
void cpu_idle(Cpu* cpu);
void cpu_idleWait(Cpu* cpu);
void cpu_checkInt(Cpu* cpu);
void cpu_doInterrupt(Cpu* cpu);
void cpu_doOpcode(Cpu* cpu, uint8_t opcode);
void cpu_doBranch(Cpu* cpu, bool check);
void snes_catchupApu(Snes* snes);
extern void (*snes_vblank_hook)(Snes* snes);
extern void (*cpu_trace_hook)(uint32_t adr, bool mf, bool xf, bool e);

extern Snes* g_snes;
extern Cpu* C;

typedef void (*RecompFn)(void);
typedef struct { uint32_t addr; RecompFn fn; } RecompEntry;
extern const RecompEntry rt_table[];
extern const int rt_table_size;

RecompFn rt_lookup(uint32_t addr24);
extern const RecompEntry rt_resume_table[];
extern const int rt_resume_table_size;
extern uint32_t rt_resume_pc;                // nonzero: the called function starts at this address
static inline uint32_t rt_take_resume(void) { uint32_t r = rt_resume_pc; rt_resume_pc = 0; return r; }
bool rt_enter_native(void);                // run native code from the current pc if any covers it
void rt_call_pc(void);                     // run the function at C->k:C->pc until it returns
void rt_interpret_until(uint16_t spe);     // reference interpreter until the stack pops above spe
void rt_interrupt(void);                   // service a pending NMI/IRQ at the current pc
void rt_wait(void);                        // WAI
void rt_stop(void);                        // STP

// statistics
#include <stdio.h>
void rt_report_fallbacks(FILE* f);
extern uint64_t rt_native_insts, rt_interp_insts, rt_fallbacks;

// Frontend events (save/load state, ...) are deferred to the next instruction
// boundary, where the CPU state is complete and consistent.
#include <setjmp.h>
extern volatile int rt_pending;             // set by the frontend; handled at the next boundary
extern void (*rt_boundary_hook)(void);      // runs at that boundary
extern jmp_buf rt_top;                      // longjmp target after a state load
void rt_boundary(void);
void rt_resume(void);                       // continue from the current CPU state forever

// --- per-instruction macros used by generated code ---
#define INS(a16) do { C->pc = (a16); if (C->intWanted | rt_pending) rt_boundary(); } while (0)
#define TRACE() do { rt_native_insts++; if (cpu_trace_hook) cpu_trace_hook((C->k << 16) | C->pc, C->mf, C->xf, C->e); } while (0)
#define FETCH() do { cpu_read(C, (C->k << 16) | C->pc); C->pc++; } while (0)
#define RT_JUMP() do { RecompFn f_ = rt_lookup((C->k << 16) | C->pc); if (f_) f_(); else rt_interpret_until(spe); return; } while (0)
