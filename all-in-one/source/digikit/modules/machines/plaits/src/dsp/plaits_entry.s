// machines/plaits: the voice routine of the PLAITS machine type on core/dsp-inline (ABI in
// modules/core/dsp-inline/src/dsp/dsp-inline.s), the shim between the hook and the C voice wrapper (plaits.c).
//
// One source, two builds: the file listed first in the object's sources defines PLAITS_MODE.
//   PLAITS_MODE 1 (machines/plaits-48k): 32 samples at 48 kHz into the voice's output (I2); returns with
//                 `JUMP _di_return_out` (the stock decimator is skipped).
//   PLAITS_MODE 0 (machines/plaits-96k): 64 samples at 96 kHz (the 48 kHz block through the half-band
//                 interpolator, halfband.c) into the work buffer (I1); returns with `JUMP _di_return_work`.
// A routine uses one continuation only: the type always returns through the same label.
//
// Per block, voice v of track t (R0 = v, R2 = t, R4 = the voice record):
//   odd v   the track's two voices are its left and right bus (docs/findings/15, "From the frame to a voice's
//           sample pointer"; finding 06: buffers 0x80 bytes apart): the odd voice copies the even voice's block,
//           which the voice loop rendered just before it (records in order): the output (mode 1, I2 - 0x80 bytes)
//           or the work buffer (mode 0, I1 - 0x1d8 bytes, the even voice's record + 4).
//   even v  reads the block's controls into _pl_frame (PlaitsFrame, plaits.h) - the frame copy's halfwords
//           1 + t (pitch, note << 8) and 0x6d + 0x30 t + k for the machine slots k = 0 TUNE, 1 MODEL, 2 HARMONICS,
//           6 TIMBRE, 7 MORPH, 8 MIX (byte 0xda + 0x60 t + 2 (mirror - 25); slot 3 is MANUAL SLICE's sample slot and
//           unused) and halfword 0x1a + t (velocity << 8, byte 0x34 + 2t, finding 06), each converted to float, and
//           the record's ACTIVE byte (+0x1b8) - and the trigger-latch byte
//           (_di_trig), then calls the C function plaits_track(t, &_pl_frame, trig, out, n, rate) on a stack of its
//           own (_pl_stack, L2), in the C calling convention: arguments R4, R8, R12 and three on the stack, the
//           frame set up by CJUMP, the return address - 1 in the second delay slot (the stock call sequence).
//
// Registers: the renderer's I6, I7, L6 and L7 are saved in _pl_save and restored after the C call (L6 = L7 = 0 during
// it: the firmware's stack is a circular buffer, ours is linear). The order of the switch is chosen so that an
// interrupt between any two of its instructions pushes inside one stack (test_an_interrupt_at_any_instruction_...):
// L6/L7 are zeroed while I7 is still on the firmware's stack, I7 moves to _pl_stack after, and on the way back I7
// returns before L6/L7 are restored. A firmware I7 with L = 0 pushes linearly at the very addresses a circular push
// would use, as long as the push does not run below B7 (where a circular push would overwrite the stack's own
// oldest frames, so the firmware cannot rely on it); our I7 with the firmware's L would not: _pl_stack lies above
// B7 + L7, so each push would wrap by -L7 into foreign memory. So never move I7 before zeroing L7, and never restore
// L7 before I7. The C code (Selache)
// uses R0-R15, I4, I6, I7, I12 and reads M5 (0), M7 (-1), M14 (1); it leaves I8-I11, I13-I15, M0-M4, M8-M12, the B
// and L registers and MODE1 alone, as the routine ABI requires. Symbol offsets are computed in registers (Selache
// drops `symbol + constant` addends, docs/findings/20-patch-toolchains.md); right shifts are not used.

#ifndef PLAITS_MODE
#error PLAITS_MODE is not defined: list the mode file before plaits_entry.s in the object's sources
#endif

.SECTION/PM seg_pmco;
.GLOBAL _pl_render;
.EXTERN _di_frame_hw;
.EXTERN _di_trig;
.EXTERN _di_released;
.EXTERN _di_return_work;
.EXTERN _di_return_out;
.EXTERN plaits_track.;

// Every instruction in its normal 48-bit form; no indirect jumps.
.NOCOMPRESS;

