| Terminal audio-event observation only. Native note-on/off producers execute
| unchanged up to their ordinary 40139b54 event emitter. No synthetic note flag
| or direct DSP trigger write is used to infer producer reachability.
    .text
    .globl repair_event_sink
repair_event_sink:
    addq.l #1,0x47f0c000
    movea.l 4(%sp),%a0
    lea 0x47f0c004,%a1
    moveq #15,%d0
1:  move.l (%a0)+,(%a1)+
    subq.l #1,%d0
    bpl.s 1b
    rts
