// Original lifecycle integration. Native header writer is unchanged except
// two hash-gated jump sites. No firmware payload or new interrupt masking.
.SECTION/PM seg_pmco;
.NOCOMPRESS;
.GLOBAL _tw_header_begin;
.GLOBAL _tw_header_end;
.EXTERN tw_generations.;
.EXTERN _tw_header_body;
.EXTERN _tw_header_return;

_tw_header_begin:
    R0 = PASS R4;
    IF LT JUMP _tw_header_resume;
    R2 = 0x401;
    COMP(R4, R2);
    IF GE JUMP _tw_header_resume;
    R0 = LSHIFT R4 BY 2;
    R1 = tw_generations.;
    R0 = R0 + R1;
    I4 = R0;
    R0 = DM(I4, M5);
    R0 = R0 + 1;
    DM(I4, M5) = R0;
_tw_header_resume:
    MODIFY(I7, -2) (NW);
    JUMP _tw_header_body;

_tw_header_end:
    // Displaced stores complete the native metadata update first.
    DM(2, I4) = R1;
    DM(3, I4) = R0;
    R0 = LSHIFT R4 BY 2;
    R1 = tw_generations.;
    R0 = R0 + R1;
    I4 = R0;
    R0 = DM(I4, M5);
    R0 = R0 + 1;
    DM(I4, M5) = R0;
    JUMP _tw_header_return;

