// machines/plaits: the voice's output stage for one sub-block (plaits.c render_engine), hand-written SISD kernel.
//
// plaits_stage_kernel(float *t, float *dst, const float *k), t = &v->out[0] of a PlaitsVoice: t[0..15] OUT,
// t[16..31] AUX, t[32] dc.x1, t[33] dc.y1, t[34] out_peak, t[35] aux_peak (plaits.h); k[0] the model's OUT gain,
// k[1] its AUX gain, k[2] mix, k[3] plaits_gain. For 16 samples, bit-identical to selcc's code of the C (-DPL_POST_C):
//   plaits_post(&out_peak, k[0], OUT, 16); plaits_post(&aux_peak, k[1], AUX, 16)   (in place, as the C)
//   dst[i] = OUT[i] + mix (AUX[i] - OUT[i]), then the DC blocker and the output gain on dst (the C runs those two
//   over the whole 32-sample block after both sub-blocks: the blocker is a per-sample recurrence and the gain is per
//   sample, so running them per sub-block, in order, gives the same words).
// Every operation, its operand order and every comparison (operands and condition or its complement) are selcc's:
// tests/plaits/test_post_kernel.py. Constants as selcc's literals. The limiter's division (selcc: RECIPS and its
// Newton steps) runs on every sample and its result is dropped when peak <= 1 (selcc branches around it): the same
// words, no branch in the loop. For peak <= 1 (also peak = 0: RECIPS 0 = Inf, then 0 * Inf = NaN) the discarded
// result can set STKYX's invalid/overflow bits where the C does not: harmless while IMASK.FLTII is clear (the stock
// IMASK 0x80408018, docs/findings/07-emulator.md).
//
// State on return: R0-R8, R12, I4, LCNTR and the flags change; every other register is as on entry (R9-R11, R13-R15
// saved above I7; I0, I1, M6, L0, L1, L4 pushed through R0, M6 = 1 and L0 = L1 = L4 = 0 set here). No SIMD.

// ABI: like the C around it, the kernel relies on the C ABI's M5 = 0 and M7 = -1 (and the shim's L6 = L7 = 0); it
// sets M6 = 1 itself because it post-modifies with M6 and the C does not guarantee it (the hook's M6 = 1 is not
// relied on). DAG registers are restored by loads through I7 (same DAG): the PRM's Type 3a/15a text forbids that
// direction too, but stock does it thousands of times and the RINGS and drum kernels do it on the unit (H-opt2/3).

.SECTION/PM seg_pmco;
.GLOBAL plaits_stage_kernel.;
.COMPRESS;  // the whole file (Task O5): the assembler keeps DO loops 48-bit (PRM: a loop's last 11)

plaits_stage_kernel.:
    R0 = I0;
    DM(I7, M7) = R0;
    R0 = I1;
    DM(I7, M7) = R0;
    R0 = M6;
    DM(I7, M7) = R0;
    R0 = L0;
    DM(I7, M7) = R0;
    R0 = L1;
    DM(I7, M7) = R0;
    R0 = L4;
    DM(I7, M7) = R0;
    M6 = 1;
    L0 = 0;
    L1 = 0;
    L4 = 0;
    I7=MODIFY (I7,-6)(NW);
    DM(1, I7) = R9;
    DM(2, I7) = R10;
    DM(3, I7) = R11;
    DM(4, I7) = R13;
    DM(5, I7) = R14;
    DM(6, I7) = R15;
    I0 = R4;                                            // t
    I1 = R8;                                            // dst
    I4 = R12;                                           // k
    R8 = 0;                                             // 0.0f
    R9 = 0x3F800000;                                    // 1.0f
    R10 = 0x40000000;                                   // 2.0f
    R11 = 0xBF800000;                                   // -1.0f
    R13 = 0x3D4CCCCD;                                   // 0.05f
    R14 = 0x37A7C5AC;                                   // 0.00002f
    R15 = 0x3F4CCCCD;                                   // 0.8f

    // ---- OUT: plaits_post(&t[34], k[0], t, 16)
    R4 = DM(0, I4);                                     // gain
    R7 = DM(34, I0);                                    // *peak
    COMP(F4, F8);                                       // gain < 0.0f ?
    IF GE JUMP _ps_oplain;
    F4 = -F4;                                           // pre = -gain
    LCNTR = 16, DO _ps_olim_end UNTIL LCE;
    R0 = DM(I0, M5);
    F1 = F0 - F0;                                       // !(y - y == 0.0f): 0.0f
    COMP(F1, F8);
    IF NE R0 = R8 OR R8;
    F0 = F0 * F4;                                       // s = x * pre
    F1 = -F0;                                           // absf(s)
    COMP(F0, F8);
    IF GE R1 = R0 OR R0;
    F1 = F1 - F7;                                       // error = absf(s) - pk
    R2 = PASS R13;
    COMP(F1, F8);                                       // error > 0.0f ? 0.05f : 0.00002f
    IF LE R2 = R14 OR R14;
    F2 = F2 * F1;
    F7 = F7 + F2;                                       // pk += .. * error
    F5 = RECIPS F7, R7 = R7;                            // selcc's 1.0f / pk
    F6 = F7 * F5;
    F6 = F10 - F6;
    F5 = F5 * F6;
    F6 = F7 * F5;
    F6 = F10 - F6;
    F5 = F5 * F6;
    F12 = F9 * F5;
    F6 = F7 * F12;
    F6 = F9 - F6;
    F6 = F6 * F5;
    F12 = F12 + F6;
    COMP(F7, F9);                                       // pk <= 1.0f: g = 1.0f
    IF LE R12 = R9 OR R9;
    F0 = F0 * F12;                                      // s * g
    F0 = F0 * F15;                                      // * 0.8f
    COMP(F0, F11);                                      // CONSTRAIN(y, -1.0f, 1.0f)
    IF LT R0 = R11 OR R11;
    COMP(F0, F9);
    IF GT R0 = R9 OR R9;
