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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: the PHASE-DIST engine (Plaits PhaseDistortionEngine, model 17) in C99, from
// plaits/dsp/engine2/phase_distortion_engine.{h,cc} and plaits/dsp/oscillator/variable_shape_oscillator.h (the
// <enable_sync, output_phase> = <true, true> and <false, true> instantiations). Not a strike engine: it runs on between
// notes and reads neither the trigger nor the accent. HARMONICS sets the modulator's frequency ratio (quantized), TIMBRE
// the modulation amount, MORPH the asymmetry (pulse width); OUT is the synced pair, AUX the free-running one.
// The temporary buffer (kMaxBlockSize * 4 floats of the arena upstream) is on the stack: size <= 16.
#ifndef PLAITS_PHASE_DIST_H
#define PLAITS_PHASE_DIST_H
#include "engine.h"

typedef struct {
  float master_phase_, slave_phase_, next_sample_, previous_pw_;
  int high_;
  float master_frequency_, slave_frequency_, pw_, waveshape_, phase_modulation_;
} PdOsc;

typedef struct { PdOsc shaper_, modulator_; } PhaseDist;

#ifdef __cplusplus
extern "C" {
#endif
void phase_dist_init(PhaseDist *s);
void phase_dist_render(PhaseDist *s, const Params *p, float *out, float *aux, int size);
/* Sine with a negative phase wrapped (exported for the tests). */
float phase_dist_sine(float phase);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_PHASE_DIST_H
