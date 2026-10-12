/* sharcvm.c -- SHARC+ (ADSP-2156x) interpreter core. See sharcvm.h.
 *
 * Semantics sources (cited in notes/T3.6-sharc-interpreter.md):
 *   PGR = SHARC Processor Programming Reference rev 2.4 (ADSP-214xx, VISA),
 *         chapters 3, 4, 9, 11, 12;
 *   PRM tables as transcribed by digikit (tools/sharcspec/compute_table.json);
 *   observed DN2 code (address-space scaling, access widths).
 * Every instruction the translator cannot express is K_UNIMPL and traps.
 */
#include "sharcvm.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stddef.h>

#define LIKELY(x) __builtin_expect(!!(x), 1)
#define UNLIKELY(x) __builtin_expect(!!(x), 0)

/* ------------------------------------------------------------------ */
/* errors                                                              */

static int trapf(vm_t *vm, int code, const char *fmt, ...) {
    if (vm->trap_code) return ST_TRAP;      /* keep the first */
    va_list ap; va_start(ap, fmt);
    vsnprintf(vm->trap_msg, sizeof vm->trap_msg, fmt, ap);
    va_end(ap);
    vm->trap_code = code ? code : 1;
    vm->trap_pc = vm->pc;
    return ST_TRAP;
}
#define TRAP(...) do { trapf(vm, 1, __VA_ARGS__); return; } while (0)
#define TRAPV(v, ...) do { trapf(vm, 1, __VA_ARGS__); return (v); } while (0)

/* ------------------------------------------------------------------ */
/* memory                                                              */

/* address spaces of the ADSP-2156x core (T3.2 section 0):
 *   LW   0x48000-0x73fff   byte = 8*a  (long word)
 *   NW   0x90000-0xe7fff   byte = 4*a
 *   SW   0x120000-0x1cffff byte = 2*a
 *   byte 0x240000-0x39ffff L1, 0x20000000 L2, 0x80000000 DDR, MMRs
 *   0x28000000 + L1 byte = system (loader) alias of L1                */
enum { SP_BYTE = 0, SP_NW, SP_SW, SP_LW, SP_OTHER };
/* SP_OTHER: values that are not an address in any data space (e.g. 0, core MMRs).
 * DAG modifiers are scaled (by the access width) only for byte-space addresses:
 * DN2 0xb83f47 `I0 = modify(I3, M4)` with I3 = 0 and M4 = a byte address must not
 * scale, while `modify(I7, M7)` on the byte-addressed stack must (0xb84845). */
static inline int space_of(uint32_t a) {
    if (a >= 0x240000u && a < 0x400000u) return SP_BYTE;
    if (a >= 0x20000000u) return SP_BYTE;
    if (a >= 0x90000u && a < 0xe8000u) return SP_NW;
    if (a >= 0x120000u && a < 0x1d0000u) return SP_SW;
    if (a >= 0x48000u && a < 0x74000u) return SP_LW;
    return SP_OTHER;
}
static inline uint32_t to_byte(uint32_t a) {
    switch (space_of(a)) {
    case SP_NW: return a * 4u;
    case SP_SW: return a * 2u;
    case SP_LW: return a * 8u;
    default:
        if (a >= 0x28240000u && a < 0x283a0000u) return a - 0x28000000u;
        return a;
    }
}

static int mmr_index(vm_t *vm, uint32_t a, int create) {
    uint32_t h = (a * 2654435761u) >> 16;
    for (int k = 0; k < 65536; k++) {
        uint32_t j = (h + (uint32_t)k) & 0xffff;
        if (!vm->mmr_used[j]) {
            if (!create) return -1;
            vm->mmr_used[j] = 1; vm->mmr_addr[j] = a; vm->mmr_val[j] = 0; vm->n_mmr++;
            return (int)j;
        }
        if (vm->mmr_addr[j] == a) return (int)j;
    }
    return -1;
}
static inline int is_mmr(uint32_t a) {
    return (a >= 0x30000u && a < 0x40000u) || (a >= 0x30000000u && a < 0x40000000u);
}

static inline uint8_t *mptr(vm_t *vm, uint32_t b, uint32_t n) {
    uint8_t *p = vm->mem[b >> 12];
    if (UNLIKELY(!p)) return NULL;
    if (UNLIKELY(((b & 0xfff) + n) > 0x1000)) {
        /* crosses a page: only fine when the next page is contiguous */
        uint8_t *q = vm->mem[(b >> 12) + 1];
        if (q != p + 0x1000) return NULL;
    }
    return p + (b & 0xfff);
}

static int rd_bytes(vm_t *vm, uint32_t b, uint32_t n, uint64_t *out) {
    uint8_t *p = mptr(vm, b, n);
    if (LIKELY(p != NULL)) {
        uint64_t v = 0;
        for (uint32_t i = 0; i < n; i++) v |= (uint64_t)p[i] << (8 * i);
        *out = v; return 1;
    }
    if (is_mmr(b) && n == 4) {
        vm->stat_mmr_reads++; vm->last_mmr_addr = b;
        if (vm->mmr_strict) { trapf(vm, 2, "read of MMR %#x (strict mode)", b); return 0; }
        if (b < 0x40000u) { *out = vm->core_mmr[b - 0x30000u]; return 1; }
        int i = mmr_index(vm, b, 0);
        *out = i >= 0 ? vm->mmr_val[i] : 0;
        return 1;
    }
    trapf(vm, 2, "read of unmapped address %#x (%u bytes)", b, n);
    return 0;
}
static int wr_bytes(vm_t *vm, uint32_t b, uint32_t n, uint64_t v) {
    if (UNLIKELY(b < vm->watch_hi && b + n > vm->watch_lo) && vm->n_watch < 65536) {
        vm->watch_pc[vm->n_watch] = vm->pc; vm->watch_addr[vm->n_watch] = b; vm->watch_val[vm->n_watch] = (uint32_t)v;
        vm->n_watch++;
    }
    uint8_t *p = mptr(vm, b, n);
    if (LIKELY(p != NULL)) {
        for (uint32_t i = 0; i < n; i++) p[i] = (uint8_t)(v >> (8 * i));
        return 1;
    }
    if (is_mmr(b) && n == 4) {
        vm->stat_mmr_writes++; vm->last_mmr_addr = b;
        if (vm->mmr_strict) { trapf(vm, 2, "write of MMR %#x (strict mode)", b); return 0; }
        if (b < 0x40000u) { vm->core_mmr[b - 0x30000u] = (uint32_t)v; return 1; }
        int i = mmr_index(vm, b, 1);
        if (i < 0) { trapf(vm, 2, "MMR table full at %#x", b); return 0; }
        vm->mmr_val[i] = (uint32_t)v;
        return 1;
    }
    trapf(vm, 2, "write of unmapped address %#x (%u bytes)", b, n);
    return 0;
}

/* ------------------------------------------------------------------ */
/* float helpers (40-bit register format: s|e8|m31)                    */

static inline uint32_t hi32(uint64_t r) { return (uint32_t)(r >> 8); }
static inline uint64_t from32(uint32_t v) { return (uint64_t)v << 8; }
#define NAN40 0xffffffffffull

typedef struct { double v; int nan, inf, zero, neg; } fval_t;

static inline fval_t fdec(uint64_t r) {
    fval_t f; uint32_t s = (uint32_t)(r >> 39) & 1, e = (uint32_t)(r >> 31) & 0xff;
    uint64_t m = r & 0x7fffffffull;
    f.nan = f.inf = f.zero = 0; f.neg = (int)s;
    if (e == 0xff) {
        if (m) { f.nan = 1; f.v = NAN; } else { f.inf = 1; f.v = s ? -INFINITY : INFINITY; }
        return f;
    }
    if (e == 0) { f.zero = 1; f.v = s ? -0.0 : 0.0; return f; }   /* denormals flush */
    f.v = ldexp((double)((1ull << 31) | m), (int)e - 127 - 31);
    if (s) f.v = -f.v;
    return f;
}

/* round a double to the SHARC float format. bits40: 1 = 31-bit mantissa (RND32=0).
 * Returns the 40-bit register value; sets *ov / *un. */
static uint64_t fenc(double v, int bits40, int trunc, int *ov, int *un) {
    *ov = *un = 0;
    if (isnan(v)) return NAN40;
    uint64_t s = signbit(v) ? 1ull << 39 : 0;
    if (isinf(v)) { *ov = 1; return s | (0xffull << 31); }
    if (v == 0.0) return s;
    int P = bits40 ? 32 : 24;      /* significant bits incl. hidden */
    int e; double f = frexp(fabs(v), &e);   /* f in [0.5,1) */
    double sc = ldexp(f, P);
    double m = floor(sc), rem = sc - m;
    uint64_t mi = (uint64_t)m;
    if (!trunc) {
        if (rem > 0.5 || (rem == 0.5 && (mi & 1))) mi++;
    }
    if (mi == (1ull << P)) { mi >>= 1; e++; }
    int be = e - 1 + 127;
    if (be >= 255) {
        *ov = 1;
        if (trunc) return s | (0xfeull << 31) | (bits40 ? 0x7fffffffull : (0x7fffffull << 8));
        return s | (0xffull << 31);
    }
    if (be <= 0) { *un = 1; return s; }
    uint64_t frac = mi & ((1ull << (P - 1)) - 1);
    if (!bits40) frac <<= 8;
    return s | ((uint64_t)be << 31) | frac;
}

/* ------------------------------------------------------------------ */
/* registers                                                           */

static inline int simd(vm_t *vm) { return (vm->mode1 & M1_PEYEN) != 0; }

static uint32_t curlcntr(vm_t *vm) {
    if (vm->lsp == 0) return 0xffffffffu;
    return vm->lstk[vm->lsp - 1].count;
}

static void update_stack_flags(vm_t *vm) {
    uint32_t s = vm->pe[0].stky & ~(PCEM | SSEM | LSEM);
    if (vm->pcsp == 0) s |= PCEM;
    if (vm->ssp == 0) s |= SSEM;
    if (vm->lsp == 0) s |= LSEM;
    vm->pe[0].stky = s;
}

static void mode1_write(vm_t *vm, uint32_t nv);

/* read a universal register as a 40-bit value */
static uint64_t ureg_rd(vm_t *vm, int code) {
    int g = code >> 4, i = code & 15;
    switch (g) {
    case 0: return vm->pe[0].r[i];
    case 1: return from32(vm->I[i]);
    case 2: return from32(vm->M[i]);
    case 3: return from32(vm->L[i]);
    case 4: return from32(vm->B[i]);
    case 5: return vm->pe[1].r[i];
    }
    uint32_t v = 0;
    switch (code) {
    case 0x60: v = vm->faddr; break;
    case 0x61: v = vm->daddr; break;
    case 0x63: v = vm->pc; break;
    case 0x64: v = vm->pcsp ? vm->pcstk[vm->pcsp - 1] : 0xffffffffu; break;
    case 0x65: v = (uint32_t)vm->pcsp; break;
    case 0x66: v = vm->lsp ? vm->lstk[vm->lsp - 1].end : 0xffffffffu; break;
    case 0x67: v = curlcntr(vm); break;
    case 0x68: v = vm->lcntr; break;
    case 0x69: v = (uint32_t)(vm->icount + vm->emuclk_base); break;
    case 0x6a: v = (uint32_t)((vm->icount + vm->emuclk_base) >> 32); break;
    case 0x6b: { uint64_t px = ((uint64_t)vm->px2 << 32) | vm->px1; return (px >> 24) & 0xffffffffffull; }  /* PGR 2-10: upper 40 bits */
    case 0x6c: v = vm->px1; break;
    case 0x6d: v = vm->px2; break;
    case 0x6e: v = vm->tperiod; break;
    case 0x6f: v = vm->tcount; break;
    case 0x70: v = vm->ustat[0]; break;
    case 0x71: v = vm->ustat[1]; break;
    case 0x72: v = vm->mode1; break;
    case 0x73: v = vm->mmask; break;
    case 0x74: v = vm->mode2; break;
    case 0x75: v = vm->flagsreg; break;
    case 0x76: v = vm->pe[0].astat; break;
    case 0x77: v = vm->pe[1].astat; break;
    case 0x78: update_stack_flags(vm); v = vm->pe[0].stky; break;
    case 0x79: v = vm->pe[1].stky; break;
    case 0x7a: v = vm->irptl; break;
    case 0x7b: v = vm->imask; break;
    case 0x7c: v = vm->imaskp; break;
    case 0x7d: v = vm->mode1stk; break;
    case 0x7e: v = vm->ustat[2]; break;
    case 0x7f: v = vm->ustat[3]; break;
    default:
        trapf(vm, 3, "read of unsupported universal register %#x", code);
    }
    return from32(v);
}

