// core/dsp-inline: the in-renderer voice hook of the SHARC engine (docs/findings/06, "In-renderer hook:
// core/dsp-inline").
//
// The engine renders each of its 32 voices with the stock renderer at SW 0x1c4ecf; voice v has the record
// _fw_records + v * 0x1d8 and belongs to track v >> 1. patchkit overwrites the renderer's two instructions at SW
// 0x1c4f15 (after its prologue: the frame I6 holds the saved registers, DM(-6, I6) = the record, DM(-7, I6) = the
// voice's 32-float output, DM(-4, I6) = 32) with `JUMP _di_entry`. Here I4 = R4 = the record and R0 = 64, the value
// the stock code stores at DM(-5, I6) in the delay slot of its next jump.
//
// A voice whose track type has no row in the registry `inline_voices` (= _di_table, rows {type, pc}, filled by
// `[[entry]]` rows of the machine modules) leaves through patchkit's tail _fw_stock: the two displaced instructions,
// then `JUMP 0x1c4f18`, with R0 and I4 as they came in. A patched image with no row renders exactly the stock audio.
//
// Routine ABI. The routine is entered with JUMP (M13, I12) (the stock 32-bit word 083f283f) and returns with a
// direct jump to one of the two return labels below; it may CALL its own subroutines (and the helpers below) and
// return from them with RTS.
//   in   R4 = voice record (0x1d8 bytes), R0 = voice index v (0..31), R2 = track v >> 1, R1 = type,
//        I1 = record + 4 (the 96 kHz work buffer, 64 floats), I2 = the voice's final output (32 floats at 48 kHz,
//        the pointer the renderer keeps at DM(-7, I6)).
//   return  the label picks the continuation (no register carries a return mode):
//        `JUMP _di_return_work;`  the routine wrote 64 floats at 96 kHz to the work buffer; the stock decimator
//                halves them into the output (continue at _fw_decimate, SW 0x1c524a).
//        `JUMP _di_return_out;`   the routine wrote the 32 output floats at 48 kHz itself; the decimator is skipped
//                and its state (record +0x114..+0x14b) is left as it was (continue at _fw_after_decimate, SW
//                0x1c5264).
//        Both are the absolute 48-bit jump (type 8a), as in the images that ran on the unit. A routine never
//        returns through an indirect jump: `JUMP (M13, I13)` (083f683f) does not occur in the stock code, and every
//        custom routine that stalled the owner's unit returned with it; Selache's 48-bit indirect jump (type 9a,
//        under .NOCOMPRESS) is mis-encoded (docs/findings/20-patch-toolchains.md). The encoding gate refuses both.
//        One continuation per routine: a routine ends at the same label in every block. _di_return_out leaves the
//        decimator's state (its ring and filter words at record +0x114..+0x14b) as the last _di_return_work block
//        or the stock renderer left it; a routine that switched between them would make the decimator start a
//        work-buffer block from that stale state (a click or worse).
//   may clobber  R0-R15, I0-I5, M1-M4, I12 and the ALU/multiplier state; the renderer's epilogue restores what its
//        caller needs.
//   must keep  I6, I7 (and the frame below I6: DM(-4..-26, I6)), M5-M7 (0, 1, -1), M13-M15 (0, 1, -1), the L
//        registers, MODE1, and every register not in the clobber list (I8-I11, I13-I15, M0, M8-M12, B
//        registers). L6 and L7 are NOT zero: the firmware's stack is a circular buffer (B6 = B7, L6 = L7, MODE1
//        CBUFEN; docs/findings/06-sharc-engine-and-startup.md, "machines/plaits on core/dsp-inline"). A routine
//        that moves I7 to a stack of its own must save L6/L7, zero them while it uses that stack and restore them
//        (modules/machines/plaits/src/dsp/plaits_entry.s does), or every push and frame MODIFY wraps by L.
//
// Instruction shapes: only stock or silicon-proven shapes (the encoding gate checks the image); right shifts use a
// negative amount in a register.

.SECTION/PM seg_pmco;
.GLOBAL _di_entry;
.GLOBAL _di_return_work;
.GLOBAL _di_return_out;
.EXTERN _di_table;
.EXTERN _fw_records;
.EXTERN _fw_type_cache;
.EXTERN _fw_frame;
.EXTERN _fw_trig_latch;
.EXTERN _fw_release_latch;
.EXTERN _fw_decimate;
.EXTERN _fw_after_decimate;
.EXTERN _fw_stock;

_di_entry:
    DM(-5, I6) = R0;                      // the stock delay-slot store, done early: R0 is free from here on
    R2 = R4;
    R1 = _fw_records;
    R2 = R2 - R1;                         // byte offset of the record
    R1 = 0;
    COMP(R2, R1);
    IF LT JUMP _di_stock;
    R1 = 15104;                           // 32 * 0x1d8
    COMP(R2, R1);
    IF GE JUMP _di_stock;
    R0 = 0;
    R1 = 472;                             // 0x1d8
