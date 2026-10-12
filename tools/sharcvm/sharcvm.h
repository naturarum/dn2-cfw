/* SPDX-License-Identifier: GPL-2.0-or-later */
/* sharcvm.h -- SHARC+ (ADSP-2156x) instruction-set interpreter core.
 *
 * The Python side (tools/sharcvm/vm.py) decodes instructions with digikit's
 * table-driven decoder, translates them into insn_t records (translate.py) and
 * hands them to this core page by page. The core executes them. Anything the
 * translator cannot express becomes K_UNIMPL, which traps when executed.
 *
 * Layout of every struct here is mirrored with ctypes in vm.py: keep in sync.
 */
#ifndef SHARCVM_H
#define SHARCVM_H
#include <stdint.h>

/* ---------- compute slots ---------- */
typedef struct {
    uint16_t op;      /* OP_* (0 = empty) */
    uint8_t d, x, y;  /* register numbers 0..15 */
    uint8_t mod;      /* op-specific flags, see MOD_* */
    int16_t imm;      /* immediate (shift data8, bit6:len6 packed, mul target...) */
} cslot_t;            /* 8 bytes */

/* ---------- memory access descriptor ---------- */
typedef struct {
    uint8_t kind;     /* 0 none, 1 DAG/absolute access */
    uint8_t write;    /* 1 = store to memory */
    uint8_t ireg;     /* I register 0..15 */
    uint8_t mreg;     /* M register 0..15, 0xFF = immediate offset in off */
    uint8_t mode;     /* AM_* */
    uint8_t width;    /* W_* */
    uint8_t ureg;     /* 7-bit universal register code */
    uint8_t flags;    /* MF_* */
    int32_t off;      /* immediate offset (in access units) or absolute address */
    uint32_t data;    /* immediate data (Type 16) */
} mem_t;              /* 16 bytes */

typedef struct {
    uint16_t kind;    /* K_* */
    uint8_t len;      /* instruction length in 16-bit parcels (1..3) */
    uint8_t cond;     /* 0..31, 31 = always */
    uint8_t flags;    /* IF_* */
    uint8_t ncomp;    /* number of compute slots used */
    uint8_t ckind;    /* CK_* */
    uint8_t a, b, c;  /* generic register fields */
    uint8_t pad[2];
    int32_t imm;      /* generic immediate: branch target, data, mask ... */
    uint32_t imm2;
    cslot_t cs[3];
    mem_t mem[2];
    uint32_t pc;      /* own SW address (for messages) */
    uint32_t msg;     /* index of a message string on the Python side */
    uint32_t hits;    /* executions (profile mode only) */
} insn_t;

/* insn kinds */
enum {
    K_UNDECODED = 0,  /* lazily decoded: core returns ST_DECODE */
    K_UNIMPL,         /* trap with msg */
    K_NOP,
    K_IDLE,           /* stop (ST_IDLE) */
    K_COMPUTE,        /* cond compute (types 2a/2b/2c/2a_short), optional ELSE */
    K_DAG,            /* compute + up to two memory transfers (1a,1b,3x,4x,6a,14x,15x,16x) */
    K_UREG_MOVE,      /* a = dst ureg, b = src ureg, + compute (5a/5b move) */
    K_SWAP,           /* a = dreg, b = cdreg: Ra <-> Sb, + compute (5a/5b swap) */
    K_UREG_IMM,       /* a = ureg, imm = data (17a/17b) */
    K_MODIFY,         /* I[a] = I[b] + M[c] * scale or + imm; flags IF_IMM, IF_NW, IF_BITREV (7a/7b/19a) */
    K_JUMP,           /* direct/indirect/relative jump or call (8a, 9a, 9b, 25a) */
    K_RETURN,         /* RTS/RTI (11a/11c) */
    K_DO,             /* DO loop: imm = end address, a = counter source */
    K_SYSBIT,         /* 18a: a = sreg ureg code, b = bop, imm = mask */
    K_STACK,          /* 20a: imm = field bits */
    K_RFRAME,         /* I7 = I6, I6 = DM(0, I6) */
    K_ACONV,          /* 7d address conversion */
    K_SHIFTIMM,       /* 6b / 6a nomem: cond + shift-immediate compute */
};

/* insn flags */
#define IF_DB     0x01  /* delayed branch */
#define IF_CALL   0x02
#define IF_LA     0x04  /* loop abort */
#define IF_CI     0x08  /* clear interrupt */
#define IF_ELSE   0x10  /* compute executes when cond is false (9a/10a/11a 'e') */
#define IF_CJUMP  0x20  /* R2 = I6, I6 = I7 */
#define IF_LR     0x40  /* loop reentry (11a) */
#define IF_RTI    0x80