static void ureg_wr(vm_t *vm, int code, uint64_t val) {
    int g = code >> 4, i = code & 15;
    uint32_t v = hi32(val);
    switch (g) {
    case 0: vm->pe[0].r[i] = val & 0xffffffffffull; return;
    case 1: vm->I[i] = v; return;
    case 2: vm->M[i] = v; return;
    case 3: vm->L[i] = v; return;
    case 4: vm->B[i] = v; vm->I[i] = v; return;   /* writing B also loads I (PGR ch. 6) */
    case 5: vm->pe[1].r[i] = val & 0xffffffffffull; return;
    }
    switch (code) {
    case 0x60: vm->faddr = v; return;
    case 0x61: vm->daddr = v; return;
    case 0x64: if (vm->pcsp) vm->pcstk[vm->pcsp - 1] = v; else trapf(vm, 3, "write PCSTK with empty stack"); return;
    case 0x66: trapf(vm, 3, "write to LADDR not supported"); return;
    case 0x67: if (vm->lsp) vm->lstk[vm->lsp - 1].count = v; return;
    case 0x68: vm->lcntr = v; return;
    case 0x69: vm->emuclk_base = (vm->emuclk_base & ~0xffffffffull) + v - (uint32_t)vm->icount; return;
    case 0x6b: { uint64_t px = (val & 0xffffffffffull) << 24; vm->px1 = (uint32_t)px; vm->px2 = (uint32_t)(px >> 32); return; }
    case 0x6c: vm->px1 = v; return;
    case 0x6d: vm->px2 = v; return;
    case 0x6e: vm->tperiod = v; return;
    case 0x6f: vm->tcount = v; return;
    case 0x70: vm->ustat[0] = v; return;
    case 0x71: vm->ustat[1] = v; return;
    case 0x72: mode1_write(vm, v); return;
    case 0x73: vm->mmask = v; return;
    case 0x74: vm->mode2 = v; return;
    case 0x75: vm->flagsreg = v; return;
    case 0x76: vm->pe[0].astat = v; return;
    case 0x77: vm->pe[1].astat = v; return;
    case 0x78: vm->pe[0].stky = (vm->pe[0].stky & (PCEM | SSEM | LSEM)) | (v & ~(PCEM | SSEM | LSEM)); return;
    case 0x79: vm->pe[1].stky = v; return;
    case 0x7a: vm->irptl = v; return;
    case 0x7b: vm->imask = v; return;
    case 0x7c: vm->imaskp = v; return;
    case 0x7d: vm->mode1stk = v; return;
    case 0x7e: vm->ustat[2] = v; return;
    case 0x7f: vm->ustat[3] = v; return;
    }
    trapf(vm, 3, "write of unsupported universal register %#x", code);
}

/* SIMD complement of a universal register code, or -1 */
static inline int complement(int code) {
    if (code < 0x10) return code + 0x50;
    if (code >= 0x50 && code < 0x60) return code - 0x50;
    switch (code) {
    case 0x70: return 0x71; case 0x71: return 0x70;
    case 0x7e: return 0x7f; case 0x7f: return 0x7e;
    case 0x76: return 0x77; case 0x77: return 0x76;
    case 0x78: return 0x79; case 0x79: return 0x78;
    case 0x6c: return 0x6d; case 0x6d: return 0x6c;
    }
    return -1;
}

static void swap_u32(uint32_t *a, uint32_t *b, int n) {
    for (int i = 0; i < n; i++) { uint32_t t = a[i]; a[i] = b[i]; b[i] = t; }
}
static void swap_u64(uint64_t *a, uint64_t *b, int n) {
    for (int i = 0; i < n; i++) { uint64_t t = a[i]; a[i] = b[i]; b[i] = t; }
}

static void mode1_write(vm_t *vm, uint32_t nv) {
    uint32_t ov = vm->mode1, ch = ov ^ nv;
    /* secondary register sets (PGR 2-14): swap the groups whose select bit changes */
    if (ch & M1_SRD1L) { swap_u32(vm->I, vm->Ialt, 4); swap_u32(vm->M, vm->Malt, 4); swap_u32(vm->L, vm->Lalt, 4); swap_u32(vm->B, vm->Balt, 4); }
    if (ch & M1_SRD1H) { swap_u32(vm->I + 4, vm->Ialt + 4, 4); swap_u32(vm->M + 4, vm->Malt + 4, 4); swap_u32(vm->L + 4, vm->Lalt + 4, 4); swap_u32(vm->B + 4, vm->Balt + 4, 4); }
    if (ch & M1_SRD2L) { swap_u32(vm->I + 8, vm->Ialt + 8, 4); swap_u32(vm->M + 8, vm->Malt + 8, 4); swap_u32(vm->L + 8, vm->Lalt + 8, 4); swap_u32(vm->B + 8, vm->Balt + 8, 4); }
    if (ch & M1_SRD2H) { swap_u32(vm->I + 12, vm->Ialt + 12, 4); swap_u32(vm->M + 12, vm->Malt + 12, 4); swap_u32(vm->L + 12, vm->Lalt + 12, 4); swap_u32(vm->B + 12, vm->Balt + 12, 4); }
    if (ch & M1_SRRFL) { swap_u64(vm->pe[0].r, vm->Ralt[0], 8); swap_u64(vm->pe[1].r, vm->Ralt[1], 8); }
    if (ch & M1_SRRFH) { swap_u64(vm->pe[0].r + 8, vm->Ralt[0] + 8, 8); swap_u64(vm->pe[1].r + 8, vm->Ralt[1] + 8, 8); }
    if (ch & M1_SRCU) {
        for (int p = 0; p < 2; p++) { __int128 t = vm->pe[p].mr[1]; vm->pe[p].mr[1] = vm->pe[p].mr[2]; vm->pe[p].mr[2] = t; }
    }
    vm->mode1 = nv;
    if (nv & (M1_BR0 | M1_BR8 | M1_SSE))
        trapf(vm, 4, "MODE1 = %#x sets an unsupported mode (bit-reverse / SSE)", nv);

}

/* ------------------------------------------------------------------ */
/* conditions                                                          */

static int cond_pe(vm_t *vm, int pe, int c) {
    uint32_t a = vm->pe[pe].astat;
    int af = (a & AF) != 0, an = (a & AN) != 0, az = (a & AZ) != 0, av = (a & AV) != 0;
    int sat = (vm->mode1 & M1_ALUSAT) != 0;
    int y = (!af && (an ^ (av && !sat))) || (af && an && !az);          /* LT */
    int x = (!af && (an ^ (av && !sat))) || (af && an) || az;           /* LE */
    switch (c) {
    case 0: return az;
    case 1: return y;
    case 2: return x;
    case 3: return (a & AC) != 0;
    case 4: return av;
    case 5: return (a & MV) != 0;
    case 6: return (a & MN) != 0;
    case 7: return (a & SV) != 0;
    case 8: return (a & SZ) != 0;
    case 9: case 10: case 11: case 12: return (vm->flagsreg >> (2 * (c - 9))) & 1;
    case 13: return (a & BTF) != 0;
    case 15: return curlcntr(vm) != 1;                 /* NOT LCE */
    case 16: return !az;
    case 17: return !y;
    case 18: return !x;
    case 19: return !(a & AC);
    case 20: return !av;
    case 21: return !(a & MV);
    case 22: return !(a & MN);
    case 23: return !(a & SV);
    case 24: return !(a & SZ);
    case 25: case 26: case 27: case 28: return !((vm->flagsreg >> (2 * (c - 25))) & 1);
    case 29: return !(a & BTF);
    case 31: return 1;
    }
    trapf(vm, 5, "unsupported condition code %d", c);
    return 0;
}

/* ------------------------------------------------------------------ */
/* compute                                                             */

typedef struct {
    int nw; int rd[3]; uint64_t rv[3];      /* register writes */
    uint32_t aset, amask;                   /* ASTAT update: astat = (astat & ~amask) | aset */
    uint32_t sticky;                        /* STKY bits to OR in */
    int mrw[2]; __int128 mrv[2]; int nmr;   /* MR writes (1 = MRF, 2 = MRB) */
    uint32_t alu_or;                        /* dual add/sub: OR of ALU flags */
    int dual;
} cres_t;

#define ALUMASK (AZ | AV | AN | AC | AS | AI | AF)
#define MULMASK (MN | MV | MU | MI)
#define SHMASK  (SZ | SV | SS)

static inline void put(cres_t *o, int d, uint64_t v) { o->rd[o->nw] = d; o->rv[o->nw] = v & 0xffffffffffull; o->nw++; }
static inline void aflags(cres_t *o, uint32_t mask, uint32_t set) {
    if (o->dual && (mask & ALUMASK) == ALUMASK) { o->alu_or |= set; return; }
    o->amask |= mask; o->aset = (o->aset & ~mask) | (set & mask);
}

static inline uint32_t fix_flags(uint32_t r) { return (r == 0 ? AZ : 0) | ((r >> 31) ? AN : 0); }

static __int128 sext80(__int128 v) {
    /* keep 80 bits, sign-extend */
    unsigned __int128 u = (unsigned __int128)v & ((((unsigned __int128)1) << 80) - 1);
    if (u >> 79) u |= ~((((unsigned __int128)1) << 80) - 1);
    return (__int128)u;
}

/* ALU fixed add/sub with carry/overflow */
static uint32_t addc(uint32_t x, uint32_t y, uint32_t ci, uint32_t *fl, int sat) {
    uint64_t s = (uint64_t)x + y + ci;
    uint32_t r = (uint32_t)s;
    int c = (int)(s >> 32);
    int v = (int)((~(x ^ y) & (x ^ r)) >> 31);
    if (v && sat) r = (x >> 31) ? 0x80000000u : 0x7fffffffu;
    *fl = fix_flags(r) | (v ? AV : 0) | (c ? AC : 0);
    return r;
}

