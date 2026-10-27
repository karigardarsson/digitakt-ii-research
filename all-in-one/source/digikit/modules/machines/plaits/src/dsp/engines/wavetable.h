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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' WavetableEngine (model WAVETABLE: 4 banks of 8 x 8 waves, TIMBRE the column,
// MORPH the row, HARMONICS the bank, interpolated in three dimensions; AUX is OUT quantised to 5 bits) in C99,
// translated from plaits/dsp/engine/wavetable_engine.{h,cc} and plaits/dsp/oscillator/wavetable_oscillator.h.

#ifndef PLAITS_WAVETABLE_H
#define PLAITS_WAVETABLE_H
#include "engine.h"
#include "../lib/wavetable_oscillator.h"
typedef struct {
  float phase_;
  float x_pre_lp_, y_pre_lp_, z_pre_lp_;
  float x_lp_, y_lp_, z_lp_;
  float previous_x_, previous_y_, previous_z_;
  float previous_f0_;
  Differentiator diff_out_;
  /* wave_map_ is the constant pl_wavetable_map (lib/waves.h): LoadUserData(NULL) */
} Wavetable;
#ifdef __cplusplus
extern "C" {
#endif
void wavetable_init(Wavetable *s);
// out and aux receive size samples each.
void wavetable_render(Wavetable *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_WAVETABLE_H
