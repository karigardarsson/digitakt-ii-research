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
// PLAITS for the Digitakt II: Plaits' HiHatEngine (model HIHAT: a 808-style hi-hat of six square oscillators through
// a swing VCA on OUT and a ring-modulated one on AUX; HARMONICS the noisiness, TIMBRE the tone, MORPH the decay) in
// C99, from plaits/dsp/engine/hi_hat_engine.cc and plaits/dsp/drums/hi_hat.h (HiHat<SquareNoise, SwingVCA, true,
// false> and HiHat<RingModNoise, LinearVCA, false, true>: `kind` 0 and 1 here) with the SQUARE and SAW shapes of
// plaits/dsp/oscillator/oscillator.h that RingModNoise uses. The trigger is Plaits' RISING_EDGE (the wrapper's
// note-on); the sustain branch (trigger UNPATCHED) is not ported; the temporary buffer is on the stack.
#ifndef PLAITS_HIHAT_H
#define PLAITS_HIHAT_H
#include "engine.h"
#include "../lib/svf.h"

typedef struct {
  float phase_, next_sample_, lp_state_, hp_state_;
  int high_;
  float frequency_, pw_;
  float pad_;  /* 8 words: indexing the six oscillators is a shift (selcc multiplies by 7 with an instruction the gate refuses) */
} HatOscillator;

/* HiHat without its metallic noise source: SquareNoise's phases (hi_hat_1_) and RingModNoise's oscillators (hi_hat_2_)
 * are in the engine, one source each, so no voice carries the other kind's unused state. */
typedef struct {
  float envelope_, noise_clock_, noise_sample_;
  Svf noise_coloration_svf_, hpf_;
} HiHat;

typedef struct {
  HiHat hi_hat_1_, hi_hat_2_;
  unsigned int square_phase_[6];     /* SquareNoise::phase_ of hi_hat_1_ */
  HatOscillator oscillator_[6];      /* RingModNoise::oscillator_ of hi_hat_2_ */
  unsigned int rng_;
} HiHatEngine;

#ifdef __cplusplus
extern "C" {
#endif
void hihat_init(HiHatEngine *s);
void hihat_render(HiHatEngine *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_HIHAT_H