static void alu_fixed(vm_t *vm, int pe, const cslot_t *s, cres_t *o) {
    pe_t *p = &vm->pe[pe];
    uint32_t x = hi32(p->r[s->x]), y = hi32(p->r[s->y]), r = 0, fl = 0;
    int sat = (vm->mode1 & M1_ALUSAT) != 0;
    uint32_t ci = (p->astat & AC) ? 1 : 0;
    switch (s->op) {
    case OP_ADD:   r = addc(x, y, 0, &fl, sat); break;
    case OP_SUB:   r = addc(x, ~y, 1, &fl, sat); break;
    case OP_ADDCI: r = addc(x, y, ci, &fl, sat); break;
    case OP_SUBCI: r = addc(x, ~y, ci, &fl, sat); break;
    case OP_AVG: {
        int64_t t = (int64_t)(int32_t)x + (int32_t)y;
        /* round to nearest-even or truncate per MODE1 TRUNCATE */
        int64_t q = t >> 1;
        if (!(vm->mode1 & M1_TRUNC) && (t & 1) && (q & 1)) q++;
        r = (uint32_t)q; uint64_t s2 = (uint64_t)x + y;
        fl = fix_flags(r) | ((s2 >> 32) ? AC : 0);
        break; }
    case OP_COMP: case OP_COMPU: {
        int lt = s->op == OP_COMP ? ((int32_t)x < (int32_t)y) : (x < y);
        fl = (x == y ? AZ : 0) | (lt ? AN : 0);
        uint32_t cacc = (p->astat >> 24) & 0xff;
        cacc = (cacc >> 1) | ((!lt && x != y) ? 0x80 : 0);
        o->amask |= 0xff000000u; o->aset = (o->aset & 0x00ffffffu) | (cacc << 24);
        aflags(o, ALUMASK, fl);
        return; }
    case OP_PASS:  r = x; fl = fix_flags(r); break;
    case OP_NEG:   r = addc(0, ~x, 1, &fl, sat); break;
    case OP_INCCI: r = addc(x, 0, ci, &fl, sat); break;
    case OP_DECCI: r = addc(x, 0xffffffffu, ci, &fl, sat); break;
    case OP_INC:   r = addc(x, 1, 0, &fl, sat); break;
    case OP_DEC:   r = addc(x, 0xffffffffu, 0, &fl, sat); break;
    case OP_ABS: {
        int neg = (int32_t)x < 0;
        if (neg) { r = addc(0, ~x, 1, &fl, sat); } else { r = x; fl = fix_flags(r); }
        fl |= neg ? AS : 0;
        break; }
    case OP_AND:   r = x & y; fl = fix_flags(r); break;
    case OP_OR:    r = x | y; fl = fix_flags(r); break;
    case OP_XOR:   r = x ^ y; fl = fix_flags(r); break;
    case OP_NOT:   r = ~x; fl = fix_flags(r); break;
    case OP_MIN:   r = (int32_t)x < (int32_t)y ? x : y; fl = fix_flags(r); break;
    case OP_MAX:   r = (int32_t)x > (int32_t)y ? x : y; fl = fix_flags(r); break;
    case OP_CLIP: {
        int32_t a = (int32_t)x; int64_t b = (int32_t)y; if (b < 0) b = -b;
        int64_t aa = a < 0 ? -(int64_t)a : a;
        int64_t res = aa < b ? a : (a < 0 ? -b : b);
        r = (uint32_t)res; fl = fix_flags(r); break; }
    default: trapf(vm, 6, "alu_fixed op %d", s->op); return;
    }
    if (fl & AV) o->sticky |= AOS;
    aflags(o, ALUMASK, fl);
    put(o, s->d, from32(r));
}

static inline int f_trunc(vm_t *vm) { return (vm->mode1 & M1_TRUNC) != 0; }
static inline int f_40(vm_t *vm) { return (vm->mode1 & M1_RND32) == 0; }

/* float result with standard flags */
static void fresult(vm_t *vm, cres_t *o, int d, double v, int ai, int is_mul) {
    int ov, un; uint64_t r;
    if (ai || isnan(v)) { r = NAN40; ov = 0; un = 0; ai = 1; }
    else r = fenc(v, f_40(vm), f_trunc(vm), &ov, &un);
    if (is_mul) {
        uint32_t fl = ((r >> 39) & 1 && r != NAN40 ? MN : 0) | (ov ? MV : 0) | (un ? MU : 0) | (ai ? MI : 0);
        if (r == NAN40) fl |= MN;
        aflags(o, MULMASK, fl);
        o->sticky |= (ov ? MVS : 0) | (un ? MUS : 0) | (ai ? MIS : 0);
    } else {
        int zero = ((r >> 31) & 0xff) == 0 && r != NAN40;
        uint32_t fl = AF | (zero ? AZ : 0) | (((r >> 39) & 1) ? AN : 0) | (ov ? AV : 0) | (ai ? AI : 0);
        aflags(o, ALUMASK, fl);
        o->sticky |= (un ? AUS : 0) | (ov ? AVS : 0) | (ai ? AIS : 0);
    }
    put(o, d, r);
}

/* a float register value moved without arithmetic (pass/neg/abs/copysign...) */
static uint64_t fclean(uint64_t r) {
    uint32_t e = (uint32_t)(r >> 31) & 0xff;
    if (e == 0) return r & (1ull << 39);          /* denormal -> signed zero */
    return r;
}
static int fisnan(uint64_t r) { return ((r >> 31) & 0xff) == 0xff && (r & 0x7fffffffull); }

static void fsimple(vm_t *vm, cres_t *o, int d, uint64_t r, int ai, uint32_t extra) {
    (void)vm;
    if (ai) r = NAN40;
    int zero = ((r >> 31) & 0xff) == 0 && !ai;
    uint32_t fl = AF | (zero ? AZ : 0) | (((r >> 39) & 1) ? AN : 0) | (ai ? AI : 0) | extra;
    aflags(o, ALUMASK, fl);
    if (ai) o->sticky |= AIS;
    put(o, d, r);
}

/* RECIPS/RSQRTS seeds: the hardware uses ROM tables we do not have. We use the
 * exact reciprocal truncated to the documented seed precision ([inferred]). */
static double seed_trunc(double v, int bits) {
    int e; double f = frexp(v, &e);
    double sc = ldexp(f, bits);
    return ldexp(floor(sc), e - bits);
}

static void alu_float(vm_t *vm, int pe, const cslot_t *s, cres_t *o) {
    pe_t *p = &vm->pe[pe];
    uint64_t rx = p->r[s->x], ry = p->r[s->y];
    fval_t a = fdec(rx), b = fdec(ry);
    int sat = (vm->mode1 & M1_ALUSAT) != 0;
    switch (s->op) {
    case OP_FADD: fresult(vm, o, s->d, a.v + b.v, a.nan || b.nan || (a.inf && b.inf && a.neg != b.neg), 0); return;
    case OP_FSUB: fresult(vm, o, s->d, a.v - b.v, a.nan || b.nan || (a.inf && b.inf && a.neg == b.neg), 0); return;
    case OP_FAVG: fresult(vm, o, s->d, (a.v + b.v) * 0.5, a.nan || b.nan || (a.inf && b.inf && a.neg != b.neg), 0); return;
    case OP_FABSADD: fresult(vm, o, s->d, fabs(a.v + b.v), a.nan || b.nan || (a.inf && b.inf && a.neg != b.neg), 0); return;
    case OP_FABSSUB: fresult(vm, o, s->d, fabs(a.v - b.v), a.nan || b.nan || (a.inf && b.inf && a.neg == b.neg), 0); return;
    case OP_FCOMP: {
        int ai = a.nan || b.nan;
        int eq = !ai && a.v == b.v, lt = !ai && a.v < b.v;
        uint32_t fl = AF | (eq ? AZ : 0) | (lt ? AN : 0) | (ai ? AI : 0);
        uint32_t cacc = (p->astat >> 24) & 0xff;
        cacc = (cacc >> 1) | ((!ai && a.v > b.v) ? 0x80 : 0);
        o->amask |= 0xff000000u; o->aset = (o->aset & 0x00ffffffu) | (cacc << 24);
        aflags(o, ALUMASK, fl);
        if (ai) o->sticky |= AIS;
        return; }
    case OP_FPASS: fsimple(vm, o, s->d, fclean(rx), a.nan, 0); return;
    case OP_FNEG:  fsimple(vm, o, s->d, fclean(rx) ^ (1ull << 39), a.nan, 0); return;
    case OP_FABS:  fsimple(vm, o, s->d, fclean(rx) & ~(1ull << 39), a.nan, a.neg ? AS : 0); return;
    case OP_FCOPYSIGN: fsimple(vm, o, s->d, (fclean(rx) & ~(1ull << 39)) | (ry & (1ull << 39)), a.nan || b.nan, 0); return;
    case OP_FMIN: case OP_FMAX: {
        if (a.nan || b.nan) { fsimple(vm, o, s->d, 0, 1, 0); return; }
        uint64_t ca = fclean(rx), cb = fclean(ry), r;
        if (a.v == b.v) {    /* +0/-0: MIN gives -0, MAX gives +0 */
            int an = (ca >> 39) & 1, bn = (cb >> 39) & 1;
            r = s->op == OP_FMIN ? ((an || bn) ? (ca | cb) : ca) : (an && bn ? ca : (an ? cb : ca));
        } else r = (s->op == OP_FMIN) == (a.v < b.v) ? ca : cb;
        fsimple(vm, o, s->d, r, 0, 0); return; }
    case OP_FCLIP: {
        if (a.nan || b.nan) { fsimple(vm, o, s->d, 0, 1, 0); return; }
        uint64_t ca = fclean(rx), cb = fclean(ry) & ~(1ull << 39), r;
        if (fabs(a.v) < fabs(b.v)) r = ca; else r = cb | (ca & (1ull << 39));
        fsimple(vm, o, s->d, r, 0, 0); return; }
    case OP_FRND: {   /* PGR: rounds to a 32-bit boundary whatever RND32 says */
        int ov, un; uint64_t r = a.nan ? NAN40 : fenc(a.v, 0, f_trunc(vm), &ov, &un);
        if (a.nan) { ov = un = 0; }
        int zero = ((r >> 31) & 0xff) == 0 && !a.nan;
        uint32_t fl = AF | (zero ? AZ : 0) | (((r >> 39) & 1) ? AN : 0) | (ov ? AV : 0) | (a.nan ? AI : 0);
        aflags(o, ALUMASK, fl); o->sticky |= (ov ? AVS : 0) | (a.nan ? AIS : 0);
        put(o, s->d, r); return; }
    case OP_FSCALB: {
        int32_t sc = (int32_t)hi32(ry);
        if (a.nan) { fresult(vm, o, s->d, 0, 1, 0); return; }
        fresult(vm, o, s->d, ldexp(a.v, sc), 0, 0); return; }
    case OP_FMANT: {
        uint32_t r, fl = AF;
        if (a.nan || a.inf) { r = 0xffffffffu; fl |= a.inf ? AV : AI; }
        else if (a.zero) r = 0;
        else r = (1u << 31) | (uint32_t)(rx & 0x7fffffffull);   /* 1.31, hidden bit explicit */
        fl |= (r == 0 ? AZ : 0) | (a.neg ? AS : 0);
        aflags(o, ALUMASK, fl); if (fl & AV) o->sticky |= AVS; if (fl & AI) o->sticky |= AIS;
        put(o, s->d, from32(r)); return; }
    case OP_FLOGB: {
        uint32_t r, fl = AF;
        if (a.nan) { r = 0xffffffffu; fl |= AI; o->sticky |= AIS; aflags(o, ALUMASK, fl); put(o, s->d, NAN40); return; }
        if (a.inf || a.zero) {
            fl |= AV; o->sticky |= AVS;
            if (sat) r = a.inf ? 0x7fffffffu : 0x80000000u;
            else { /* floating-point +inf / -inf */
                aflags(o, ALUMASK, fl | (a.zero ? AN : 0));
                put(o, s->d, a.inf ? (0xffull << 31) : ((1ull << 39) | (0xffull << 31))); return; }
        } else r = (uint32_t)((int32_t)((rx >> 31) & 0xff) - 127);
        fl |= fix_flags(r);
        aflags(o, ALUMASK, fl); put(o, s->d, from32(r)); return; }
    case OP_FIX: case OP_TRUNC: case OP_FIXBY: case OP_TRUNCBY: {
        int byr = s->op == OP_FIXBY || s->op == OP_TRUNCBY;
        int trunc_op = s->op == OP_TRUNC || s->op == OP_TRUNCBY;
        uint32_t fl = AF, r;
        if (a.nan) { aflags(o, ALUMASK, fl | AI); o->sticky |= AIS; put(o, s->d, NAN40); return; }
        double v = a.v;
        if (byr && !a.inf && !a.zero) v = ldexp(v, (int32_t)hi32(ry));
        double q;
        if (trunc_op) q = trunc(v);
        else if (f_trunc(vm)) q = floor(v);                  /* PGR: FIX with TRUNCATE toward -inf */
        else q = nearbyint(v);                               /* round to nearest even */
        int ov = a.inf || q > 2147483647.0 || q < -2147483648.0;
        if (ov) {
            fl |= AV; o->sticky |= AVS;
            if (!sat) { fl |= AI; o->sticky |= AIS; aflags(o, ALUMASK, fl | (v < 0 ? AN : 0)); put(o, s->d, NAN40); return; }
            r = v < 0 ? 0x80000000u : 0x7fffffffu;
        } else r = (uint32_t)(int32_t)q;
        fl |= fix_flags(r);
        aflags(o, ALUMASK, fl); put(o, s->d, from32(r)); return; }
    case OP_FLOAT: case OP_FLOATBY: {
        double v = (double)(int32_t)hi32(rx);
        int ov, un;
        if (s->op == OP_FLOATBY) v = ldexp(v, (int32_t)hi32(ry));
        /* PGR: rounded to a 40-bit boundary regardless of RND32 */
        uint64_t r = fenc(v, 1, f_trunc(vm), &ov, &un);
        uint32_t fl = AF | ((((r >> 31) & 0xff) == 0) ? AZ : 0) | (((r >> 39) & 1) ? AN : 0) | (ov ? AV : 0);
        aflags(o, ALUMASK, fl);
        o->sticky |= (ov ? AVS : 0) | (un ? AUS : 0);
        put(o, s->d, r); return; }
    case OP_FRECIPS: {
        uint32_t fl = AF; uint64_t r;
        if (a.nan) { fsimple(vm, o, s->d, 0, 1, 0); return; }
        if (a.zero) { r = (a.neg ? (1ull << 39) : 0) | (0xffull << 31); fl |= AV; o->sticky |= AVS; }
        else if (a.inf) { r = a.neg ? (1ull << 39) : 0; fl |= AZ; }
        else {
            int e = (int)((rx >> 31) & 0xff) - 127;
            if (e > 125) { r = a.neg ? (1ull << 39) : 0; fl |= AZ; }
            else {
                /* 8-bit-accurate seed: mantissa from the 7 MSBs of the input fraction */
                uint32_t idx = (uint32_t)((rx >> 24) & 0x7f);
                double mx = 1.0 + (idx + 0.5) / 128.0;
                double seed = seed_trunc(1.0 / mx, 8);  /* in (0.5, 1] */
                double res = ldexp(seed, -e);
                int ov2, un2;
                r = fenc(a.neg ? -res : res, 1, 1, &ov2, &un2);
                if (un2) fl |= AZ;
            }
        }
        fl |= a.neg ? AN : 0;
        aflags(o, ALUMASK, fl); put(o, s->d, r); return; }
    case OP_FRSQRTS: {
        uint32_t fl = AF; uint64_t r;
        if (a.nan || (a.neg && !a.zero)) { fsimple(vm, o, s->d, 0, 1, 0); return; }
        if (a.zero) { r = (a.neg ? (1ull << 39) : 0) | (0xffull << 31); fl |= AV | (a.neg ? AN : 0); o->sticky |= AVS; }
        else if (a.inf) { r = 0; fl |= AZ; }
        else {
            int e = (int)((rx >> 31) & 0xff) - 127;
            uint32_t idx = (uint32_t)((rx >> 25) & 0x3f) | (((rx >> 31) & 1) << 6);
            double mx = 1.0 + (((idx & 0x3f) + 0.5) / 64.0);
            if (e & 1) mx *= 2.0;    /* odd exponent: fold one factor 2 into the mantissa */
            double seed = seed_trunc(1.0 / sqrt(mx), 4);
            int eh = (e - (e & 1)) / 2;
            double res = ldexp(seed, -eh);
            int ov2, un2; r = fenc(res, 1, 1, &ov2, &un2);
        }
        aflags(o, ALUMASK, fl); put(o, s->d, r); return; }
    default: trapf(vm, 6, "alu_float op %d", s->op); return;
    }
}

