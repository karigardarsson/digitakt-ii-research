// machines/plaits: StringSynthOscillator's sample loop (lib/string_synth_oscillator.c, CHORD), hand-written SISD kernel.
//
// string_synth_kernel(float *st, const float *k, float *out, int size):
//   st[0..7] = the values of fm and the saw 8, 4, 2, 1 gain interpolators, phase, next_sample, segment (int) (in and
//   out); k[0..4] = their increments, then the loop's literals k[5] 8.0f, k[6] 2.0f, k[7] 1.0f, k[8] 0.5f,
//   k[9] -0.5f, k[10] 4.0f, k[11] 0.125f, k[12] 0.25f, k[13] 8388608.0f (0x4B000000, itof_nonneg's bias).
//   Adds `size` samples to out, bit-identical to selcc's code of the C loop (-DPL_SS_C): the same operations with the
//   same operands in the same order, the same comparisons (operands and condition or its complement), selcc's
//   division for fraction / f and itof_nonneg written out (tests/plaits/test_string_synth_kernel.py).
//
// State on return: R0-R8, R12, I4, LCNTR and the flags change; every other register is as on entry (R9-R11, R13-R15
// saved above I7; I0, M6, L0, L4 pushed through R0, M6 = 1 and L0 = L4 = 0 set here). No SIMD.
// Registers in the loop: R15 f, R14/R13/R11/R10 the saw 8/4/2/1 gains, R9 phase, R8 next_sample, R7 segment,
// R6 this_sample, R12 = 0x4B000000, R1 next_segment, R2 discontinuity; I4 = k, I0 = out.

// ABI: like the C around it, the kernel relies on the C ABI's M5 = 0 and M7 = -1 (and the shim's L6 = L7 = 0); it
// sets M6 = 1 itself because it post-modifies with M6 and the C does not guarantee it (the hook's M6 = 1 is not
// relied on). DAG registers are restored by loads through I7 (same DAG): the PRM's Type 3a/15a text forbids that
// direction too, but stock does it thousands of times and the RINGS and drum kernels do it on the unit (H-opt2/3).

.SECTION/PM seg_pmco;
.GLOBAL string_synth_kernel.;
.COMPRESS;  // the whole file (Task O5): the assembler keeps DO loops 48-bit (PRM: a loop's last 11)

string_synth_kernel.:
    R0 = I0;
    DM(I7, M7) = R0;
    R0 = M6;
    DM(I7, M7) = R0;
    R0 = L0;
    DM(I7, M7) = R0;
    R0 = L4;
    DM(I7, M7) = R0;
    M6 = 1;
    L0 = 0;
    L4 = 0;
    I7=MODIFY (I7,-7)(NW);
    DM(1, I7) = R9;
    DM(2, I7) = R10;
    DM(3, I7) = R11;
    DM(4, I7) = R13;
    DM(5, I7) = R14;
    DM(6, I7) = R15;
    DM(7, I7) = R4;                                     // st
    I0 = R12;                                           // out
    R12 = PASS R8;                                      // k
    I4 = R4;
    R15 = DM(0, I4);
    R14 = DM(1, I4);
    R13 = DM(2, I4);
    R11 = DM(3, I4);
    R10 = DM(4, I4);
    R9 = DM(5, I4);
    R8 = DM(6, I4);
    R7 = DM(7, I4);
    I4 = R12;
    R12 = DM(13, I4);                                   // 0x4B000000
    R4 = DM(1, I6);                                     // size
    R0 = 0;
    COMP(R4, R0);
    IF LE JUMP _sy_done;
    LCNTR = R4, DO _sy_end UNTIL LCE;
    R6 = PASS R8;                                       // this_sample = next_sample
    R8 = 0;                                             // next_sample = 0.0f
    R0 = DM(0, I4);
    F15 = F15 + F0;                                     // the five Next() calls: f
    R0 = DM(1, I4);
    F14 = F14 + F0;
    R0 = DM(2, I4);
    F13 = F13 + F0;
    R0 = DM(3, I4);
    F11 = F11 + F0;
    R0 = DM(4, I4);
    F10 = F10 + F0;
    F9 = F9 + F15;                                      // phase += f
    R1 = TRUNC F9;                                      // next_segment
    COMP(R1, R7);
    IF EQ JUMP _sy_same;
    R2 = 0;                                             // discontinuity = 0.0f
    R0 = 8;
    COMP(R1, R0);
    IF NE JUMP _sy_n8;
    R0 = DM(5, I4);
    F9 = F9 - F0;                                       // phase -= 8.0f
    R0 = 8;
    R1 = R1 - R0;                                       // next_segment -= 8
    F2 = F2 - F14;                                      // discontinuity -= saw_8_gain
