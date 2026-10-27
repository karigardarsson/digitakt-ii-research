/* PLAITS for the Digitakt II: small helpers shared by the engines and the voice wrapper (plain C99, float only).
 *
 * The same source builds on the host (tests/plaits) and with Selache's C compiler for the SHARC+ (`__SELACHE__`).
 * Selache's C has no optimiser and no inlining: these are static functions or macros.
 */
#ifndef PLAITS_DSP_UTIL_H
#define PLAITS_DSP_UTIL_H

/* stmlib's CONSTRAIN and MAKE_INTEGRAL_FRACTIONAL (stmlib/stmlib.h). */
#define CONSTRAIN(v, lo, hi) if (v < (lo)) { v = (lo); } else if (v > (hi)) { v = (hi); }
#define MAKE_INTEGRAL_FRACTIONAL(v) int v##_integral = (int)v; float v##_fractional = v - itof_nonneg(v##_integral);

static float maxf(float a, float b) { return a > b ? a : b; }
static float minf(float a, float b) { return a < b ? a : b; }
static float absf(float a) { return a < 0.0f ? -a : a; }

/* (float)i for 0 <= i < 2^23, without a FLOAT instruction: 2^23 + i is exact in the mantissa, and subtracting 2^23
 * leaves i exactly. Selache compresses a register-to-register `Fn = FLOAT Rm` (n != m) into a 32-bit form the stock
 * DSP code never uses, which the encoding gate (tools/patchkit/pk_encoding_gate.py) refuses. The float is read
 * through a pointer to the union converted to `float *` (C99 6.7.2.1), never as `u.f`: selcc places union members
 * like struct members (docs/findings/20-patch-toolchains.md), and pk_sharc.compile_c refuses a later member's name
 * (tools/patchkit/pk_cunion.py). */
static float itof_nonneg(int i)
{
    union { int i; float f; } u;
    u.i = 0x4B000000 | i;
    return *(float *)(void *)&u - 8388608.0f;
}

#endif
