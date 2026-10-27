// Explicit full-width casts, matching the native ISA/mandatory encoding gate.
// Metadata is bounded to positive signed 32-bit values before conversion.
.SECTION/PM seg_pmco;
.NOCOMPRESS;
.GLOBAL fusion_float.;
.GLOBAL fusion_bits.;
.GLOBAL fusion_phase.;
fusion_float.:
    F0 = FLOAT R4;
.COMPRESS;
    I12 = DM(M7, I6);
    JUMP (M14, I12) (DB);
    RFRAME;
    NOP;
.NOCOMPRESS;
fusion_bits.:
    R0 = R4;
.COMPRESS;
    I12 = DM(M7, I6);
    JUMP (M14, I12) (DB);
    RFRAME;
    NOP;
.NOCOMPRESS;
// Native length is frames; the Q31 cursor high word counts frame pairs.
// Keep this expression out of selcc's observed cross-call local-lifetime bug.
fusion_phase.:
    I4 = R4;
    R0 = DM(109, I4);
    R1 = DM(98, I4);
    F0 = FLOAT R0;
    F1 = FLOAT R1;
    F0 = F0 + F0;
.COMPRESS;
    F2 = RECIPS F1;
.NOCOMPRESS;
    R3 = 0x40000000;
    F4 = F1 * F2;
    F4 = F3 - F4;
    F2 = F2 * F4;
    F0 = F0 * F2;
.COMPRESS;
    I12 = DM(M7, I6);
    JUMP (M14, I12) (DB);
    RFRAME;
    NOP;