/* multiplier */
static __int128 mul_product(uint32_t x, uint32_t y, int mod) {
    __int128 X = (mod & MOD_XS) ? (__int128)(int32_t)x : (__int128)x;
    __int128 Y = (mod & MOD_YS) ? (__int128)(int32_t)y : (__int128)y;
    __int128 P = X * Y;
    if ((mod & MOD_FRAC) && (mod & MOD_XS) && (mod & MOD_YS)) P <<= 1;
    return P;
}
/* round-to-nearest at bit 32 (fractional) */
static __int128 mr_round(__int128 v) {
    __int128 low = v & 0xffffffff;
    __int128 r = v + 0x80000000;
    if (low == 0x80000000) r &= ~(((__int128)1) << 32);   /* tie: to even */
    return r & ~(__int128)0xffffffff;
}
static uint32_t mul_flags(__int128 v, int mod) {
    uint32_t fl = 0;
    int sgn = (mod & (MOD_XS | MOD_YS)) != 0;
    if (v < 0 && sgn) fl |= MN;
    int frac = (mod & MOD_FRAC) != 0;
    int ub = sgn ? (frac ? 33 : 49) : (frac ? 32 : 48);   /* upper bits that must be sign */
    int top = 80 - ub;            /* bits below the checked region */
    __int128 hi = v >> top;       /* arithmetic */
    if (sgn) { if (!(hi == 0 || hi == -1)) fl |= MV; }
    else { if (hi != 0) fl |= MV; }
    if (frac) {
        __int128 up = v >> 32;
        if ((up == 0 || (sgn && up == -1)) && (v & 0xffffffff) != 0) fl |= MU;
    }
    return fl;
}

static void mul_unit(vm_t *vm, int pe, const cslot_t *s, cres_t *o) {
    pe_t *p = &vm->pe[pe];
    int mod = s->mod;
    switch (s->op) {
    case OP_FMUL: {
        fval_t a = fdec(p->r[s->x]), b = fdec(p->r[s->y]);
        int ai = a.nan || b.nan || (a.inf && b.zero) || (a.zero && b.inf);
        fresult(vm, o, s->d, a.v * b.v, ai, 1);
        return; }
    case OP_MUL: {
        int tgt = s->imm & 3, acc = (s->imm >> 2) & 3, accsrc = (s->imm >> 4) & 3;
        uint32_t x = hi32(p->r[s->x]), y = hi32(p->r[s->y]);
        __int128 P = mul_product(x, y, mod), v;
        if (acc == MA_NONE) v = P;
        else { __int128 m = p->mr[accsrc ? accsrc : 1]; v = acc == MA_ADD ? m + P : m - P; }
        v = sext80(v);
        if ((mod & MOD_RND) && (mod & MOD_FRAC)) v = sext80(mr_round(v));
        uint32_t fl = mul_flags(v, mod);
        aflags(o, MULMASK, fl);
        if (fl & MV) o->sticky |= MOS;
        if (tgt == MT_RN) {
            uint32_t r = (mod & MOD_FRAC) ? (uint32_t)(v >> 32) : (uint32_t)v;
            put(o, s->d, from32(r));
        } else { o->mrw[o->nmr] = tgt; o->mrv[o->nmr] = v; o->nmr++; }
        return; }
    case OP_MRCLR: {
        o->mrw[o->nmr] = s->imm & 3; o->mrv[o->nmr] = 0; o->nmr++;
        aflags(o, MULMASK, 0); return; }
    case OP_MRRND: case OP_MRSAT: {
        int src = s->imm & 3, tgt = (s->imm >> 2) & 3;
        __int128 v = p->mr[src];
        uint32_t fl;
        if (s->op == OP_MRRND) {
            if (mod & MOD_FRAC) v = sext80(mr_round(v));
            fl = mul_flags(v, mod);
        } else {
            int sgn = (mod & (MOD_XS | MOD_YS)) != 0, frac = (mod & MOD_FRAC) != 0;
            __int128 mx, mn;
            if (sgn) {
                mx = frac ? ((((__int128)1) << 63) - 1) : 0x7fffffff;
                mn = frac ? -(((__int128)1) << 63) : -(__int128)0x80000000;
            } else { mx = frac ? ((((__int128)1) << 64) - 1) : 0xffffffff; mn = 0; }
            if (v > mx) v = mx; else if (v < mn) v = mn;
            fl = mul_flags(v, mod) & ~MV;
        }
        aflags(o, MULMASK, fl);
        if (fl & MV) o->sticky |= MOS;
        if (tgt == MT_RN) {
            uint32_t r = (mod & MOD_FRAC) ? (uint32_t)(v >> 32) : (uint32_t)v;
            put(o, s->d, from32(r));
        } else { o->mrw[o->nmr] = tgt; o->mrv[o->nmr] = v; o->nmr++; }
        return; }
    case OP_MR2R: {
        int part = s->imm & 3, mr = (s->imm >> 2) & 3;
        __int128 v = p->mr[mr];
        uint32_t r;
        if (part == 0) r = (uint32_t)v;
        else if (part == 1) r = (uint32_t)(v >> 32);
        else r = (uint32_t)(int32_t)(int16_t)(v >> 64);
        aflags(o, MULMASK, 0);
        put(o, s->d, from32(r));
        return; }
    case OP_R2MR: {
        int part = s->imm & 3, mr = (s->imm >> 2) & 3;
        __int128 v = p->mr[mr]; uint32_t r = hi32(p->r[s->x]);
        unsigned __int128 u = (unsigned __int128)v;
        if (part == 0) { u = (u & ~(unsigned __int128)0xffffffff) | r; }
        else if (part == 1) {   /* sign-extends into MR2 */
            u = (u & (unsigned __int128)0xffffffff) | ((unsigned __int128)(__int128)(int32_t)r << 32);
        } else { u = (u & ((((unsigned __int128)1) << 64) - 1)) | ((unsigned __int128)(__int128)(int16_t)r << 64); }
        o->mrw[o->nmr] = mr; o->mrv[o->nmr] = sext80((__int128)u); o->nmr++;
        aflags(o, MULMASK, 0);
        return; }
    }
    trapf(vm, 6, "mul op %d", s->op);
}

/* shifter */
static void shifter(vm_t *vm, int pe, const cslot_t *s, cres_t *o) {
    pe_t *p = &vm->pe[pe];
    uint32_t x = hi32(p->r[s->x]);
    uint32_t yv = (s->mod & MOD_IMM) ? (uint32_t)(int32_t)s->imm : hi32(p->r[s->y]);
    uint32_t r = 0, fl = 0;
    int write = 1;
    switch (s->op) {
    case OP_LSHIFT: case OP_LSHIFT_OR: case OP_ASHIFT: case OP_ASHIFT_OR: {
        int sh = (int8_t)(yv & 0xff);
        if (sh >= 32) r = 0;
        else if (sh >= 0) r = x << sh;
        else if (sh > -32) r = (s->op == OP_ASHIFT || s->op == OP_ASHIFT_OR) ? (uint32_t)((int32_t)x >> (-sh)) : x >> (-sh);
        else r = (s->op == OP_ASHIFT || s->op == OP_ASHIFT_OR) ? (uint32_t)((int32_t)x >> 31) : 0;
        if (s->op == OP_LSHIFT_OR || s->op == OP_ASHIFT_OR) r |= hi32(p->r[s->d]);
        fl = (r == 0 ? SZ : 0) | (sh > 0 && x != 0 ? SV : 0);
        /* SV: "input shifted to the left by more than 0": approximated as bits lost */
        if (sh > 0) { fl &= ~SV; if (sh >= 32 ? x != 0 : ((uint64_t)x << sh) >> 32 != 0) fl |= SV; }
        break; }
    case OP_ROT: {
        int sh = (int8_t)(yv & 0xff); sh = ((sh % 32) + 32) % 32;
        r = sh ? (x << sh) | (x >> (32 - sh)) : x;
        fl = r == 0 ? SZ : 0; break; }
    case OP_FEXT: case OP_FEXT_SE: {
        int bit6 = yv & 0x3f, len6 = (yv >> 6) & 0x3f;
        uint64_t f = (bit6 >= 32) ? 0 : ((uint64_t)x >> bit6);
        if (len6 < 32) f &= (1ull << len6) - 1;
        r = (uint32_t)f;
        if (s->op == OP_FEXT_SE && len6 > 0 && len6 < 32 && bit6 + len6 <= 32 && ((r >> (len6 - 1)) & 1))
            r |= ~((1u << len6) - 1);
        fl = (r == 0 ? SZ : 0) | (bit6 + len6 > 32 ? SV : 0); break; }
    case OP_FDEP: case OP_FDEP_SE: case OP_FDEP_OR: case OP_FDEP_OR_SE: {
        int bit6 = yv & 0x3f, len6 = (yv >> 6) & 0x3f;
        uint64_t fld = len6 >= 32 ? x : (x & ((1u << len6) - 1));
        if ((s->op == OP_FDEP_SE || s->op == OP_FDEP_OR_SE) && len6 > 0 && len6 < 32 && ((fld >> (len6 - 1)) & 1))
            fld |= ~((1ull << len6) - 1);   /* sign-extend to the left */
        uint64_t sh = bit6 >= 64 ? 0 : (fld << bit6);
        r = (uint32_t)sh;
        if (s->op == OP_FDEP_OR || s->op == OP_FDEP_OR_SE) r |= hi32(p->r[s->d]);
        fl = (r == 0 ? SZ : 0) | (bit6 + len6 > 32 ? SV : 0); break; }
    case OP_BSET: case OP_BCLR: case OP_BTGL: case OP_BTST: {
        int32_t pos = (s->mod & MOD_IMM) ? (int32_t)(yv & 0xfff) : (int32_t)yv;
        int ok = pos >= 0 && pos <= 31;
        uint32_t m = ok ? (1u << pos) : 0;
        if (s->op == OP_BSET) r = x | m;
        else if (s->op == OP_BCLR) r = x & ~m;
        else if (s->op == OP_BTGL) r = x ^ m;
        else { write = 0; r = x & m; }
        if (s->op == OP_BTST) fl = ((!ok || !(x & m)) ? SZ : 0) | (ok ? 0 : SV);
        else fl = (r == 0 ? SZ : 0) | (pos > 31 ? SV : 0);
        break; }
    case OP_EXP: case OP_EXPEX: {
        int sb = 1; uint32_t sgn = x >> 31;
        while (sb < 32 && ((x >> (31 - sb)) & 1) == sgn) sb++;
        int32_t e = -(sb - 1);
        int avf = (vm->pe[pe].astat & AV) != 0;
        if (s->op == OP_EXPEX && avf) e = 1;
        r = (uint32_t)e;
        fl = (r == 0 ? SZ : 0) | ((s->op == OP_EXPEX ? (avf ^ sgn) : sgn) ? SS : 0);
        break; }
    case OP_LEFTZ: case OP_LEFTO: {
        uint32_t v = s->op == OP_LEFTZ ? x : ~x;
        r = v ? (uint32_t)__builtin_clz(v) : 32;
        fl = ((s->op == OP_LEFTZ ? (x >> 31) : !(x >> 31)) ? SZ : 0) | (r == 32 ? SV : 0);
        break; }
    default:
        trapf(vm, 6, "shifter op %d", s->op); return;
    }
    aflags(o, SHMASK, fl);
    if (write) put(o, s->d, from32(r));
}

