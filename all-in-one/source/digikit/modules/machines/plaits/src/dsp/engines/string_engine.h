// Copyright 2016 Emilie Gillet.
//
// Author: Emilie Gillet (emilie.o.gillet@gmail.com)
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.
//
// See http://creativecommons.org/licenses/MIT/ for more information.
//
//
// PLAITS for the Digitakt II: Plaits' StringEngine (model STRING: a Karplus-Strong string, three strings used in turn, each
// struck by a burst of noise; HARMONICS the string's non-linearity (below 0.24 a curved bridge, 0.24 to 0.26 none, above it
// dispersion), TIMBRE the brightness of the strike and of the string, MORPH the damping, to an infinite decay at the top)
// in C99, from plaits/dsp/engine/string_engine.{h,cc}, plaits/dsp/physical_modelling/string_voice.{h,cc},
// string.{h,cc} and delay_line.h, with stmlib's Svf, DCBlocker, ParameterInterpolator and Random.
// A STRIKE engine (PORTING.md drift 7): every note-on is TRIGGER_RISING_EDGE (the wrapper's note-on), HIGH after; the accent is the
// note-on's velocity. Upstream's sustain mode (TRIGGER_UNPATCHED: Dust noise as the excitation) is not ported: the wrapper
// never passes it, so `sustain` is always false.
// Memory (drift 25): each of the three strings has a delay line of 1024 floats and a stretch (all-pass) line of 256: 5,120 bytes
// and 15,360 bytes for the three, plus the f0 history (16 floats) and the state; the union is still DUST's. Upstream's arena is
// zeroed by Reset; string_engine_init is Init and Reset (every line cleared, every pointer 0), so it sets every field that is read later.
// Random: stmlib's global generator is one state per engine here (rng_, 0x21 at init as at Plaits' power-up, then
// 0x21 + track * 0x9e3779b1 by string_engine_seed so that tracks differ; track 0 is upstream's; the oracle seeds 0x21 when it
// selects the engine). Draws in upstream's order: per block, string 0 (excitation noise while it lasts, then one draw per
// processed sample in the dispersion mode), then string 1, then string 2.
// Deliberate differences from upstream's arithmetic: none for any f0 > 0. For f0 == 0 (the f0 history is zeros at the first
// strike: the first note-on of an engine leaves the third string with f0 = 0) the delay 1 / f0 is infinite upstream and is then
// constrained to 1020; here the division is skipped for f0 <= 0 and 1020 is used (a division by zero is not a defined quantity
// on the core): the same value.
// Layout (PORTING.md section 5): the string is a sequential recurrence (the delay line feeds back at a delay of 4 or more
// samples), so the lanes of a SIMD kernel are the three strings (voice_[i], same structure, one pad lane to make four); each
// string's loop keeps its state in locals and the delay lines are plain contiguous arrays.
#ifndef PLAITS_STRING_ENGINE_H
#define PLAITS_STRING_ENGINE_H
#include "engine.h"
#include "../lib/svf.h"

#define kNumStrings 3
#define kStringDelayLineSize 1024
#define kStringStretchSize 256

typedef struct {
  float string_[kStringDelayLineSize];
  float stretch_[kStringStretchSize];
  Svf excitation_filter_;
  Svf iir_damping_filter_;
  float dc_x_, dc_y_;
  float delay_, dispersion_noise_, curved_bridge_;
  float src_phase_;
  float out_sample_[2];
  int string_write_ptr_, stretch_write_ptr_;
  int remaining_noise_samples_;
  /* writes to string_ / stretch_ since string_voice_init: the DSP build (lib/string_sm.s) does not clear the lines and
     reads a word the C would still see cleared as 0.0f (lib/gen_string_kernel.py). The C does not read them. */
  int fresh_string_, fresh_stretch_;
} StringVoice;

typedef struct {
  StringVoice voice_[kNumStrings];
  float f0_[kNumStrings];
  float f0_delay_[16];
  int f0_write_ptr_;
  int active_string_;
  unsigned int rng_;
} StringEngine;

#ifdef __cplusplus
extern "C" {
#endif
/* Init and Reset (every delay line cleared). */
void string_engine_init(StringEngine *s);
/* Reseed the engine's generator for a track: 0x21 + track * 0x9e3779b1 (track 0: 0x21, as the oracle). Called by the wrapper after string_engine_init. */
void string_engine_seed(StringEngine *s, int track);
void string_engine_render(StringEngine *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_STRING_ENGINE_H
