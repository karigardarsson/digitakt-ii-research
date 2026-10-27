| machines/plaits on the Digitakt II (1.17): the value text of the MODEL parameter.
|
| core/params puts this function's address in MODEL's parameter row (+0x34, `formatter = "plaits_model_fmt"` in
| module.toml). Every place the firmware shows a parameter value as text calls that word: the knob-turn header
| `Model=...` (param_value_text) and ParameterSet::vfunc_23 (research-model-names.md, finding 02).
|
|     void plaits_model_fmt(int raw, char *buf)
|
| The stock formatters' convention, read from their bytes (e.g. the LFO waveform and Euclid operator formatters):
| cdecl, `raw` at 4(sp) (the parameter word in 8.8, UI value = raw >> 8, asr), `buf` at 8(sp), no length argument;
| d0/d1/a0/a1 are scratch, everything else is preserved; `rts`, the caller pops the arguments; the callers ignore d0.
| The text is the model's name, NUL-terminated (at most 11 characters plus the NUL; the header path's buffer is
| 64 bytes), or "ERR" when raw >> 8 is outside 0..24, as the stock table formatters do. No firmware calls, no bss.

    .text
    .globl plaits_model_fmt
plaits_model_fmt:
    move.l  4(%sp),%d0
    asr.l   #8,%d0              | raw 0 .. 0x1800 -> model 0 .. 24
    movea.l 8(%sp),%a1
    cmpi.l  #24,%d0
    bls.s   1f                  | unsigned: a negative index fails too
    lea     pm_err,%a0
    bra.s   2f
1:  lea     pm_names,%a0
    movea.l (%a0,%d0.l*4),%a0
2:  move.b  (%a0)+,(%a1)+       | strcpy, NUL included
    bne.s   2b
    rts

    .section .rodata
    .balign 4
pm_names:
    .long   pm0, pm1, pm2, pm3, pm4, pm5, pm6, pm7, pm8, pm9, pm10, pm11
    .long   pm12, pm13, pm14, pm15, pm16, pm17, pm18, pm19, pm20, pm21, pm22, pm23, pm24
pm_err: .asciz "ERR"
pm0:    .asciz "VA"
pm1:    .asciz "ASYM-TRI"
pm2:    .asciz "2FM"
pm3:    .asciz "FORMANT"
pm4:    .asciz "ADDITIVE"
pm5:    .asciz "WAVETABLE"
pm6:    .asciz "CHORD"
pm7:    .asciz "SPEECH"
pm8:    .asciz "SWARM"
pm9:    .asciz "NOISE"
pm10:   .asciz "DUST"
pm11:   .asciz "STRING"
pm12:   .asciz "RINGS"
pm13:   .asciz "KICK"
pm14:   .asciz "SNARE"
pm15:   .asciz "HIHAT"
pm16:   .asciz "VCF-VA"
pm17:   .asciz "PHASE-DIST"
pm18:   .asciz "DX7-1"
pm19:   .asciz "DX7-2"
pm20:   .asciz "DX7-3"
pm21:   .asciz "DX7-4"
pm22:   .asciz "TERRAIN"
pm23:   .asciz "STRING MACH"
pm24:   .asciz "SQUARE"