static inline int is_alu_fixed(int op) { return op >= OP_ADD && op <= OP_CLIP; }
static inline int is_alu_float(int op) { return op >= OP_FADD && op <= OP_FCLIP; }
static inline int is_mul(int op) { return op >= OP_MUL && op <= OP_R2MR; }

/* ---- fast path for single-slot computes ----
 * Integer ALU ops, and 32-bit float add/sub/mul/compare/pass when MODE1 has RND32 set,
 * TRUNCATE clear and both operands have zero extension bits: native IEEE single
 * arithmetic is correctly rounded, which is what the generic path computes (double,
 * then rounded to 24 bits: innocuous double rounding for + - *). Denormal inputs and
 * outputs are flushed to signed zero as in the generic path. Returns 0 if not handled. */
static inline float bits2f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static inline uint32_t f2bits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

static inline __attribute__((always_inline)) int fast_compute(vm_t *vm, int pe, const cslot_t *s) {
    pe_t *p = &vm->pe[pe];
    int op = s->op;
    if (op >= OP_FADD && op <= OP_FCLIP) {
        if ((vm->mode1 & (M1_RND32 | M1_TRUNC)) != M1_RND32) return 0;
        uint64_t rx = p->r[s->x], ry = p->r[s->y];
        if ((rx | ry) & 0xff) return 0;
        uint32_t ux = (uint32_t)(rx >> 8), uy = (uint32_t)(ry >> 8);
        uint32_t ex = ux & 0x7f800000u, ey = uy & 0x7f800000u;
        if (ex == 0x7f800000u || ey == 0x7f800000u) return 0;      /* inf/NaN: generic path */
        if (!ex) ux &= 0x80000000u;
        if (!ey) uy &= 0x80000000u;
        float a = bits2f(ux), b = bits2f(uy), r;
        switch (op) {
        case OP_FADD: r = a + b; break;
        case OP_FSUB: r = a - b; break;
        case OP_FCOMP: {
            uint32_t fl = AF | (a == b ? AZ : 0) | (a < b ? AN : 0);
            uint32_t cacc = (p->astat >> 24) & 0xff;
            cacc = (cacc >> 1) | (a > b ? 0x80 : 0);
            p->astat = (p->astat & ~(ALUMASK | 0xff000000u)) | fl | (cacc << 24);
            return 1; }
        case OP_FPASS: case OP_FNEG: case OP_FABS: {
            uint32_t u = op == OP_FPASS ? ux : op == OP_FNEG ? (ux ^ 0x80000000u) : (ux & 0x7fffffffu);
            uint32_t fl = AF | ((u & 0x7f800000u) == 0 ? AZ : 0) | ((u >> 31) ? AN : 0) | (op == OP_FABS && (ux >> 31) ? AS : 0);
            p->astat = (p->astat & ~ALUMASK) | fl;
            p->r[s->d] = from32(u);
            return 1; }
        default: return 0;
        }
        uint32_t ur = f2bits(r), e = ur & 0x7f800000u, fl = AF;
        if (e == 0x7f800000u) { fl |= AV; p->stky |= AVS; }                 /* overflow to inf */
        else if (e == 0) { if (ur & 0x7fffffu) p->stky |= AUS; ur &= 0x80000000u; fl |= AZ; }
        fl |= (ur >> 31) ? AN : 0;
        p->astat = (p->astat & ~ALUMASK) | fl;
        p->r[s->d] = from32(ur);
        return 1;
    }
    if (op == OP_FMUL) {
        if ((vm->mode1 & (M1_RND32 | M1_TRUNC)) != M1_RND32) return 0;
        uint64_t rx = p->r[s->x], ry = p->r[s->y];
        if ((rx | ry) & 0xff) return 0;
        uint32_t ux = (uint32_t)(rx >> 8), uy = (uint32_t)(ry >> 8);
        uint32_t ex = ux & 0x7f800000u, ey = uy & 0x7f800000u;
        if (ex == 0x7f800000u || ey == 0x7f800000u) return 0;
        if (!ex) ux &= 0x80000000u;
        if (!ey) uy &= 0x80000000u;
        uint32_t ur = f2bits(bits2f(ux) * bits2f(uy)), e = ur & 0x7f800000u, fl = 0;
        if (e == 0x7f800000u) { fl |= MV; p->stky |= MVS; }
        else if (e == 0 && (ur & 0x7fffffu)) { fl |= MU; p->stky |= MUS; ur &= 0x80000000u; }
        else if (e == 0) ur &= 0x80000000u;
        fl |= (ur >> 31) ? MN : 0;
        p->astat = (p->astat & ~MULMASK) | fl;
        p->r[s->d] = from32(ur);
        return 1;
    }
    if (op == OP_FEXT && (s->mod & MOD_IMM)) {
        uint32_t x = hi32(p->r[s->x]), yv = (uint32_t)(int32_t)s->imm;
        int bit6 = yv & 0x3f, len6 = (yv >> 6) & 0x3f;
        if (bit6 + len6 > 32 || len6 == 0) return 0;
        uint32_t r = (uint32_t)(((uint64_t)x >> bit6) & ((1ull << len6) - 1));
        p->astat = (p->astat & ~SHMASK) | (r == 0 ? SZ : 0);
        p->r[s->d] = from32(r);
        return 1;
    }
    if ((op == OP_LSHIFT || op == OP_ASHIFT) && (s->mod & MOD_IMM)) {
        uint32_t x = hi32(p->r[s->x]);
        int sh = (int8_t)(s->imm & 0xff);
        if (sh <= -32 || sh >= 32) return 0;
        uint32_t r, fl;
        if (sh >= 0) {
            r = x << sh;
            fl = (sh > 0 && (((uint64_t)x << sh) >> 32) != 0) ? SV : 0;
        } else {
            r = op == OP_ASHIFT ? (uint32_t)((int32_t)x >> (-sh)) : x >> (-sh);
            fl = 0;
        }
        fl |= r == 0 ? SZ : 0;
        p->astat = (p->astat & ~SHMASK) | fl;
        p->r[s->d] = from32(r);
        return 1;
    }
    if (op == OP_TRUNC && !(vm->mode1 & M1_ALUSAT)) {
        uint64_t rx = p->r[s->x];
        uint32_t e = (uint32_t)(rx >> 31) & 0xff;
        if (e == 0xff || e >= 127 + 31) return 0;      /* inf/NaN/overflow: generic path */
        int32_t r;
        if (e < 127) r = 0;                            /* |x| < 1 (incl. zero and denormals) */
        else {
            uint64_t man = (1ull << 31) | (rx & 0x7fffffffull);
            r = (int32_t)(man >> (31 - (e - 127)));
            if ((rx >> 39) & 1) r = -r;
        }
        uint32_t ur = (uint32_t)r;
        p->astat = (p->astat & ~ALUMASK) | AF | fix_flags(ur);
        p->r[s->d] = from32(ur);
        return 1;
    }
    if (op >= OP_ADD && op <= OP_CLIP) {
        uint32_t x = hi32(p->r[s->x]), y = hi32(p->r[s->y]), r, fl;
        int sat = (vm->mode1 & M1_ALUSAT) != 0;
        switch (op) {
        case OP_ADD: r = addc(x, y, 0, &fl, sat); break;
        case OP_SUB: r = addc(x, ~y, 1, &fl, sat); break;
        case OP_INC: r = addc(x, 1, 0, &fl, sat); break;
        case OP_DEC: r = addc(x, 0xffffffffu, 0, &fl, sat); break;
        case OP_PASS: r = x; fl = fix_flags(r); break;
        case OP_AND: r = x & y; fl = fix_flags(r); break;
        case OP_OR: r = x | y; fl = fix_flags(r); break;
        case OP_XOR: r = x ^ y; fl = fix_flags(r); break;
        case OP_NOT: r = ~x; fl = fix_flags(r); break;
        case OP_MIN: r = (int32_t)x < (int32_t)y ? x : y; fl = fix_flags(r); break;
        case OP_MAX: r = (int32_t)x > (int32_t)y ? x : y; fl = fix_flags(r); break;
        default: return 0;
        }
        if (fl & AV) p->stky |= AOS;
        p->astat = (p->astat & ~ALUMASK) | fl;
        p->r[s->d] = from32(r);
        return 1;
    }
    return 0;
}

static __attribute__((noinline)) void exec_compute_slow(vm_t *vm, int pe, const insn_t *in);
static inline __attribute__((always_inline)) void exec_compute(vm_t *vm, int pe, const insn_t *in) {
    if (in->ckind == CK_SINGLE && !vm->no_fast && fast_compute(vm, pe, &in->cs[0])) return;
    exec_compute_slow(vm, pe, in);
}
static __attribute__((noinline)) void exec_compute_slow(vm_t *vm, int pe, const insn_t *in) {
    cres_t o; o.nw = 0; o.aset = 0; o.amask = 0; o.sticky = 0; o.nmr = 0; o.alu_or = 0;
    o.dual = (in->ckind == CK_DUAL || in->ckind == CK_MULDUAL);
    for (int i = 0; i < in->ncomp; i++) {
        const cslot_t *s = &in->cs[i];
        int op = s->op;
        if (is_alu_fixed(op)) alu_fixed(vm, pe, s, &o);
        else if (is_alu_float(op)) alu_float(vm, pe, s, &o);
        else if (is_mul(op)) mul_unit(vm, pe, s, &o);
        else if (op >= OP_LSHIFT && op < OP_COUNT) shifter(vm, pe, s, &o);
        else { trapf(vm, 6, "bad compute op %d", op); return; }
    }
    pe_t *p = &vm->pe[pe];
    if (o.dual) {   /* PGR 3-33: ALU flags of the two operations are ORed */
        o.amask |= ALUMASK; o.aset = (o.aset & ~ALUMASK) | (o.alu_or & ALUMASK);
        if (o.alu_or & AV) o.sticky |= (o.alu_or & AF) ? AVS : AOS;
    }
    for (int i = 0; i < o.nw; i++) p->r[o.rd[i]] = o.rv[i];
    for (int i = 0; i < o.nmr; i++) p->mr[o.mrw[i]] = o.mrv[i];
    p->astat = (p->astat & ~o.amask) | (o.aset & o.amask);
    p->stky |= o.sticky;
}