/* K_JUMP target modes in .a */
enum { JT_ABS = 0, JT_IND = 1 /* I[b] + M[c] */ };
/* K_DO counter source in .a */
enum { DO_IMM = 0, DO_UREG = 1, DO_COND = 2 };
/* K_MODIFY flags reuse .flags: */
#define IF_IMM    0x01
#define IF_NW     0x02
#define IF_BITREV 0x04
#define IF_SW     0x08

/* compute combination kinds */
enum { CK_NONE = 0, CK_SINGLE, CK_MULALU, CK_DUAL, CK_MULDUAL };

/* address modes */
enum { AM_PRE = 0, AM_POST = 1, AM_ABS = 2 };
/* access widths */
enum { W_NW = 0, W_LW, W_SW, W_SWSE, W_BW, W_BWSE, W_PX48 };
/* mem flags */
#define MF_IMMDATA 0x01   /* store imm data (Type 16) instead of a register */
#define MF_DREG    0x02   /* ureg is a data register (dreg), SIMD implicit move applies */
#define MF_PM      0x04   /* PM bus (DAG2): only informational */

/* ---------- compute op codes ---------- */
enum {
    OP_NONE = 0,
    /* ALU fixed */
    OP_ADD, OP_SUB, OP_ADDCI, OP_SUBCI, OP_AVG, OP_COMP, OP_COMPU, OP_PASS, OP_NEG,
    OP_INCCI, OP_DECCI, OP_INC, OP_DEC, OP_ABS, OP_AND, OP_OR, OP_XOR, OP_NOT,
    OP_MIN, OP_MAX, OP_CLIP,
    /* ALU float */
    OP_FADD, OP_FSUB, OP_FAVG, OP_FCOMP, OP_FABSADD, OP_FABSSUB, OP_FPASS, OP_FNEG,
    OP_FRND, OP_FMANT, OP_FABS, OP_FSCALB, OP_FLOGB, OP_FRECIPS, OP_FRSQRTS,
    OP_FIX, OP_FLOAT, OP_TRUNC, OP_FIXBY, OP_FLOATBY, OP_TRUNCBY, OP_FCOPYSIGN,
    OP_FMIN, OP_FMAX, OP_FCLIP,
    /* multiplier */
    OP_MUL,      /* d = Rn; mod = MOD_*; imm = target | acc<<2 | sub<<4 */
    OP_MRSAT,    /* imm = src mr (1 MRF 2 MRB) | target<<2 */
    OP_MRRND,
    OP_MRCLR,    /* imm = mr */
    OP_FMUL,
    OP_MR2R,     /* Rn = MRxF/B part; imm = part (0..2) | mr<<2 */
    OP_R2MR,     /* MRxF/B part = Rn */
    /* shifter (MOD_IMM: amount from imm) */
    OP_LSHIFT, OP_ASHIFT, OP_ROT, OP_LSHIFT_OR, OP_ASHIFT_OR,
    OP_FEXT, OP_FDEP, OP_FEXT_SE, OP_FDEP_SE, OP_FDEP_OR, OP_FDEP_OR_SE,
    OP_BSET, OP_BCLR, OP_BTGL, OP_BTST, OP_EXP, OP_EXPEX, OP_LEFTZ, OP_LEFTO,
    OP_FPACK, OP_FUNPACK,
    OP_COUNT
};
/* multiplier mod bits */
#define MOD_RND   0x01
#define MOD_FRAC  0x02
#define MOD_XS    0x04   /* x signed */
#define MOD_YS    0x08   /* y signed */
#define MOD_IMM   0x10   /* shifter: immediate operand */
/* multiplier imm encoding */
#define MT_RN  0
#define MT_MRF 1
#define MT_MRB 2
#define MA_NONE 0
#define MA_ADD 1
#define MA_SUB 2

/* ---------- status ---------- */
enum {
    ST_OK = 0,        /* reached max_insns */
    ST_STOP,          /* reached stop_pc */
    ST_IDLE,          /* executed IDLE */
    ST_DECODE,        /* need decode at vm->pc */
    ST_TRAP,          /* error: vm->trap_msg / trap_code */
};

#define AZ 0x1
#define AV 0x2
#define AN 0x4
#define AC 0x8
#define AS 0x10
#define AI 0x20
#define MN 0x40
#define MV 0x80
#define MU 0x100
#define MI 0x200
#define AF 0x400
#define SV 0x800
#define SZ 0x1000
#define SS 0x2000
#define BTF 0x40000

