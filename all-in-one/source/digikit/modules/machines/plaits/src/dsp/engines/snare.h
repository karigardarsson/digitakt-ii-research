// Copyright 2014 Emilie Gillet.
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
// PLAITS for the Digitakt II: Plaits' SnareDrumEngine (model SNARE: an analog 808-style snare on OUT and a
// synthetic 909-style one on AUX; HARMONICS the snappy amount (and the synthetic one's FM), TIMBRE the tone, MORPH the
// decay) in C99, from plaits/dsp/engine/snare_drum_engine.cc and plaits/dsp/drums/{analog,synthetic}_snare_drum.h.
// The trigger is Plaits' RISING_EDGE (the wrapper's note-on); the sustain branches (trigger UNPATCHED) are not ported.
// Upstream passes the same four values to both drums, each drum's Render naming them as it likes: TIMBRE is the
// analog one's tone and the synthetic one's fm_amount, HARMONICS the snappy of both.
#ifndef PLAITS_SNARE_H
#define PLAITS_SNARE_H
#include "engine.h"
#include "../lib/svf.h"
#include "../lib/one_pole.h"

#define kSnareNumModes 5

typedef struct {
  int pulse_remaining_samples_;
  float pulse_, pulse_height_, pulse_lp_, noise_envelope_;
  Svf resonator_[kSnareNumModes];
  Svf noise_filter_;
} AnalogSnareDrum;

typedef struct {
  float phase_[2], drum_amplitude_, snare_amplitude_, fm_;
  int hold_counter_;
  OnePole drum_lp_, snare_hp_;
  Svf snare_lp_;
} SyntheticSnareDrum;

typedef struct {
  AnalogSnareDrum analog_;
  SyntheticSnareDrum synthetic_;
  unsigned int rng_;
} Snare;

#ifdef __cplusplus
extern "C" {
#endif
void snare_init(Snare *s);
void snare_render(Snare *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_SNARE_H