/* ------------------------------------------------------------------ */
/* DAG / memory transfers                                              */

static inline uint32_t width_bytes(int w) {
    switch (w) { case W_NW: return 4; case W_LW: return 8; case W_SW: case W_SWSE: return 2; case W_BW: case W_BWSE: return 1; }
    return 4;
}
/* modifier scale (byte space): NW/LW in words, SW in halfwords, BW in bytes */
static inline uint32_t mscale(uint32_t addr, int w) {
    if (space_of(addr) != SP_BYTE) return 1;
    switch (w) { case W_SW: case W_SWSE: return 2; case W_BW: case W_BWSE: return 1; }
    return 4;
}

static uint32_t circ(vm_t *vm, int i, uint32_t newv, int32_t delta, uint32_t scale) {
    if (!(vm->mode1 & M1_CBUFEN) || vm->L[i] == 0) return newv;
    uint32_t b = vm->B[i], len = vm->L[i] * scale;
    uint32_t r = newv;
    if (delta >= 0) { if (r >= b + len) r -= len; }
    else { if (r < b) r += len; }
    if (r != newv) {
        vm->stat_circ_wraps++;
        trapf(vm, 7, "circular buffer wrap on I%d (B=%#x L=%#x): length unit unverified", i, b, vm->L[i]);
    }
    return r;
}

/* load the value of width w at byte address b into a 40-bit register value */
static int load_val(vm_t *vm, uint32_t b, int w, uint64_t *out, uint64_t *out2) {
    uint64_t v;
    switch (w) {
    case W_NW: if (!rd_bytes(vm, b, 4, &v)) return 0; *out = from32((uint32_t)v); return 1;
    case W_LW: if (!rd_bytes(vm, b, 8, &v)) return 0; *out = from32((uint32_t)v); *out2 = from32((uint32_t)(v >> 32)); return 1;
    case W_SW: if (!rd_bytes(vm, b, 2, &v)) return 0; *out = from32((uint32_t)v & 0xffff); return 1;
    case W_SWSE: if (!rd_bytes(vm, b, 2, &v)) return 0; *out = from32((uint32_t)(int32_t)(int16_t)v); return 1;
    case W_BW: if (!rd_bytes(vm, b, 1, &v)) return 0; *out = from32((uint32_t)v & 0xff); return 1;
    case W_BWSE: if (!rd_bytes(vm, b, 1, &v)) return 0; *out = from32((uint32_t)(int32_t)(int8_t)v); return 1;
    }
    trapf(vm, 8, "unsupported access width %d", w); return 0;
}
static int store_val(vm_t *vm, uint32_t b, int w, uint64_t v, uint64_t v2) {
    switch (w) {
    case W_NW: return wr_bytes(vm, b, 4, hi32(v));
    case W_LW: return wr_bytes(vm, b, 8, (uint64_t)hi32(v) | ((uint64_t)hi32(v2) << 32));
    case W_SW: case W_SWSE: return wr_bytes(vm, b, 2, hi32(v) & 0xffff);
    case W_BW: case W_BWSE: return wr_bytes(vm, b, 1, hi32(v) & 0xff);
    }
    trapf(vm, 8, "unsupported access width %d", w); return 0;
}

/* pending register writes from loads (applied after compute) */
typedef struct { int n; int code[4]; uint64_t v[4]; } lwrites_t;

/* one memory transfer; cx/cy = conditions of PEx/PEy (cy only used in SIMD) */
static void do_mem(vm_t *vm, const mem_t *m, int cx, int cy, lwrites_t *lw, uint32_t *ireg_upd, int *iupd_idx, int sim) {
    uint32_t addr, ea; int i = m->ireg;
    uint32_t scale;
    int32_t delta = 0;
    if (m->mode == AM_ABS) { addr = (uint32_t)m->off; ea = addr; scale = 1; }
    else {
        addr = vm->I[i];
        scale = mscale(addr, m->width);
        delta = m->mreg == 0xff ? m->off : (int32_t)vm->M[m->mreg];
        uint32_t base = addr;
        if (vm->imm_align && m->mreg == 0xff && scale > 1) {
            /* immediate-offset forms (Types 4, 15) address in units of the access width:
             * the low bits of I are dropped. DN2 0xb82e48 `R2 = DM(I5 - 0x8187)` with an odd
             * byte pointer in I5 reads a struct word; selcc avoids Type 4 for byte pointers
             * because "writes to a byte address went to a different ... bank" (isel.rs).
             * M-register forms (Types 1, 3, 6, 16) stay byte-exact: selache's HW-validated
             * cases do misaligned Type 3 accesses and need byte-exact results. Off by default:
             * the DN2 evidence for it went away with the Type 7 short-word scaling. */
            base = addr & ~(scale - 1);
        }
        if (m->mode == AM_PRE) ea = base + (uint32_t)(delta * (int32_t)scale);
        else ea = base;
    }
    int doit_x = cx, doit_y = sim ? cy : 0;
    int compl_ok = sim && (m->flags & MF_DREG) && !(m->flags & MF_IMMDATA);
    /* SIMD stores of a register without a complement (I, M, L, B, ...) or of immediate
     * data write the same value to the implicit (PEy) location too: the DN2 master stage
     * clears its 2-samples-per-pass SIMD buses with `DM(I4, M4) = M12` (0x1c2026) and
     * would accumulate garbage in every odd sample otherwise. [inferred] */
    int same_y = sim && m->write && !compl_ok;
    uint32_t b = to_byte(ea);
    uint32_t wb = width_bytes(m->width);
    if (UNLIKELY(b & (wb > 4 ? 3 : wb - 1)) && (cx || cy) && !(b >= 0x30000u && b < 0x40000u)) {
        /* (core MMR space 0x30000-0x3ffff is word-addressed: one register per address) */
        vm->stat_misaligned++;
        if (vm->n_mis < 64) vm->mis_pc[vm->n_mis++] = vm->pc;
        if (vm->align_strict == 2 || (vm->align_strict == 4 && (b & 1))) {
            trapf(vm, 8, "misaligned %u-byte access at %#x (I%d=%#x)", wb, b, i, m->mode == AM_ABS ? 0 : vm->I[i]);
            return;
        }
        /* misaligned accesses: 0 byte-exact (selache's HW-validated cases need it for L1;
         * the DN2 frame unpacker 0x1c28e3 reads two 16-bit words with one 32-bit load at
         * an address 2 mod 4); 1 drop the low address bits; 2 trap; 3 byte-exact in L1,
         * dropped in L2/DDR (an experiment); 4 (DN2 runs) byte-exact if halfword-aligned,
         * trap on odd addresses. */
        if (vm->align_strict == 1 || (vm->align_strict == 3 && b >= 0x20000000u))
            b &= ~(uint32_t)(wb > 4 ? 3 : wb - 1);
    }
    /* implicit (PEy) address: next word; broadcast loads (BDCST1/9 via I1/I9) read the same word */
    int bcast = !m->write && m->mode != AM_ABS && (m->ureg < 0x10 || (m->ureg >= 0x50 && m->ureg < 0x60)) &&
                (((vm->mode1 & M1_BDCST1) && i == 1) || ((vm->mode1 & M1_BDCST9) && i == 9));
    uint32_t b2 = bcast ? b : b + wb;
    if (m->width == W_PX48) {
        /* 48-bit PX transfer (PGR 2-11): memory word -> PX bits 63:16. NW addresses of the
         * L1 blocks hold 48-bit words 6 bytes apart from the block base (T3.2 section 0). */
        static const uint32_t nwb[4] = {0x90000u, 0xb0000u, 0xc0000u, 0xe0000u}, bb[4] = {0x240000u, 0x2c0000u, 0x300000u, 0x380000u};
        uint32_t b48 = 0; int found = 0;
        for (int k = 0; k < 4; k++) if (ea >= nwb[k] && ea < nwb[k] + 0x10000u) { b48 = bb[k] + 6u * (ea - nwb[k]); found = 1; }
        if (!found) { trapf(vm, 8, "48-bit PX transfer at a non-NW address %#x", ea); return; }
        if (sim) { trapf(vm, 8, "PX transfer in SIMD mode"); return; }
        if (cx) {
            if (m->write) {
                uint64_t px = ((uint64_t)vm->px2 << 32) | vm->px1;
                if (!wr_bytes(vm, b48, 6, px >> 16)) return;
            } else {
                uint64_t v48;
                if (!rd_bytes(vm, b48, 6, &v48)) return;
                uint64_t px = (v48 & 0xffffffffffffull) << 16;
                vm->px1 = (uint32_t)px; vm->px2 = (uint32_t)(px >> 32);
            }
        }
        if (m->mode == AM_POST && cx) {
            uint32_t nv = addr + (uint32_t)(delta * (int32_t)scale);
            ireg_upd[*iupd_idx] = nv; ireg_upd[*iupd_idx + 2] = (uint32_t)i; (*iupd_idx)++;
        }
        return;
    }
    if (m->width == W_LW && sim) { trapf(vm, 8, "long-word access in SIMD mode"); return; }
    /* SIMD sub-word accesses: the implicit (PEy) element is the next one (+2 bytes for
     * short words): DN2 0x1c2a17 converts consecutive 16-bit frame words two at a time
     * with post-modify +2 short words, which covers each word once only this way. The
     * classic PGR's k = 2 short words would read overlapping pairs. [inferred] */
    if (m->write) {
        if (doit_x) {
            uint64_t v = (m->flags & MF_IMMDATA) ? from32(m->data) : ureg_rd(vm, m->ureg), v2 = 0;
            if (m->width == W_LW) {
                if (m->ureg >= 0x10) { trapf(vm, 8, "long-word store of non-data register"); return; }
                v2 = vm->pe[0].r[m->ureg ^ 1];
                if (m->ureg & 1) { uint64_t t = v; v = v2; v2 = t; }
            }
            if (!store_val(vm, b, m->width, v, v2)) return;
        }
        if (doit_y && compl_ok) {
            int cc = complement(m->ureg);
            if (cc >= 0) { if (!store_val(vm, b2, m->width, ureg_rd(vm, cc), 0)) return; }
        } else if (doit_y && same_y) {
            uint64_t v = (m->flags & MF_IMMDATA) ? from32(m->data) : ureg_rd(vm, m->ureg);
            if (!store_val(vm, b2, m->width, v, 0)) return;
        }
    } else {
        /* SIMD load into a register without a complement: only the explicit transfer
         * (DN2 0x1c2784 `I13 = DM(I6 - 38)` loads a zero for later SIMD stores). [inferred] */
        if (doit_x) {
            uint64_t v, v2 = 0;
            if (!load_val(vm, b, m->width, &v, &v2)) return;
            if (m->width == W_LW) {
                if (m->ureg >= 0x10) { trapf(vm, 8, "long-word load of non-data register"); return; }
                int lo = m->ureg & ~1, hi = lo + 1;
                lw->code[lw->n] = lo; lw->v[lw->n++] = v;
                lw->code[lw->n] = hi; lw->v[lw->n++] = v2;
            } else {
                lw->code[lw->n] = m->ureg; lw->v[lw->n++] = v;
                /* broadcast load (PGR 6-24): data-register loads indexed by I1/I9 also
                 * write the complementary register, independent of SIMD */
                if (bcast && !sim) {
                    lw->code[lw->n] = complement(m->ureg); lw->v[lw->n++] = v;
                }
            }
        }
        if (doit_y && compl_ok) {
            int cc = complement(m->ureg);
            if (cc >= 0) {
                uint64_t v, v2;
                if (!load_val(vm, b2, m->width, &v, &v2)) return;
                lw->code[lw->n] = cc; lw->v[lw->n++] = v;
            }
        }
    }
    if (m->mode == AM_POST && (cx || (sim && cy))) {
        uint32_t nv = addr + (uint32_t)(delta * (int32_t)scale);
        ireg_upd[*iupd_idx] = circ(vm, i, nv, delta, scale);
        ireg_upd[*iupd_idx + 2] = (uint32_t)i;
        (*iupd_idx)++;
    }
}

