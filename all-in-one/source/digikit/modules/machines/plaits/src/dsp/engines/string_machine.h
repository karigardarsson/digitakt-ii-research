// Copyright 2021 Emilie Gillet.
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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' StringMachineEngine (model STRING MACHINE: a four-note chord from the chord bank
// on divide-down string/organ voices, HARMONICS the chord, MORPH the registration (saw to square), TIMBRE the filter
// cutoff and the ensemble's amount and depth; AUX the other channel) in C99, translated from
// plaits/dsp/engine2/string_machine_engine.{h,cc}. Per voice state: four StringSynthOscillators, the ChordBank, a pair
// of low-pass filters (arrays of two, lanes 0 and 1: the left and right channel), and the Ensemble with its 1024-float
// delay buffer.

#ifndef PLAITS_STRING_MACHINE_H
#define PLAITS_STRING_MACHINE_H
#include "engine.h"
#include "../lib/chord_bank.h"
#include "../lib/ensemble.h"
#include "../lib/string_synth_oscillator.h"
typedef struct {
  StringSynthOscillator divide_down_voice_[kChordNumNotes];
  /* stmlib::NaiveSvf svf_[2] as a pair: lane 0 filters OUT (even notes), lane 1 AUX (odd notes). */
  float svf_f_[2];
  float svf_lp_[2];
  float svf_bp_[2];
  ChordBank chords_;
  float morph_lp_;
  float timbre_lp_;
  Ensemble ensemble_;
} StringMachine;
#ifdef __cplusplus
extern "C" {
#endif
/* Init and Reset (upstream calls Reset when the engine is selected). */
void string_machine_init(StringMachine *s);
// out and aux receive size samples each.
void string_machine_render(StringMachine *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_STRING_MACHINE_H
