# DSP inline voice hook

core/dsp-inline lets other modules render a Digitakt II voice with their own DSP code. It hooks inside the stock
sample renderer, after its prologue: a track whose machine type a module registered runs that module's routine for its
voices; every other track renders exactly as stock. On its own it changes nothing you can hear.

- Devices and firmware: Digitakt II 1.17.
- A machine module adds a row to the registry `inline_voices` (`type`, `pc`); the routine contract is at the top of
  `src/dsp/dsp-inline.s`. The hook enters the routine with the stock indirect jump `JUMP (M13, I12)`. The routine
  either fills the voice's 96 kHz work buffer (64 samples) and ends with `JUMP _di_return_work;` (the stock decimator
  then halves it), or writes the 32 output samples at 48 kHz itself and ends with `JUMP _di_return_out;` (the
  decimator is skipped). It never returns through an indirect jump: the encoding gate refuses `JUMP (M13, I13)`,
  which the stock code does not use.
- Helpers for routines: `_di_frame_hw` (a 16-bit word of the DSP's copy of the frame) and `_di_trig` (the voice's
  byte of the trigger latch, non-zero in the block of a note-on).
- Replaces core/dsp-voice (whole-renderer replacement, which stalls the unit); the two cannot be installed together.
- Known limits: at most 16 registered types; the direct 48 kHz mode is not yet tested on the unit. Evidence:
  docs/findings/06, "In-renderer hook: core/dsp-inline".

The gallery shows this text. Keep it free of firmware bytes and vendor tool names.