/* ------------------------------------------------------------------ */
/* stacks                                                              */

static int pc_push(vm_t *vm, uint32_t v) {
    if (vm->pcsp >= PCSTK_N) { trapf(vm, 9, "PC stack overflow"); return 0; }
    vm->pcstk[vm->pcsp++] = v; return 1;
}
static int pc_pop(vm_t *vm, uint32_t *v) {
    if (vm->pcsp <= 0) { trapf(vm, 9, "PC stack underflow"); return 0; }
    *v = vm->pcstk[--vm->pcsp]; return 1;
}

/* ------------------------------------------------------------------ */
/* execution                                                           */

/* transfer of control requested by the current instruction */
typedef struct { int taken; uint32_t target; int delayed; int call; int la; } xfer_t;

static void run_compute_sisd_simd(vm_t *vm, const insn_t *in, int cx, int cy, int sim) {
    if (in->ncomp == 0) return;
    if (cx) exec_compute(vm, 0, in);
    if (sim && cy) exec_compute(vm, 1, in);
}

static void exec_insn(vm_t *vm, const insn_t *in, xfer_t *x) {
    int sim = simd(vm);
    int c = in->cond;
    int cx = c == 31 ? 1 : cond_pe(vm, 0, c);
    int cy = (sim && c != 31) ? cond_pe(vm, 1, c) : cx;
    if (c == 31) cy = 1;
    switch (in->kind) {
    case K_NOP: return;
    case K_IDLE: return;
    case K_COMPUTE: {
        if (in->flags & IF_ELSE) { cx = !cx; cy = !cy; }
        run_compute_sisd_simd(vm, in, cx, cy, sim);
        return; }
    case K_SHIFTIMM:
        run_compute_sisd_simd(vm, in, cx, cy, sim);
        return;
    case K_DAG: {
        /* fast path: one unconditional normal-word access, SISD, byte-space address,
         * no circular buffer, aligned, mapped page, no write watch. Same semantics as
         * the generic path below (store data read before the compute, load written after). */
        if (!sim && c == 31 && !vm->no_fast && !in->mem[1].kind && in->mem[0].width == W_NW && in->mem[0].mode != AM_ABS &&
            vm->watch_hi <= vm->watch_lo) {
            const mem_t *m = &in->mem[0];
            int i = m->ireg;
            uint32_t addr = vm->I[i];
            if (((addr >= 0x240000u && addr < 0x400000u) || addr >= 0x20000000u) &&
                (vm->L[i] == 0 || !(vm->mode1 & M1_CBUFEN))) {
                int32_t delta = m->mreg == 0xff ? m->off : (int32_t)vm->M[m->mreg];
                uint32_t ea = m->mode == AM_PRE ? addr + (uint32_t)(delta * 4) : addr;
                uint8_t *pg = vm->mem[ea >> 12];
                if (pg && !(ea & 3)) {
                    uint8_t *pp = pg + (ea & 0xfff);
                    if (m->write) {
                        uint32_t v;
                        if (m->flags & MF_IMMDATA) v = m->data;
                        else if (m->ureg < 16) v = hi32(vm->pe[0].r[m->ureg]);
                        else v = hi32(ureg_rd(vm, m->ureg));
                        memcpy(pp, &v, 4);
                        if (in->ncomp) exec_compute(vm, 0, in);
                    } else {
                        uint32_t v; memcpy(&v, pp, 4);
                        if (in->ncomp) exec_compute(vm, 0, in);
                        if (m->ureg < 16) vm->pe[0].r[m->ureg] = from32(v);
                        else ureg_wr(vm, m->ureg, from32(v));
                    }
                    if (m->mode == AM_POST) vm->I[i] = addr + (uint32_t)(delta * 4);
                    return;
                }
            }
        }
        lwrites_t lw; lw.n = 0;
        uint32_t iupd[4]; int nupd = 0;
        /* stores read registers before compute writes them; loads write after */
        for (int k = 0; k < 2; k++) {
            if (!in->mem[k].kind) continue;
            do_mem(vm, &in->mem[k], cx, cy, &lw, iupd, &nupd, sim);
            if (vm->trap_code) return;
        }
        run_compute_sisd_simd(vm, in, cx, cy, sim);
        for (int k = 0; k < lw.n; k++) ureg_wr(vm, lw.code[k], lw.v[k]);
        for (int k = 0; k < nupd; k++) vm->I[iupd[k + 2]] = iupd[k];
        return; }
    case K_UREG_MOVE: {
        int dst = in->a, src = in->b;
        uint64_t v = ureg_rd(vm, src);
        int cd = complement(dst), cs = complement(src);
        uint64_t vy = 0; int dy = -1;
        if (sim) {
            if (cd >= 0 && cs >= 0) { dy = cd; vy = ureg_rd(vm, cs); }
            else if (cd >= 0) { dy = cd; vy = v; }
        }
        run_compute_sisd_simd(vm, in, cx, cy, sim);
        if (cx) ureg_wr(vm, dst, v);
        if (dy >= 0 && cy) ureg_wr(vm, dy, vy);
        return; }
    case K_SWAP: {
        /* Ra <-> Sb (PGR Table 4-40) */
        int a = in->a, b = in->b;
        uint64_t ra = vm->pe[0].r[a], sb = vm->pe[1].r[b];
        run_compute_sisd_simd(vm, in, cx, cy, sim);
        if (!sim) { if (cx) { vm->pe[0].r[a] = sb; vm->pe[1].r[b] = ra; } }
        else { if (cx) vm->pe[0].r[a] = sb; if (cy) vm->pe[1].r[b] = ra; }
        return; }
    case K_UREG_IMM: {
        ureg_wr(vm, in->a, from32((uint32_t)in->imm));
        if (sim) { int cc = complement(in->a); if (cc >= 0) ureg_wr(vm, cc, from32((uint32_t)in->imm)); }
        return; }
    case K_MODIFY: {
        int dst = in->a, src = in->b;
        uint32_t base = vm->I[src];
        if (in->flags & IF_BITREV) { trapf(vm, 10, "bit-reverse modify not implemented"); return; }
        int32_t delta; uint32_t scale;
        if (in->flags & IF_IMM) {
            delta = in->imm;
            scale = (in->flags & IF_NW) ? mscale(base, W_NW) : 1;
        } else {
            delta = (int32_t)vm->M[in->c];
            if ((in->flags & (IF_NW | IF_SW)) == (IF_NW | IF_SW) && delta != 0) {
                trapf(vm, 10, "Type 7 modify with both scaling bits and M%d = %#x (meaning unknown)", in->c, (uint32_t)delta);
                return;
            }
            scale = (in->flags & IF_NW) ? mscale(base, W_NW) : (in->flags & IF_SW) ? mscale(base, W_SW) : 1;
        }
        if (in->ncomp) run_compute_sisd_simd(vm, in, cx, cy, sim);
        if (cx || (sim && cy)) vm->I[dst] = (dst == src) ? circ(vm, src, base + (uint32_t)(delta * (int32_t)scale), delta, scale)
                                                         : base + (uint32_t)(delta * (int32_t)scale);
        return; }
    case K_JUMP: {
        int take = sim ? (cx && cy) : cx;
        if (in->flags & IF_ELSE) {
            /* compute executes when the condition is false */
            if (in->ncomp) run_compute_sisd_simd(vm, in, !cx, !cy, sim);
        } else if (in->ncomp) run_compute_sisd_simd(vm, in, cx, cy, sim);
        if (!take) return;
        uint32_t tgt;
        if (in->a == JT_IND) tgt = vm->I[in->b] + vm->M[in->c];
        else tgt = (uint32_t)in->imm;
        if (in->flags & IF_CJUMP) {
            uint64_t i6 = from32(vm->I[6]);
            vm->pe[0].r[2] = i6;
            if (sim) vm->pe[1].r[2] = i6;
            vm->I[6] = vm->I[7];
        }
        x->taken = 1; x->target = tgt & 0xffffff;
        x->delayed = (in->flags & IF_DB) != 0;
        x->call = (in->flags & IF_CALL) != 0;
        x->la = (in->flags & IF_LA) != 0;
        return; }
    case K_RETURN: {
        int take = sim ? (cx && cy) : cx;
        if (in->flags & IF_ELSE) { if (in->ncomp) run_compute_sisd_simd(vm, in, !cx, !cy, sim); }
        else if (in->ncomp) run_compute_sisd_simd(vm, in, cx, cy, sim);
        if (!take) return;
        if (in->flags & IF_RTI) { trapf(vm, 11, "RTI not supported"); return; }
        if (in->flags & IF_LR) { trapf(vm, 11, "RTS (LR) not supported"); return; }
        uint32_t t; if (!pc_pop(vm, &t)) return;
        x->taken = 1; x->target = t; x->delayed = (in->flags & IF_DB) != 0;
        return; }
    case K_DO: {
        uint32_t cnt = 0; int counted = 1;
        if (in->a == DO_IMM) cnt = (uint32_t)in->imm2;
        else if (in->a == DO_UREG) cnt = hi32(ureg_rd(vm, in->b));
        else counted = 0;
        if (counted) {
            vm->lcntr = cnt;
            if (cnt == 0) { trapf(vm, 12, "DO loop with LCNTR = 0 (2^32 iterations)"); return; }
        }
        if (vm->lsp >= LSTK_N) { trapf(vm, 12, "loop stack overflow"); return; }
        if (!pc_push(vm, in->pc + in->len)) return;
        loop_t *l = &vm->lstk[vm->lsp++];
        l->end = (uint32_t)in->imm; l->start = in->pc + in->len; l->count = cnt;
        l->counted = (uint8_t)counted; l->term = in->b;
        if (!counted) l->term = (uint8_t)in->c;
        return; }
    case K_SYSBIT: {
        int code = in->a; uint32_t mask = (uint32_t)in->imm;
        uint32_t v = hi32(ureg_rd(vm, code));
        switch (in->b) {
        case 0: ureg_wr(vm, code, from32(v | mask)); break;
        case 1: ureg_wr(vm, code, from32(v & ~mask)); break;
        case 2: ureg_wr(vm, code, from32(v ^ mask)); break;
        case 4: case 5: {
            int r = in->b == 4 ? ((v & mask) == mask) : (v == mask);
            vm->pe[0].astat = (vm->pe[0].astat & ~BTF) | (r ? BTF : 0);
            int cc = complement(code);
            if (sim && cc >= 0) {
                uint32_t vy = hi32(ureg_rd(vm, cc));
                int ry = in->b == 4 ? ((vy & mask) == mask) : (vy == mask);
                vm->pe[1].astat = (vm->pe[1].astat & ~BTF) | (ry ? BTF : 0);
            }
            break; }
        default: trapf(vm, 13, "Type 18 bop %d not supported", in->b); return;
        }
        if (sim && in->b <= 2) {
            int cc = complement(code);
            if (cc >= 0) {
                uint32_t vy = hi32(ureg_rd(vm, cc));
                uint32_t ny = in->b == 0 ? (vy | mask) : in->b == 1 ? (vy & ~mask) : (vy ^ mask);
                ureg_wr(vm, cc, from32(ny));
            }
        }
        return; }
    case K_STACK: {
        uint32_t f = (uint32_t)in->imm;   /* bit layout from translate.py */
        if (f & 0x01) { /* lpu */ trapf(vm, 14, "PUSH LOOP not supported"); return; }
        if (f & 0x02) { /* lpo */
            if (vm->lsp <= 0) { trapf(vm, 14, "POP LOOP with empty loop stack"); return; }
            vm->lsp--;
        }
        if (f & 0x04) { /* spu */
            if (vm->ssp >= SSTK_N) { trapf(vm, 14, "status stack overflow"); return; }
            vm->sstk[vm->ssp][0] = vm->pe[0].astat; vm->sstk[vm->ssp][1] = vm->pe[1].astat; vm->sstk[vm->ssp][2] = vm->mode1;
            vm->ssp++;
        }
        if (f & 0x08) { /* spo */
            if (vm->ssp <= 0) { trapf(vm, 14, "status stack underflow"); return; }
            vm->ssp--;
            vm->pe[0].astat = vm->sstk[vm->ssp][0]; vm->pe[1].astat = vm->sstk[vm->ssp][1];
            mode1_write(vm, vm->sstk[vm->ssp][2]);
        }
        if (f & 0x10) { /* ppu */ if (!pc_push(vm, vm->pc + in->len)) return; }
        if (f & 0x20) { /* ppo */ uint32_t t; if (!pc_pop(vm, &t)) return; }
        if (f & 0x40) { /* fc: flush cache */ }
        if (f & ~0x7fu) { trapf(vm, 14, "Type 20 option bits %#x not supported", f); return; }
        update_stack_flags(vm);
        return; }
    case K_RFRAME: {
        uint32_t i6 = vm->I[6]; uint64_t v;
        if (!rd_bytes(vm, to_byte(i6), 4, &v)) return;
        vm->I[7] = i6; vm->I[6] = (uint32_t)v;
        return; }
    case K_ACONV: {
        /* B2W: byte -> normal-word address (>> 2); W2B: the reverse. Only for L1 addresses
         * that have both views; anything else would raise ILAD on the unit: trap. */
        uint32_t *R = in->c ? vm->B : vm->I;
        uint32_t v = R[in->b];
        if (in->imm) {
            if (!(v >= 0x90000u && v < 0xe8000u)) { trapf(vm, 15, "ACONV W2B of %#x (no byte equivalent)", v); return; }
            v <<= 2;
        } else {
            if (!(v >= 0x240000u && v < 0x3a0000u)) { trapf(vm, 15, "ACONV B2W of %#x (no word equivalent)", v); return; }
            v >>= 2;
        }
        R[in->a] = v;
        return; }
    case K_UNIMPL:
        trapf(vm, 16, "unimplemented instruction (msg %u)", in->msg); vm->trap_msg_idx = in->msg; return;
    }
    trapf(vm, 17, "unknown insn kind %d", in->kind);
}

