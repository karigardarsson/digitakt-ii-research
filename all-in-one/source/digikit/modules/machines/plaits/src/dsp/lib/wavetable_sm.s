// machines/plaits: WavetableOscillator's sample loop (lib/wavetable_oscillator.c, CHORD), hand-written SISD kernel.
//
// wavetable_kernel(float *st, const float *k, float *out, int size, const float *waves, const int *offsets):
//   st[0..6] = the frequency, waveform and amplitude interpolators' values, phase, lp, the differentiator's lp and
//   previous (in and out); k[0..2] = their increments, k[3] = 128.0f, k[4] = 1.0f, k[5] = 8388608.0f (0x4B000000,
//   itof_nonneg's bias). Adds `size` samples to out, bit-identical to selcc's code of the C loop (-DPL_WT_C): the same
//   operations with the same operands in the same order, minf and itof_nonneg written out with selcc's own
//   comparisons and words (tests/plaits/test_wavetable_kernel.py).
//
// State on return: R0-R8, R12, I4, LCNTR and the flags change; every other register is as on entry (R9-R11, R13-R15
// saved above I7; I0, I1, M6, L0, L1, L4 pushed through R0, M6 = 1 and L0 = L1 = L4 = 0 set here). No SIMD.
// Registers in the loop: R15 frequency value, R14 waveform value, R13 amplitude value, R11 phase, R10 lp, R9 the
// differentiator's lp, R8 its previous, R12 = 0x4B000000, R3 = 1.0f, R2 cutoff; I4 = k, I0 = out, I1 the table reads.

// ABI: like the C around it, the kernel relies on the C ABI's M5 = 0 and M7 = -1 (and the shim's L6 = L7 = 0); it
// sets M6 = 1 itself because it post-modifies with M6 and the C does not guarantee it (the hook's M6 = 1 is not
// relied on). DAG registers are restored by loads through I7 (same DAG): the PRM's Type 3a/15a text forbids that
// direction too, but stock does it thousands of times and the RINGS and drum kernels do it on the unit (H-opt2/3).

.SECTION/PM seg_pmco;
.GLOBAL wavetable_kernel.;
.COMPRESS;  // the whole file (Task O5): the assembler keeps DO loops 48-bit (PRM: a loop's last 11)

wavetable_kernel.:
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
    I7=MODIFY (I7,-7)(NW);
    DM(1, I7) = R9;
    DM(2, I7) = R10;
    DM(3, I7) = R11;
    DM(4, I7) = R13;
    DM(5, I7) = R14;
    DM(6, I7) = R15;
    DM(7, I7) = R4;                                     // st
    I1 = R4;
    I4 = R8;                                            // k
    I0 = R12;                                           // out
    R15 = DM(0, I1);
    R14 = DM(1, I1);
    R13 = DM(2, I1);
    R11 = DM(3, I1);
    R10 = DM(4, I1);
    R9 = DM(5, I1);
    R8 = DM(6, I1);
    R12 = DM(5, I4);                                    // 0x4B000000
    R3 = DM(4, I4);                                     // 1.0f
    R4 = DM(1, I6);                                     // size
    R0 = 0;
    COMP(R4, R0);
    IF LE JUMP _wt_done;
    LCNTR = R4, DO _wt_end UNTIL LCE;
    R0 = DM(0, I4);
    F15 = F15 + F0;                                     // frequency_modulation.Next(): f0
    R1 = DM(3, I4);
    F2 = F1 * F15;                                      // 128.0f * f0
    COMP(F2, F3);                                       // cutoff = minf(.., 1.0f)
    IF GE R2 = R3 OR R3;
    F11 = F11 + F15;                                    // phase += f0
    F0 = F11 - F3;
    COMP(F11, F3);                                      // phase >= 1.0f: phase -= 1.0f
    IF GE R11 = R0 OR R0;
    R0 = DM(1, I4);
    F14 = F14 + F0;                                     // waveform_modulation.Next(): wf
    R4 = TRUNC F14;                                     // waveform_integral
    R5 = R12 OR R4;
    F5 = F5 - F12;                                      // itof_nonneg
    F5 = F14 - F5;                                      // waveform_fractional
    F6 = F11 * F1;                                      // p = phase * 128.0f
    R7 = TRUNC F6;                                      // p_integral
    R0 = R12 OR R7;
    F0 = F0 - F12;
    F6 = F6 - F0;                                       // p_fractional
    R0 = LSHIFT R4 BY 2;
    R1 = DM(3, I6);                                     // offsets (the sixth argument)
    R0 = R1 + R0;
    I1 = R0;
    R0 = DM(I1, M5);                                    // offsets[waveform_integral]
    R1 = DM(1, I1);                                     // offsets[waveform_integral + 1]
    R4 = DM(2, I6);                                     // waves (the fifth argument)
    R0 = LSHIFT R0 BY 2;
    R0 = R4 + R0;
    R1 = LSHIFT R1 BY 2;
    R1 = R4 + R1;
    R7 = LSHIFT R7 BY 2;
    R0 = R0 + R7;
    R1 = R1 + R7;
    I1 = R0;
    R0 = DM(I1, M5);                                    // t[p_integral]
    R4 = DM(1, I1);                                     // t[p_integral + 1]
    F4 = F4 - F0;
    F4 = F4 * F6;
    F0 = F0 + F4;                                       // x0
    I1 = R1;
    R1 = DM(I1, M5);
    R4 = DM(1, I1);
    F4 = F4 - F1;
    F4 = F4 * F6;
    F1 = F1 + F4;                                       // x1
    F1 = F1 - F0;
    F1 = F1 * F5;
    F0 = F0 + F1;                                       // sample
    F1 = F0 - F8;                                       // ONE_POLE(d_lp, sample - d_previous, cutoff)
    F1 = F1 - F9;
    F1 = F2 * F1;
    F9 = F9 + F1;
    R8 = PASS R0;                                       // d_previous = sample
    F1 = F9 - F10;                                      // ONE_POLE(lp, d_lp, cutoff)
    F1 = F2 * F1;
    F10 = F10 + F1;
    R0 = DM(2, I4);
    F13 = F13 + F0;                                     // amplitude_modulation.Next()
    R0 = DM(I0, M5);
    F1 = F13 * F10;
    F0 = F0 + F1;                                       // *out += amplitude * lp
_wt_end:
    DM(I0, M6) = R0;
_wt_done:
    R4 = DM(7, I7);
    I1 = R4;
    DM(0, I1) = R15;
    DM(1, I1) = R14;
    DM(2, I1) = R13;
    DM(3, I1) = R11;
    DM(4, I1) = R10;
    DM(5, I1) = R9;
    DM(6, I1) = R8;
    R9 = DM(1, I7);
    R10 = DM(2, I7);
    R11 = DM(3, I7);
    R13 = DM(4, I7);
    R14 = DM(5, I7);
    R15 = DM(6, I7);
    I7=MODIFY (I7,7)(NW);
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
