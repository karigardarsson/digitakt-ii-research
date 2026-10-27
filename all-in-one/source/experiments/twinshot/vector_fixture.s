| Offline-only caller frame for the real vector-191 trigger/lock path.
| The test replaces the instruction at 0x4002e5dc with a jump to the exit.
| It does not alter the intervening native event-handling instructions.
    .text
    .globl tw_vector_fixture
    .globl tw_vector_fixture_done
tw_vector_fixture:
    link.w %a6,#-208
    movem.l %d2-%d7/%a2-%a5,(%sp)
    move.l 8(%a6),%d2
    movea.l 12(%a6),%a1
    move.l %a1,-100(%a6)
    movea.l #0x47f19000,%a4
    moveq #0,%d6
    jmp 0x4002e4a0
tw_vector_fixture_done:
    move.l %d6,0x47f19020
    movem.l (%sp),%d2-%d7/%a2-%a5
    unlk %a6
    rts

| A second independent caller enters the actual smoothing -> split -> native
| frame modulation/LFO -> reconstruction route. Exit before later voice/mix
| preparation; no outgoing peripheral write is reached by this fixture.
    .globl tw_frame_fixture
    .globl tw_frame_fixture_done
tw_frame_fixture:
    link.w %a6,#-208
    movem.l %d2-%d7/%a2-%a5,(%sp)
    move.l %macsr,%d0
    move.l %d0,-164(%a6)
    move.l #0x20,%macsr
    moveq #0,%d6
    move.l #8,%d5
    jmp 0x4002e8c0
tw_frame_fixture_done:
    move.l -164(%a6),%d0
    move.l %d0,%macsr
    lea -208(%a6),%sp
    movem.l (%sp),%d2-%d7/%a2-%a5
    unlk %a6
    rts