int vm_run(vm_t *vm, uint64_t max_insns) {
    for (uint64_t n = 0; n < max_insns; n++) {
        uint32_t pc = vm->pc;
        if (UNLIKELY(pc == vm->stop_pc && vm->db_count == 0)) return ST_STOP;
        insn_t *page = vm->code[pc >> 12];
        if (UNLIKELY(!page)) return ST_DECODE;
        insn_t *in = &page[pc & 0xfff];
        if (UNLIKELY(in->kind == K_UNDECODED)) return ST_DECODE;
        xfer_t x = {0, 0, 0, 0, 0};
        exec_insn(vm, in, &x);
        if (UNLIKELY(vm->trap_code)) { vm->trap_pc = pc; return ST_TRAP; }
        vm->icount++;
        if (UNLIKELY(vm->profile)) { in->hits++; vm->stat_kind[in->kind & 31]++; }
        uint32_t next = pc + in->len;
        int in_slot = vm->db_count > 0;
        if (x.taken) {
            if (UNLIKELY(in_slot)) return trapf(vm, 21, "branch taken inside the delay slots of the branch at %#x", vm->db_from);
            if (x.la) {   /* loop abort: pop the loop and its PC stack entry */
                if (vm->lsp <= 0) return trapf(vm, 22, "JUMP (LA) with empty loop stack");
                vm->lsp--; uint32_t t; if (!pc_pop(vm, &t)) return ST_TRAP;
            }
            if (x.delayed) {
                vm->db_count = 2; vm->db_target = x.target; vm->db_from = pc;
                vm->db_from = pc | (x.call ? 0x80000000u : 0);
                vm->pc = next;
            } else {
                if (x.call && !pc_push(vm, next)) return ST_TRAP;
                vm->pc = x.target;
            }
            if (in->kind == K_IDLE) return ST_IDLE;
            continue;
        }
        /* loop end */
        if (vm->lsp && pc == vm->lstk[vm->lsp - 1].end) {
            if (UNLIKELY(in_slot)) return trapf(vm, 23, "loop end at %#x inside delay slots", pc);
            loop_t *l = &vm->lstk[vm->lsp - 1];
            int done;
            if (l->counted) { done = l->count == 1; if (!done) l->count--; }
            else {
                int cx2 = cond_pe(vm, 0, l->term);
                done = simd(vm) ? (cx2 && cond_pe(vm, 1, l->term)) : cx2;
            }
            if (done) { vm->lsp--; uint32_t t; if (!pc_pop(vm, &t)) return ST_TRAP; }
            else next = l->start;
        }
        if (in_slot) {
            vm->db_count--;
            if (vm->db_count == 0) {
                if (vm->db_from & 0x80000000u) { if (!pc_push(vm, next)) return ST_TRAP; }
                next = vm->db_target;
            }
        }
        vm->pc = next;
        if (UNLIKELY(in->kind == K_IDLE)) return ST_IDLE;
    }
    return ST_OK;
}

/* ------------------------------------------------------------------ */
/* API for ctypes                                                      */

vm_t *vm_new(void) {
    vm_t *vm = calloc(1, sizeof(vm_t));
    vm->stop_pc = 0xffffffffu;
    vm->mode1 = 0;
    update_stack_flags(vm);
    return vm;
}
void vm_free(vm_t *vm) { free(vm); }
size_t vm_sizeof(void) { return sizeof(vm_t); }
size_t vm_sizeof_insn(void) { return sizeof(insn_t); }
size_t vm_offsetof_pe(void) { return offsetof(vm_t, pe); }

void vm_map(vm_t *vm, uint32_t base, uint32_t size, uint8_t *host) {
    for (uint32_t o = 0; o < size; o += 0x1000) vm->mem[(base + o) >> 12] = host + o;
}
void vm_set_code_page(vm_t *vm, uint32_t page, insn_t *p) { vm->code[page] = p; }
const char *vm_trap_msg(vm_t *vm) { return vm->trap_msg; }

uint64_t vm_get_ureg(vm_t *vm, int code) { return ureg_rd(vm, code); }
void vm_set_ureg(vm_t *vm, int code, uint64_t v) { ureg_wr(vm, code, v); }
void vm_set_mode1(vm_t *vm, uint32_t v) { mode1_write(vm, v); }
int vm_pc_push(vm_t *vm, uint32_t v) { return pc_push(vm, v); }
void vm_clear_trap(vm_t *vm) { vm->trap_code = 0; vm->trap_msg[0] = 0; }

/* field offsets for the Python mirror */
size_t vm_layout(int which) {
    switch (which) {
    case 0: return offsetof(vm_t, pe);
    case 1: return offsetof(vm_t, I);
    case 2: return offsetof(vm_t, pc);
    case 3: return offsetof(vm_t, icount);
    case 4: return offsetof(vm_t, mode1);
    case 5: return offsetof(vm_t, lsp);
    case 6: return offsetof(vm_t, pcsp);
    case 7: return offsetof(vm_t, stop_pc);
    case 8: return offsetof(vm_t, trap_code);
    case 9: return offsetof(vm_t, db_count);
    case 10: return sizeof(pe_t);
    case 11: return offsetof(insn_t, cs);
    case 12: return offsetof(insn_t, mem);
    case 13: return offsetof(insn_t, pc);
    case 14: return offsetof(vm_t, stat_kind);
    case 15: return offsetof(vm_t, mmr_strict);
    case 16: return offsetof(vm_t, trap_pc);
    case 17: return offsetof(vm_t, stat_mmr_reads);
    }
    return (size_t)-1;
}

uint32_t vm_get_pc(vm_t *vm) { return vm->pc; }
void vm_set_pc(vm_t *vm, uint32_t pc) { vm->pc = pc; }
uint64_t vm_get_icount(vm_t *vm) { return vm->icount; }
void vm_set_stop(vm_t *vm, uint32_t pc) { vm->stop_pc = pc; }
int vm_get_trap(vm_t *vm, uint32_t *pc, uint32_t *msgidx) { *pc = vm->trap_pc; *msgidx = vm->trap_msg_idx; return vm->trap_code; }
uint64_t vm_get_reg40(vm_t *vm, int pe, int i) { return vm->pe[pe].r[i]; }
void vm_set_reg40(vm_t *vm, int pe, int i, uint64_t v) { vm->pe[pe].r[i] = v & 0xffffffffffull; }
uint32_t vm_get_dag(vm_t *vm, int kind, int i) {
    switch (kind) { case 0: return vm->I[i]; case 1: return vm->M[i]; case 2: return vm->L[i]; case 3: return vm->B[i]; }
    return 0;
}
void vm_set_dag(vm_t *vm, int kind, int i, uint32_t v) {
    switch (kind) { case 0: vm->I[i] = v; break; case 1: vm->M[i] = v; break; case 2: vm->L[i] = v; break; case 3: vm->B[i] = v; break; }
}
void vm_get_mr(vm_t *vm, int pe, int which, uint64_t *lo, uint64_t *hi) {
    unsigned __int128 u = (unsigned __int128)vm->pe[pe].mr[which]; *lo = (uint64_t)u; *hi = (uint64_t)(u >> 64);
}
void vm_stats(vm_t *vm, uint64_t *out) {
    for (int i = 0; i < 32; i++) out[i] = vm->stat_kind[i];
    out[32] = vm->stat_mmr_reads; out[33] = vm->stat_mmr_writes; out[34] = vm->last_mmr_addr; out[35] = vm->stat_circ_wraps;
}
int vm_depths(vm_t *vm, int *out) { out[0] = vm->pcsp; out[1] = vm->lsp; out[2] = vm->ssp; out[3] = vm->db_count; return 0; }
void vm_set_mmr_strict(vm_t *vm, int s) { vm->mmr_strict = (uint8_t)s; }
void vm_reset_stacks(vm_t *vm) { vm->pcsp = vm->lsp = vm->ssp = 0; vm->db_count = 0; update_stack_flags(vm); }

void vm_set_watch(vm_t *vm, uint32_t lo, uint32_t hi) { vm->watch_lo = lo; vm->watch_hi = hi; vm->n_watch = 0; }
int vm_get_watch(vm_t *vm, uint32_t *pc, uint32_t *addr, uint32_t *val) {
    for (int i = 0; i < vm->n_watch; i++) { pc[i] = vm->watch_pc[i]; addr[i] = vm->watch_addr[i]; val[i] = vm->watch_val[i]; }
    return vm->n_watch;
}
void vm_set_align_strict(vm_t *vm, int s) { vm->align_strict = (uint8_t)s; }
uint64_t vm_misaligned(vm_t *vm) { return vm->stat_misaligned; }
int vm_get_mis(vm_t *vm, uint32_t *out) { for (int i = 0; i < vm->n_mis; i++) out[i] = vm->mis_pc[i]; return vm->n_mis; }
void vm_set_imm_align(vm_t *vm, int s) { vm->imm_align = (uint8_t)s; }
void vm_set_no_fast(vm_t *vm, int v) { vm->no_fast = (uint8_t)v; }
void vm_set_profile(vm_t *vm, int v) { vm->profile = (uint8_t)v; }
uint32_t vm_mmr_get(vm_t *vm, uint32_t a) {
    if (a >= 0x30000u && a < 0x40000u) return vm->core_mmr[a - 0x30000u];
    int i = mmr_index(vm, a, 0); return i >= 0 ? vm->mmr_val[i] : 0;
}
void vm_mmr_set(vm_t *vm, uint32_t a, uint32_t v) {
    if (a >= 0x30000u && a < 0x40000u) { vm->core_mmr[a - 0x30000u] = v; return; }
    int i = mmr_index(vm, a, 1); if (i >= 0) vm->mmr_val[i] = v;
}
int vm_mmr_list(vm_t *vm, uint32_t *addr, uint32_t *val, int max) {
    int n = 0;
    for (int j = 0; j < 65536 && n < max; j++) if (vm->mmr_used[j]) { addr[n] = vm->mmr_addr[j]; val[n] = vm->mmr_val[j]; n++; }
    return n;
}