_sy_n8:
    R0 = 3;
    R0 = R1 AND R0;
    R3 = 0;
    COMP(R0, R3);
    IF NE JUMP _sy_a3;
    F2 = F2 - F13;                                      // -= saw_4_gain
_sy_a3:
    R0 = 1;
    R0 = R1 AND R0;
    R3 = 0;
    COMP(R0, R3);
    IF NE JUMP _sy_a1;
    F2 = F2 - F11;                                      // -= saw_2_gain
_sy_a1:
    F2 = F2 - F10;                                      // -= saw_1_gain
    R3 = 0;
    COMP(F2, F3);                                       // discontinuity != 0.0f
    IF EQ JUMP _sy_same;
    R0 = R12 OR R1;
    F0 = F0 - F12;                                      // itof_nonneg(next_segment)
    F0 = F9 - F0;                                       // fraction
    R3 = DM(6, I4);                                     // selcc's fraction / f
    F4 = RECIPS F15, R15 = R15;
    F5 = F15 * F4;
    F5 = F3 - F5;
    F4 = F4 * F5;
    F5 = F15 * F4;
    F5 = F3 - F5;
    F4 = F4 * F5;
    F3 = F0 * F4;
    F5 = F15 * F3;
    F5 = F0 - F5;
    F5 = F5 * F4;
    F3 = F3 + F5;                                       // t
    R0 = DM(7, I4);
    F0 = F0 - F3;                                       // u = 1.0f - t
    R4 = DM(8, I4);
    F4 = F4 * F3;                                       // 0.5f * t
    F4 = F4 * F3;                                       // * t
    F4 = F4 * F2;                                       // * discontinuity
    F6 = F6 + F4;                                       // this_sample +=
    R4 = DM(9, I4);
    F4 = F4 * F0;                                       // -0.5f * u
    F4 = F4 * F0;                                       // * u
    F4 = F4 * F2;                                       // * discontinuity
    F8 = F8 + F4;                                       // next_sample +=
_sy_same:
    R7 = PASS R1;                                       // segment = next_segment
    R0 = DM(10, I4);
    F0 = F9 - F0;                                       // phase - 4.0f
    F0 = F0 * F14;                                      // * saw_8_gain
    R1 = DM(11, I4);
    F0 = F0 * F1;                                       // * 0.125f
    F8 = F8 + F0;
    R0 = 4;
    R0 = R7 AND R0;
    R0 = R12 OR R0;
    F0 = F0 - F12;                                      // itof_nonneg(segment & 4)
    F0 = F9 - F0;
    R1 = DM(6, I4);
    F0 = F0 - F1;                                       // - 2.0f
    F0 = F0 * F13;                                      // * saw_4_gain
    R1 = DM(12, I4);
    F0 = F0 * F1;                                       // * 0.25f
    F8 = F8 + F0;
    R0 = 6;
    R0 = R7 AND R0;
    R0 = R12 OR R0;
    F0 = F0 - F12;                                      // itof_nonneg(segment & 6)
    F0 = F9 - F0;
    R1 = DM(7, I4);
    F0 = F0 - F1;                                       // - 1.0f
    F0 = F0 * F11;                                      // * saw_2_gain
    R1 = DM(8, I4);
    F0 = F0 * F1;                                       // * 0.5f
    F8 = F8 + F0;
    R0 = 7;
    R0 = R7 AND R0;
    R0 = R12 OR R0;
    F0 = F0 - F12;                                      // itof_nonneg(segment & 7)
    F0 = F9 - F0;
    R1 = DM(8, I4);
    F0 = F0 - F1;                                       // - 0.5f
    F0 = F0 * F10;                                      // * saw_1_gain
    F8 = F8 + F0;
    R0 = DM(I0, M5);
    R1 = DM(6, I4);
    F1 = F1 * F6;                                       // 2.0f * this_sample
    F0 = F0 + F1;
_sy_end:
    DM(I0, M6) = R0;
_sy_done:
    R4 = DM(7, I7);
    I4 = R4;
    DM(0, I4) = R15;
    DM(1, I4) = R14;
    DM(2, I4) = R13;
    DM(3, I4) = R11;
    DM(4, I4) = R10;
    DM(5, I4) = R9;
    DM(6, I4) = R8;
    DM(7, I4) = R7;
    R9 = DM(1, I7);
    R10 = DM(2, I7);
    R11 = DM(3, I7);
    R13 = DM(4, I7);
    R14 = DM(5, I7);
    R15 = DM(6, I7);
    I7=MODIFY (I7,7)(NW);
    L4 = DM(1, I7);
    L0 = DM(2, I7);
    M6 = DM(3, I7);
    I0 = DM(4, I7);
    I12 = DM(M7, I6);
    JUMP (M14, I12) (DB);
    RFRAME;
    NOP;
