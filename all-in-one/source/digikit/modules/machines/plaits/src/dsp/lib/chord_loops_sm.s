// machines/plaits: CHORD's two buffer loops (engines/chord.c chord_render), hand-written SISD kernels.
//
// chord_clear_kernel(float *out, float *aux, int size): out[i] = aux[i] = 0.0f.
// chord_mix_kernel(float *out, float *aux, int size): out[i] += aux[i]; aux[i] *= 3.0f.
// Bit-identical to selcc's code of the C loops (-DPL_CHORD_C): the same words stored, out + aux and aux * 3.0f with
// selcc's operand order (tests/plaits/test_chord_kernels.py).
//
// State on return: R0-R3, I4 and the flags change (and LCNTR); every other register is as on entry (I0, M6, L0, L4
// pushed through R0, M6 = 1 and L0 = L4 = 0 set here). No SIMD.

// ABI: like the C around it, the kernel relies on the C ABI's M5 = 0 and M7 = -1 (and the shim's L6 = L7 = 0); it
// sets M6 = 1 itself because it post-modifies with M6 and the C does not guarantee it (the hook's M6 = 1 is not
// relied on). DAG registers are restored by loads through I7 (same DAG): the PRM's Type 3a/15a text forbids that
// direction too, but stock does it thousands of times and the RINGS and drum kernels do it on the unit (H-opt2/3).

.SECTION/PM seg_pmco;
.COMPRESS;  // the whole file (Task O5): the assembler keeps DO loops 48-bit (PRM: a loop's last 11)

.GLOBAL chord_clear_kernel.;
chord_clear_kernel.:
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
    I4 = R4;
    I0 = R8;
    R0 = 0;
    COMP(R12, R0);
    IF LE JUMP _cc_done;
    LCNTR = R12, DO _cc_end UNTIL LCE;
    DM(I4, M6) = R0;
_cc_end:
    DM(I0, M6) = R0;
_cc_done:
    L4 = DM(1, I7);
    L0 = DM(2, I7);
    M6 = DM(3, I7);
    I0 = DM(4, I7);
    I12 = DM(M7, I6);
    JUMP (M14, I12) (DB);
    RFRAME;
    NOP;

.GLOBAL chord_mix_kernel.;
chord_mix_kernel.:
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
    I4 = R4;
    I0 = R8;
    R3 = 0x40400000;                                    // 3.0f
    R0 = 0;
    COMP(R12, R0);
    IF LE JUMP _cm_done;
    LCNTR = R12, DO _cm_end UNTIL LCE;
    R0 = DM(I4, M5);                                    // out[i]
    R1 = DM(I0, M5);                                    // aux[i]
    F2 = F0 + F1;
    F1 = F1 * F3;
    DM(I4, M6) = R2;
_cm_end:
    DM(I0, M6) = R1;
_cm_done:
    L4 = DM(1, I7);
    L0 = DM(2, I7);
    M6 = DM(3, I7);
    I0 = DM(4, I7);
    I12 = DM(M7, I6);
    JUMP (M14, I12) (DB);
    RFRAME;
    NOP;