_pl_render:
    R8 = PASS R0;                         // v
    R9 = PASS R2;                         // t
    R10 = PASS R4;                        // the voice record
    R0 = 1;
    R0 = R8 AND R0;
    IF NE JUMP _pl_odd;

    // ---- the even voice: the block's controls into _pl_frame (I5 walks it)
    R0 = _pl_frame;
    I5 = R0;
    R0 = R9 + 1;                          // halfword 1 + t: pitch
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R11 = LSHIFT R9 BY 5;
    R1 = LSHIFT R9 BY 4;
    R11 = R11 + R1;
    R1 = 0x6d;
    R11 = R11 + R1;                       // halfword 0x6d + 0x30 t: machine slot 0
    R0 = PASS R11;                        // TUNE
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R0 = R11 + 1;                         // MODEL
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R1 = 2;
    R0 = R11 + R1;                        // HARMONICS
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R1 = 6;
    R0 = R11 + R1;                        // TIMBRE (mirror 31)
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R1 = 7;
    R0 = R11 + R1;                        // MORPH (mirror 32)
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R1 = 8;
    R0 = R11 + R1;                        // MIX (mirror 33)
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R1 = 0x1a;
    R0 = R9 + R1;                         // halfword 0x1a + t: velocity << 8
    CALL _di_frame_hw;
    F0 = FLOAT R0;
    DM(I5, M6) = R0;
    R1 = 0x1b8;
    R1 = R10 + R1;
    I4 = R1;
    R0 = DM(I4, M5);                      // the word at +0x1b8: ACTIVE is its low byte
    R1 = 0xff;
    R0 = R0 AND R1;
    DM(I5, M6) = R0;                      // PlaitsFrame.active
    R0 = PASS R8;
    CALL _di_released;
    DM(I5, M6) = R0;                      // PlaitsFrame.released (the DX7 models' gate)
    R0 = PASS R8;
    CALL _di_trig;
    R12 = PASS R0;                        // trig (the third argument)

    // ---- the C call on our own stack
    R0 = _pl_save;
    I3 = R0;
    R0 = I6;
    DM(I3, M6) = R0;
    R0 = I7;
    DM(I3, M6) = R0;
    // The firmware runs its stack as a circular buffer (B6 = B7, L6 = L7 non-zero, set at SW 0x1c0f64..0x1c0f76):
    // on our stack every push and frame MODIFY would wrap by L. Linear for the C call, restored after it.
    // Order (interrupts): L6/L7 = 0 first, while I7 is still the firmware's; only then I7 = _pl_stack.
    R0 = L6;
    DM(I3, M6) = R0;
    R0 = L7;
    DM(I3, M6) = R0;
    L6 = 0;
    L7 = 0;
    R0 = _pl_stack;
    R1 = 4092;                            // the stack's last word (_pl_stack is 4096 bytes)
    R0 = R0 + R1;
    I7 = R0;
#if PLAITS_MODE == 1
    R0 = 48000;                           // rate
    DM(I7, M7) = R0;
    R0 = 32;                              // n
    DM(I7, M7) = R0;
    R0 = I2;                              // out: the voice's output
    DM(I7, M7) = R0;
#else
    R0 = 96000;
    DM(I7, M7) = R0;
    R0 = 64;
    DM(I7, M7) = R0;
    R0 = I1;                              // out: the work buffer
    DM(I7, M7) = R0;
#endif
    R4 = PASS R9;                         // track
    R8 = _pl_frame;                       // &frame
    R2 = I6;
    CJUMP plaits_track. (DB);
    DM(I7, M7) = R2;
    DM(I7, M7) = _pl_back - 1;
_pl_back:
    // Order (interrupts): I6, I7 back to the firmware's stack first (L = 0 there is still safe), then L6/L7.
    R0 = _pl_save;
    I3 = R0;
    R0 = DM(I3, M6);
    I6 = R0;
    R0 = DM(I3, M6);
    I7 = R0;
    R0 = DM(I3, M6);
    L6 = R0;
    R0 = DM(I3, M6);
    L7 = R0;
    JUMP _pl_return;

    // ---- the odd voice: a copy of the even voice's block
_pl_odd:
#if PLAITS_MODE == 1
    R0 = I2;
    R1 = -128;                            // the even voice's output: 0x80 bytes below
    R0 = R0 + R1;
    I3 = R0;
    LCNTR = 32, DO _pl_copy UNTIL LCE;    // a hardware loop: no counter, branch or taken-branch stall per word
    R0 = DM(I3, M6);
_pl_copy:
    DM(I2, M6) = R0;
#else
    R0 = PASS R10;
    R1 = -468;                            // the even voice's work buffer: its record (0x1d8 bytes below) + 4
    R0 = R0 + R1;
    I3 = R0;
    LCNTR = 64, DO _pl_copy UNTIL LCE;
    R0 = DM(I3, M6);
_pl_copy:
    DM(I1, M6) = R0;
#endif

_pl_return:
    // The return: a direct jump (type 8a) to the hook's continuation for this mode.
#if PLAITS_MODE == 1
    JUMP _di_return_out;
#else
    JUMP _di_return_work;
#endif

.SECTION/DM seg_dmda;
.PKDATA _pl_frame, 40;                    // PlaitsFrame: 8 floats, the ACTIVE word and the RELEASED word
.PKDATA _pl_save, 16;                     // the renderer's I6, I7, L6 and L7 during the C call
.PKBSS _pl_stack, 4096;                   // the C stack (written before it is read)
// The 16 tracks' voices (plaits.c plaits_voice_mem, 16 x sizeof(PlaitsVoice) = 16 x 17,124 bytes; plaits.c checks the size at
// compile time). Reserved, not loaded: a C static would put 274 KB of zeros in the boot stream. plaits_track zeroes a voice's
// non-engine fields at its first use; the engines initialise their own state at a note-on that arms or changes them.
.PKBSS plaits_voice_mem., 273984;
