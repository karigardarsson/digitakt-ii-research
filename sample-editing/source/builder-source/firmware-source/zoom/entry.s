| Source entry shims, AGPL-3.0-or-later. Exact displaced stock prologues.
 .text
 .globl source_zoom_hook
source_zoom_hook:
 link.w %a6,#-52
 movem.l %d2-%d7/%a2-%a4,(%sp)
 move.l 12(%a6),-(%sp)
 move.l 8(%a6),-(%sp)
 jsr zoom_key
 addq.l #8,%sp
 tst.l %d0
 beq 1f
 movem.l (%sp),%d2-%d7/%a2-%a4
 unlk %a6
 rts
1: jmp 0x40018206
 .globl source_zoom_rotation_hook
source_zoom_rotation_hook:
 link.w %a6,#-52
 movem.l %d2-%d7/%a2-%a5,(%sp)
 move.l 12(%a6),-(%sp)
 move.l 8(%a6),-(%sp)
 jsr zoom_encoder
 addq.l #8,%sp
 tst.l %d0
 beq 2f
 movem.l (%sp),%d2-%d7/%a2-%a5
 unlk %a6
 rts
2: jmp 0x400184f2
 .globl oneshot_zoom_wrapper,werp_zoom_wrapper,slice_zoom_wrapper
oneshot_zoom_wrapper:
 moveq #0,%d0
 bra 3f
werp_zoom_wrapper:
 moveq #1,%d0
 bra 3f
slice_zoom_wrapper:
 moveq #2,%d0
3:
 move.l %d0,-(%sp)
 move.l 12(%sp),-(%sp)
 move.l 12(%sp),-(%sp)
 jsr zoom_draw
 lea 12(%sp),%sp
 rts
 .globl zoom_factory_hook
zoom_factory_hook:
 move.l %a2,-(%sp)
 jsr zoom_factory_for
 addq.l #4,%sp
 rts
 .globl zoom_empty_top,zoom_empty_bottom
zoom_empty_top:
 bsr zoom_cell_call
 tst.l %d0
 beq 4f
 rts
4: jmp 0x4011312e
zoom_empty_bottom:
 bsr zoom_cell_call
 tst.l %d0
 beq 5f
 rts
5: jmp 0x40113fd4
zoom_cell_call:
 move.l %a5,-(%sp)
 move.l %a3,-(%sp)
 move.l %d3,-(%sp)
 move.l %d2,-(%sp)
 move.l %a2,-(%sp)
 jsr zoom_cell
 lea 20(%sp),%sp
 rts
