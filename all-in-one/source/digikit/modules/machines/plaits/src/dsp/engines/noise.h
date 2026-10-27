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
// PLAITS for the Digitakt II: Plaits' NoiseEngine (model NOISE: two clocked noise sources through a multimode filter
// and two band-pass filters; HARMONICS the second filter's offset and the LP to HP mode, TIMBRE the clock frequency,
// MORPH the resonance) in C99, from plaits/dsp/engine/noise_engine.{h,cc} and plaits/dsp/noise/clocked_noise.h.
// Not a strike engine: it runs on between notes. The trigger's RISING_EDGE (the wrapper's note-on) syncs the two
// clocked noises as upstream's `sync`; the TRIGGER_UNPATCHED lowest clock note (0 instead of -24) is not ported.
// Random: stmlib's global generator is one state per engine here (rng_, 0x21 at init as at Plaits' power-up, then
// 0x21 + track * 0x9e3779b1 by noise_seed so that tracks differ; track 0 is upstream's; the
// oracle seeds 0x21 when it selects the engine); clocked noise 0 draws its whole block first, then noise 1, as upstream.
// The one deliberate difference: the per-sample gain 1 / Sqrt(x) uses a division-free Newton reciprocal square root
// (noise.c pl_rsqrt; the core has no square root), also patched into the oracle's text.
// The temporary buffer is on the stack.
#ifndef PLAITS_NOISE_H
#define PLAITS_NOISE_H
#include "engine.h"
#include "../lib/svf.h"

typedef struct { float phase_, sample_, next_sample_, frequency_; } ClockedNoise;

typedef struct {
  ClockedNoise clocked_noise_[2];
  Svf lp_hp_filter_;
  Svf bp_filter_[2];
  float previous_f0_, previous_f1_, previous_q_, previous_mode_;
  unsigned int rng_;
} Noise;

#ifdef __cplusplus
extern "C" {
#endif
void noise_init(Noise *s);
/* Reseed the engine's generator for a track: 0x21 + track * 0x9e3779b1 (track 0: 0x21, upstream's power-up state, as the
 * oracle), so two NOISE tracks do not render the same noise. Called by the wrapper after noise_init. */
void noise_seed(Noise *s, int track);
void noise_render(Noise *s, const Params *p, float *out, float *aux, int size);
/* 1 / sqrt(x) for x > 0 by Newton's iteration (about 2.2 ulp), 0 for x <= 0 (upstream gives inf or NaN there; the engine's
 * argument is always positive). Exported for the tests; the oracle patches in the same algorithm. */
float pl_rsqrt(float x);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_NOISE_H
