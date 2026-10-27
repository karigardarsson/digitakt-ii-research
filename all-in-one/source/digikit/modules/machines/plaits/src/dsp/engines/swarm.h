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
// PLAITS for the Digitakt II: Plaits' SwarmEngine (model SWARM: eight grains per voice, each a band-limited sawtooth
// (OUT) plus a sine (AUX) of one random frequency that glides from the last random frequency to the next, under its
// own random-length grain envelope; HARMONICS the spread of the grains' pitches, TIMBRE the grain rate, MORPH the
// grain size: short grains, a cloud, to long overlapping ones, a swarm of glissandi) in C99, from
// plaits/dsp/engine/swarm_engine.{h,cc}, plaits/dsp/oscillator/sine_oscillator.h (FastSineOscillator, additive mode)
// and stmlib/utils/random.h. Not a strike engine: it runs on between notes. Both of upstream's modes are ported:
// `burst_mode` is `!(trigger & TRIGGER_UNPATCHED)` and `start_burst` the trigger's RISING_EDGE (every grain restarts at
// phase 0.5 with its rate x 16 and draws a new frequency; each grain then slows by 0.8 to 1.0). OWNER DECISION
// (2026-10-22), a documented exception to the Digitakt trigger convention (plaits.c): the wrapper gives model 8
// TRIGGER_UNPATCHED alone on every block, so the engine runs in the continuous mode (every grain draws a fresh rate of
// 0.5 to 2.0, the swarm never slows or restarts at a note-on) and the Digitakt's amp envelope shapes the note. The burst
// path stays in the C and is tested against the oracle, but the wrapper never reaches it.
// Random: stmlib's global generator is one state per engine here (rng_, 0x21 at init, then 0x21 + track *
// 0x9e3779b1 by swarm_seed so that tracks differ; track 0 is upstream's; the oracle seeds 0x21 when it selects the
// engine); the eight grains draw in order, grain by grain, as upstream (one Step per grain, then its render).
// Layout (PORTING.md section 5): the eight grains are the lanes of arrays of eight floats (4 pairs), each array 8-byte
// aligned and contiguous; the sine oscillators are FastSineOscillator's magic circle with its ParameterInterpolators
// as lanes too. The per-grain rank is a const table (-1, -2/3, -1/3, 0, 1/3, 2/3, 1, 4/3: upstream's `n` is the
// integer 3, `(kNumSwarmVoices - 1) / 2`). No arena: the state is 8 x 15 floats and the generator, 484 bytes.
// Deliberate differences: none in the arithmetic. Float to int of the grain phase uses itof_nonneg (exact below 2^23).
#ifndef PLAITS_SWARM_H
#define PLAITS_SWARM_H
#include "engine.h"

#define kSwarmGrains 8

typedef struct {
  /* GrainEnvelope */
  float from_[kSwarmGrains], interval_[kSwarmGrains], phase_[kSwarmGrains], fm_[kSwarmGrains];
  float amplitude_[kSwarmGrains], previous_size_ratio_[kSwarmGrains], filter_coefficient_[kSwarmGrains];
  /* AdditiveSawOscillator */
  float saw_phase_[kSwarmGrains], saw_next_sample_[kSwarmGrains], saw_frequency_[kSwarmGrains],
      saw_gain_[kSwarmGrains];
  /* FastSineOscillator */
  float sine_x_[kSwarmGrains], sine_y_[kSwarmGrains], sine_epsilon_[kSwarmGrains], sine_amplitude_[kSwarmGrains];
  unsigned int rng_;
} Swarm;

#ifdef __cplusplus
extern "C" {
#endif
/* Init and Reset (the ranks are a table). */
void swarm_init(Swarm *s);
/* Reseed the engine's generator for a track: 0x21 + track * 0x9e3779b1 (track 0: 0x21, as the oracle). Called by the wrapper after swarm_init. */
void swarm_seed(Swarm *s, int track);
void swarm_render(Swarm *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_SWARM_H