/* STKY bits */
#define AUS 0x1
#define AVS 0x2
#define AOS 0x4
#define AIS 0x20
#define MOS 0x40
#define MVS 0x80
#define MUS 0x100
#define MIS 0x200
#define PCEM (1u << 22)
#define SSEM (1u << 24)
#define LSEM (1u << 26)

/* MODE1 bits */
#define M1_BR8 (1u << 0)
#define M1_BR0 (1u << 1)
#define M1_SRCU (1u << 2)
#define M1_SRD1H (1u << 3)
#define M1_SRD1L (1u << 4)
#define M1_SRD2H (1u << 5)
#define M1_SRD2L (1u << 6)
#define M1_SRRFH (1u << 7)
#define M1_SRRFL (1u << 10)
#define M1_NESTM (1u << 11)
#define M1_IRPTEN (1u << 12)
#define M1_ALUSAT (1u << 13)
#define M1_SSE (1u << 14)
#define M1_TRUNC (1u << 15)
#define M1_RND32 (1u << 16)
#define M1_PEYEN (1u << 21)
#define M1_BDCST9 (1u << 22)
#define M1_BDCST1 (1u << 23)
#define M1_CBUFEN (1u << 24)

typedef struct {
    uint64_t r[16];        /* 40-bit register file (bits 39..0) */
    uint32_t astat, stky;
    __int128 mr[3];        /* [1] MRF, [2] MRB (80-bit sign-extended); [0] unused */
} pe_t;

#define PCSTK_N 30
#define LSTK_N 6
#define SSTK_N 15
#define CODE_PAGES 4096    /* PC >> 12, 24-bit SW space */
#define MEM_PAGES (1u << 20)  /* 4 KiB pages over 32-bit byte space */

typedef struct {
    uint32_t end, start, count; uint8_t term, counted, pad[2];
} loop_t;

typedef struct vm {
    pe_t pe[2];
    uint32_t I[16], M[16], L[16], B[16];
    uint32_t Ialt[16], Malt[16], Lalt[16], Balt[16];  /* secondary DAG registers */
    uint64_t Ralt[2][16];                              /* secondary data registers */
    uint32_t mode1, mode2, mmask, flagsreg, irptl, imask, imaskp, mode1stk;
    uint32_t ustat[4];     /* USTAT1..4 */
    uint32_t px1, px2, tperiod, tcount, faddr, daddr;
    uint32_t lcntr;
    uint32_t pcstk[PCSTK_N]; int pcsp;
    loop_t lstk[LSTK_N]; int lsp;
    uint32_t sstk[SSTK_N][3]; int ssp;   /* ASTATx, ASTATy, MODE1 */
    uint32_t pc;
    /* delayed branch */
    int db_count; uint32_t db_target; uint32_t db_from;
    /* run control */
    uint32_t stop_pc;
    uint64_t icount;        /* instructions executed (EMUCLK source) */
    uint64_t emuclk_base;
    /* memory */
    uint8_t *mem[MEM_PAGES];
    uint8_t mmr_strict;     /* trap on unmapped access */
    uint8_t align_strict;   /* misaligned access mode, see do_mem */
    uint8_t imm_align;      /* drop low bits of I in immediate-offset forms (experiment) */
    uint8_t no_fast;        /* disable the compute fast path (for equivalence checks) */
    uint8_t profile;        /* count executions per instruction */
    uint64_t stat_misaligned;
    uint32_t mis_pc[64]; int n_mis;
    insn_t *code[CODE_PAGES];
    /* trap info */
    int trap_code; uint32_t trap_pc; uint32_t trap_msg_idx; char trap_msg[256];
    /* statistics */
    uint64_t stat_kind[32];
    uint64_t stat_mmr_reads, stat_mmr_writes;
    uint32_t last_mmr_addr;
    uint64_t stat_circ_wraps;
    uint32_t prev_pc;
    /* write watch (debugging): byte range [watch_lo, watch_hi) */
    uint32_t watch_lo, watch_hi; int n_watch;
    uint32_t watch_pc[65536], watch_addr[65536], watch_val[65536];
    /* MMR stores: core MMRs (word-addressed 0x30000-0x3ffff) directly, system MMRs hashed */
    uint32_t core_mmr[0x10000];
    uint32_t mmr_addr[65536]; uint32_t mmr_val[65536]; uint8_t mmr_used[65536]; int n_mmr;
} vm_t;

#endif