_ps_olim_end:
    DM(I0, M6) = R0;
    DM(18, I0) = R7;                                    // *peak = pk (t[34])
    JUMP _ps_aux;
_ps_oplain:
    LCNTR = 16, DO _ps_oplain_end UNTIL LCE;
    R0 = DM(I0, M5);
    F1 = F0 - F0;
    COMP(F1, F8);
    IF NE R0 = R8 OR R8;
    F0 = F0 * F4;                                       // x * gain
    COMP(F0, F11);
    IF LT R0 = R11 OR R11;
    COMP(F0, F9);
    IF GT R0 = R9 OR R9;
_ps_oplain_end:
    DM(I0, M6) = R0;

    // ---- AUX: plaits_post(&t[35], k[1], t + 16, 16); I0 = t + 16
_ps_aux:
    R4 = DM(1, I4);
    R7 = DM(19, I0);
    COMP(F4, F8);
    IF GE JUMP _ps_aplain;
    F4 = -F4;
    LCNTR = 16, DO _ps_alim_end UNTIL LCE;
    R0 = DM(I0, M5);
    F1 = F0 - F0;
    COMP(F1, F8);
    IF NE R0 = R8 OR R8;
    F0 = F0 * F4;
    F1 = -F0;
    COMP(F0, F8);
    IF GE R1 = R0 OR R0;
    F1 = F1 - F7;
    R2 = PASS R13;
    COMP(F1, F8);
    IF LE R2 = R14 OR R14;
    F2 = F2 * F1;
    F7 = F7 + F2;
    F5 = RECIPS F7, R7 = R7;
    F6 = F7 * F5;
    F6 = F10 - F6;
    F5 = F5 * F6;
    F6 = F7 * F5;
    F6 = F10 - F6;
    F5 = F5 * F6;
    F12 = F9 * F5;
    F6 = F7 * F12;
    F6 = F9 - F6;
    F6 = F6 * F5;
    F12 = F12 + F6;
    COMP(F7, F9);
    IF LE R12 = R9 OR R9;
    F0 = F0 * F12;
    F0 = F0 * F15;
    COMP(F0, F11);
    IF LT R0 = R11 OR R11;
    COMP(F0, F9);
    IF GT R0 = R9 OR R9;
_ps_alim_end:
    DM(I0, M6) = R0;
    DM(3, I0) = R7;                                     // t[35]
    JUMP _ps_mix;
_ps_aplain:
    LCNTR = 16, DO _ps_aplain_end UNTIL LCE;
    R0 = DM(I0, M5);
    F1 = F0 - F0;
    COMP(F1, F8);
    IF NE R0 = R8 OR R8;
    F0 = F0 * F4;
    COMP(F0, F11);
    IF LT R0 = R11 OR R11;
    COMP(F0, F9);
    IF GT R0 = R9 OR R9;
_ps_aplain_end:
    DM(I0, M6) = R0;

    // ---- the mix, the DC blocker and the output gain into dst; I0 = t + 32
_ps_mix:
    R5 = DM(0, I0);                                     // dc.x1
    R6 = DM(1, I0);                                     // dc.y1
    I0=MODIFY (I0,-32)(NW);
    R4 = DM(2, I4);                                     // mix
    R14 = DM(3, I4);                                    // plaits_gain
    R13 = 0x3F7FD51B;                                   // PL_DC_R
    LCNTR = 16, DO _ps_mix_end UNTIL LCE;
    R1 = DM(16, I0);                                    // aux
    R0 = DM(I0, M6);                                    // out
    F2 = F1 - F0;
    F2 = F4 * F2;
    F2 = F0 + F2;                                       // o + mix (a - o)
    F3 = F2 - F5;                                       // in - x1
    F12 = F13 * F6;                                     // PL_DC_R * y1
    F6 = F3 + F12;
    R5 = PASS R2;                                       // x1 = in
    F0 = F6 * F14;                                      // * plaits_gain
_ps_mix_end:
    DM(I1, M6) = R0;
    DM(16, I0) = R5;                                    // t[32], t[33]
    DM(17, I0) = R6;

    R9 = DM(1, I7);
    R10 = DM(2, I7);
    R11 = DM(3, I7);
    R13 = DM(4, I7);
    R14 = DM(5, I7);
    R15 = DM(6, I7);
    I7=MODIFY (I7,6)(NW);
    L4 = DM(1, I7);
    L1 = DM(2, I7);
    L0 = DM(3, I7);
    M6 = DM(4, I7);
    I1 = DM(5, I7);
    I0 = DM(6, I7);
    I12 = DM(M7, I6);
    JUMP (M14, I12) (DB);
    RFRAME;
    NOP;