_di_div:
    COMP(R2, R1);
    IF LT JUMP _di_voice;
    R2 = R2 - R1;
    R0 = R0 + 1;
    JUMP _di_div;
_di_voice:
    // R0 = v. The type cache holds one 16-bit type per track, two tracks per 32-bit word (track t in the low half
    // when t is even): the word is at byte offset 4 * (t >> 1) = v & ~3.
    R3 = -1;
    R2 = LSHIFT R0 BY R3;                 // t
    R1 = 0xfffffffc;
    R1 = R0 AND R1;
    R3 = _fw_type_cache;
    .NOCOMPRESS;
    R1 = R1 + R3;
    .COMPRESS;
    I0 = R1;
    R1 = DM(I0, M5);
    R3 = 1;
    R3 = R2 AND R3;
    R3 = LSHIFT R3 BY 4;
    R3 = -R3;                             // 0 or -16
    R1 = LSHIFT R1 BY R3;
    R3 = 0xffff;
    R1 = R1 AND R3;                       // the track's type
    I0 = _di_table;
_di_scan:
    R3 = DM(I0, M6);                      // row type; 0 ends the table (the last row is always empty)
    R12 = DM(I0, M6);                     // row pc
    R3 = PASS R3;
    IF EQ JUMP _di_stock;
    COMP(R3, R1);
    IF NE JUMP _di_scan;

    // A registered type.
    I12 = R12;
    R3 = R4;
    R5 = 4;
    .NOCOMPRESS;
    R3 = R3 + R5;
    .COMPRESS;
    I1 = R3;                              // the work buffer
    I2 = DM(-7, I6);                      // the output
    JUMP (M13, I12);                      // the 32-bit form, the stock word 083f283f

// The routine's two continuations (the ABI above).
_di_return_work:
    JUMP _fw_decimate;
_di_return_out:
    // The state the decimator's call leaves at its return point: I7 two words below the renderer's (the caller's
    // stack slot and the CJUMP frame word); the delay slot of the stock jump there adds them back.
    MODIFY(I7, -2) (NW);
    JUMP _fw_after_decimate;

_di_stock:
    R0 = DM(-5, I6);
    JUMP _fw_stock;

// ---- helpers for machine routines (ordinary subroutines: CALL _di_..., return with RTS) ----

// R0 = the unsigned 16-bit word k of the DSP's frame copy (R0 = k, 0..511). Clobbers R1, R2, I4.
.GLOBAL _di_frame_hw;
_di_frame_hw:
    R1 = 0xfffffffe;
    R1 = R0 AND R1;
    R1 = LSHIFT R1 BY 1;                  // byte offset of the 32-bit word that holds words k & ~1 and k | 1
    R2 = _fw_frame;
    .NOCOMPRESS;
    R1 = R1 + R2;
    .COMPRESS;
    I4 = R1;
    R1 = DM(I4, M5);
    R2 = 1;
    R2 = R0 AND R2;
    R2 = LSHIFT R2 BY 4;
    R2 = -R2;                             // 0 or -16
    R0 = LSHIFT R1 BY R2;
    R2 = 0xffff;
    .NOCOMPRESS;
    RTS (DB);                             // the stock return form is delayed
    .COMPRESS;
    R0 = R0 AND R2;
    NOP;

// R0 = voice v's byte of the trigger latch (R0 = v, 0..31): non-zero in the blocks of a note-on. Clobbers R1, R2,
// I4.
.GLOBAL _di_trig;
_di_trig:
    R1 = 0xfffffffc;
    R1 = R0 AND R1;
    R2 = _fw_trig_latch;
    .NOCOMPRESS;
    R1 = R1 + R2;
    .COMPRESS;
    I4 = R1;
    R1 = DM(I4, M5);
    R2 = 3;
    R2 = R0 AND R2;
    R2 = LSHIFT R2 BY 3;
    R2 = -R2;                             // -8 * (v & 3)
    R0 = LSHIFT R1 BY R2;
    R2 = 0xff;
    .NOCOMPRESS;
    RTS (DB);
    .COMPRESS;
    R0 = R0 AND R2;
    NOP;

// R0 = voice v's byte of the release flag (R0 = v, 0..31): 1 from the DT2 note-off to the next note-on, 0 from the note-on
// (the release path stores 1, the trigger path 0: core/dsp-inline's module.toml, anchor dsp.inline.release_latch).
// Clobbers R1, R2, I4.
.GLOBAL _di_released;
_di_released:
    R1 = 0xfffffffc;
    R1 = R0 AND R1;
    R2 = _fw_release_latch;
    .NOCOMPRESS;
    R1 = R1 + R2;
    .COMPRESS;
    I4 = R1;
    R1 = DM(I4, M5);
    R2 = 3;
    R2 = R0 AND R2;
    R2 = LSHIFT R2 BY 3;
    R2 = -R2;                             // -8 * (v & 3)
    R0 = LSHIFT R1 BY R2;
    R2 = 0xff;
    .NOCOMPRESS;
    RTS (DB);
    .COMPRESS;
    R0 = R0 AND R2;
    NOP;
