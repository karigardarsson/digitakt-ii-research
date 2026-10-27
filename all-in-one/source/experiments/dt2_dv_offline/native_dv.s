/* DT2 OS 1.16 DV-F/DV-T guest-memory research kernel.
 * ABI: D2=stock phase, D5=signed delta, D3=WAVE, D4=MODE,
 * A2=stock state+0x24, A3=parameters, A6=original tick frame SP.
 * Return D0=signed Q31. Caller saves D1-D7/A0-A6. No EMAC instructions.
 * Scratch state base is Unicorn-only; no DT2 RAM ownership is claimed.
 */

        .equ PERIOD, 0x5265c000
        .equ STATE_BASE, 0x11000000
        .equ GLOBAL_SEED, 0x11001ff0
        .equ STOCK_STATE, 0x44e66e9c
        .equ VOICE_STRIDE, 0x98

        .text
        .global dv_native
dv_native:
        move.l  %a2,%d0
        subi.l  #STOCK_STATE,%d0
        bmi     dv_error
        lea     STATE_BASE,%a0
        moveq   #0,%d1
index_loop:
        cmpi.l  #0x28,%d0
        blo     index_done
        subi.l  #0x28,%d0
        adda.l  #VOICE_STRIDE,%a0
        addq.l  #1,%d1
        cmpi.l  #48,%d1
        bhs     dv_error
        bra     index_loop
index_done:
        tst.l   %d0
        bne     dv_error
        cmpi.l  #4,%d4
        bhi     dv_error
        moveq   #2,%d7
        lsl.l   %d4,%d7
        moveq   #0,%d4
        move.w  4(%a3),%d4
        lsr.l   #8,%d4
        cmpi.l  #127,%d4
        bhi     dv_error

        tst.l   16(%a0)
        bne     initialized
        bsr     seed_mix
        lea     20(%a0),%a1
        moveq   #32,%d6
init_slots:
        bsr     rand32
        andi.l  #0xff000000,%d0
        eori.l  #0x80000000,%d0
        move.l  %d0,(%a1)+
        subq.l  #1,%d6
        bne     init_slots
        move.l  %d2,4(%a0)
        clr.l   8(%a0)
        clr.l   12(%a0)
        moveq   #1,%d0
        move.l  %d0,16(%a0)
        bra     emit_slot

initialized:
        cmpi.l  #8,%d3
        bne     no_retrigger
        tst.l   0x34(%a6)
        beq     no_retrigger
        clr.l   8(%a0)
        clr.l   12(%a0)
        move.l  %d2,4(%a0)
        bra     emit_slot

no_retrigger:
        move.l  4(%a0),148(%a0)
        move.l  %d2,4(%a0)
        move.l  148(%a0),%d0
        bsr     phase_bin
        move.l  %d1,%d6
        move.l  4(%a0),%d0
        bsr     phase_bin
        move.l  %d1,%d2
        moveq   #0,%d3
        move.l  %d5,%d0
        bmi     reverse_delta

forward_delta:
        cmpi.l  #PERIOD,%d0
        blo     forward_rem
        subi.l  #PERIOD,%d0
        addq.l  #1,%d3
forward_rem:
        add.l   148(%a0),%d0
        cmpi.l  #PERIOD,%d0
        blo     forward_bins
        subi.l  #PERIOD,%d0
        addq.l  #1,%d3
forward_bins:
        cmp.l   4(%a0),%d0
        bne     dv_error
        lsl.l   #4,%d3
        add.l   %d2,%d3
        sub.l   %d6,%d3
        bra     advance_steps

reverse_delta:
        neg.l   %d0
        cmpi.l  #PERIOD,%d0
        blo     reverse_rem
        subi.l  #PERIOD,%d0
        addq.l  #1,%d3
reverse_rem:
        move.l  148(%a0),%d1
        cmp.l   %d0,%d1
        bhs     reverse_sub
        addi.l  #PERIOD,%d1
        addq.l  #1,%d3
reverse_sub:
        sub.l   %d0,%d1
        cmp.l   4(%a0),%d1
        bne     dv_error
        lsl.l   #4,%d3
        add.l   %d6,%d3
        sub.l   %d2,%d3

advance_steps:
        add.l   %d3,8(%a0)
        tst.l   %d3
        beq     emit_slot
advance_loop:
        bsr     voice_step
        subq.l  #1,%d3
        bne     advance_loop

emit_slot:
        move.l  12(%a0),%d0
        lsl.l   #2,%d0
        lea     20(%a0,%d0.l),%a1
        move.l  (%a1),%d0
        rts

dv_error:
        move.l  #0xdeadbeef,%d0
        rts

/* Initialize this voice's RNG from one global seed, matching dv.py::_mix32.
 * Input D1=voice index. D0/D1/D6 scratch. All 48 seeds are derived natively.
 */
seed_mix:
        move.l  %d1,%d6
        moveq   #0,%d1
seed_track:
        cmpi.l  #3,%d6
        blo     seed_indices
        subi.l  #3,%d6
        addq.l  #1,%d1
        bra     seed_track
seed_indices:
        addq.l  #1,%d1
        addq.l  #1,%d6
        movea.l %d6,%a1       /* lfo+1 */
        move.l  %d1,%d6       /* track+1 */
        moveq   #0,%d0       /* 64-bit track product low */
        moveq   #0,%d1       /* high */
seed_track_product:
        addi.l  #0x9e3779b9,%d0
        bcc     seed_track_no_carry
        addq.l  #1,%d1
