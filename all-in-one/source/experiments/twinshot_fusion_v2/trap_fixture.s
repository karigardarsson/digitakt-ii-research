| Transient emulator instrumentation only; never linked into a firmware build.
    .text
    .globl repair_event_sink
repair_event_sink:
    move.l %a0,0x47f0d020
    lea 0x47f0d000,%a0
    movem.l %d0-%d7,(%a0)
    movem.l %a1-%sp,0x24(%a0)
    movea.l %sp,%a1
    lea 0x47f0d080,%a0
    move.l #511,%d0
1:  move.l (%a1)+,(%a0)+
    subq.l #1,%d0
    bpl.s 1b
2:  bra.s 2b
    .globl repair_header_probe
repair_header_probe:
    move.l %d0,-(%sp)
    move.l 8(%sp),%d0
    move.l %d0,0x47f0e000
    move.l (%sp)+,%d0
    jsr 0x401e6344
    jmp 0x40016e72