seed_track_no_carry:
        subq.l  #1,%d6
        bne     seed_track_product
        movea.l %d0,%a2
        movea.l %d1,%a3

        move.l  %a1,%d6      /* lfo+1 */
        moveq   #0,%d0
        moveq   #0,%d1
seed_lfo_product:
        addi.l  #0x85ebca6b,%d0
        bcc     seed_lfo_no_carry
        addq.l  #1,%d1
seed_lfo_no_carry:
        subq.l  #1,%d6
        bne     seed_lfo_product
        move.l  %a2,%d6
        eor.l   %d6,%d0
        move.l  %a3,%d6
        eor.l   %d6,%d1
        move.l  GLOBAL_SEED,%d6
        eor.l   %d6,%d0

        /* Python's input XOR is wider than 32 bits before its first >>16. */
        move.l  %d0,%d6
        lsr.l   #8,%d6
        lsr.l   #8,%d6
        lsl.l   #8,%d1
        lsl.l   #8,%d1
        or.l    %d1,%d6
        eor.l   %d6,%d0

        move.l  #0x7feb352d,%d1
        mulu.l  %d1,%d0
        move.l  %d0,%d1
        lsr.l   #8,%d1
        lsr.l   #7,%d1
        eor.l   %d1,%d0
        move.l  #0x846ca68b,%d1
        mulu.l  %d1,%d0
        move.l  %d0,%d1
        lsr.l   #8,%d1
        lsr.l   #8,%d1
        eor.l   %d1,%d0
        bne     seed_nonzero
        move.l  #0x6d2b79f5,%d0
seed_nonzero:
        move.l  %d0,(%a0)
        rts

/* Exact stock bin mapping. Threshold k is the first phase whose
 * ((phase*0x636ba875 >> 32) << 2) >> 27 equals k.
 */
phase_bin:
        moveq   #0,%d1
        lea     bin_thresholds(%pc),%a1
bin_loop:
        cmp.l   (%a1)+,%d0
        blo     bin_done
        addq.l  #1,%d1
        cmpi.l  #15,%d1
        bne     bin_loop
bin_done:
        rts

/* RNG: xorshift32, source-compatible with dv.py::_Voice.random32.
 * D0 return, D1 scratch; A0 voice pointer.
 */
rand32:
        move.l  (%a0),%d0
        move.l  %d0,%d1
        lsl.l   #8,%d1
        lsl.l   #5,%d1
        eor.l   %d1,%d0
        move.l  %d0,%d1
        lsr.l   #8,%d1
        lsr.l   #8,%d1
        lsr.l   #1,%d1
        eor.l   %d1,%d0
        move.l  %d0,%d1
        lsl.l   #5,%d1
        eor.l   %d1,%d0
        move.l  %d0,(%a0)
        rts

/* One logical boundary crossing. D3 is caller's crossing countdown;
 * D4=SPH, D7=length, A0=voice. D0-D2/D5-D6/A1-A2 are scratch.
 */
voice_step:
        move.l  12(%a0),%d6
        addq.l  #1,%d6
        cmp.l   %d7,%d6
        blo     cursor_ready
        clr.l   %d6
cursor_ready:
        move.l  %d6,12(%a0)
        cmpi.l  #64,%d4
        blo     maybe_refresh
        beq     step_done
        bsr     rand32
        bsr     mod63
        move.l  %d4,%d2
        subi.l  #64,%d2
        cmp.l   %d2,%d0
        bhs     step_done
        bsr     rand32
        move.l  %d7,%d1
        subq.l  #1,%d1
        and.l   %d1,%d0
        lsl.l   #2,%d0
        lea     20(%a0,%d0.l),%a1
        move.l  %d6,%d2
        lsl.l   #2,%d2
        lea     20(%a0,%d2.l),%a2
        move.l  (%a1),%d0
        move.l  (%a2),%d1
        move.l  %d0,(%a2)
        move.l  %d1,(%a1)
        bra     step_done

maybe_refresh:
        bsr     rand32
        andi.l  #63,%d0
        moveq   #64,%d2
        sub.l   %d4,%d2
        cmp.l   %d2,%d0
        bhs     step_done
        bsr     rand32
        andi.l  #0xff000000,%d0
        eori.l  #0x80000000,%d0
        move.l  %d6,%d2
        lsl.l   #2,%d2
        lea     20(%a0,%d2.l),%a1
        move.l  %d0,(%a1)
step_done:
        rts

/* x mod 63 via six base-64 digits: 64 == 1 (mod 63). */
mod63:
        moveq   #0,%d2
        moveq   #6,%d1
mod63_digit:
        move.l  %d0,%d5
        andi.l  #63,%d5
        add.l   %d5,%d2
        lsr.l   #6,%d0
        subq.l  #1,%d1
        bne     mod63_digit
        move.l  %d2,%d0
mod63_reduce:
        cmpi.l  #63,%d0
        blo     mod63_done
        subi.l  #63,%d0
        bra     mod63_reduce
mod63_done:
        rts

        .align 2
bin_thresholds:
        .long 0x05265c01,0x0a4cb801,0x0f731401,0x14997001
        .long 0x19bfcc01,0x1ee62801,0x240c8401,0x2932e001
        .long 0x2e593c01,0x337f9801,0x38a5f401,0x3dcc5001
        .long 0x42f2ac01,0x48190801,0x4d3f6401
