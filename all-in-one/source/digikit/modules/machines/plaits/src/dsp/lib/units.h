// Copyright 2014-2017 Emilie Gillet.
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
// PLAITS for the Digitakt II: the upstream helpers the 2FM, FORMANT and ADDITIVE engines share, in C99 (static: Selache's
// C has no inlining). From stmlib/dsp/parameter_interpolator.h (ParameterInterpolator), stmlib/dsp/units.h
// (SemitonesToRatio), stmlib/dsp/dsp.h (Interpolate, ONE_POLE), stmlib/dsp/filter.h (OnePole with
// FREQUENCY_DIRTY), stmlib/dsp/polyblep.h and plaits/dsp/engine/engine.h (NoteToFrequency, with a0 from exactly
// 48,000 Hz: (440 / 8) / 48000, as plaits/dsp/dsp.h with kCorrectedSampleRate = 48000). The forms follow the
// dt2-machine-lab translations (MIT, the notice above); the tables are the shared ones of lib/tables.h.

#ifndef PLAITS_LIB_UNITS_H
#define PLAITS_LIB_UNITS_H
#include "../dsp_util.h"
#include "tables.h"
#include "one_pole.h"

#define ONE_POLE(out, in, coefficient) out += (coefficient) * ((in) - out);

static const float kMaxFrequency = 0.25f;

/* stmlib::ParameterInterpolator: `commit` is its destructor (writes the value back to the state). */
typedef struct { float *state; float value; float increment; } Interp;
/* itof_nonneg(size) written out (selcc has no inlining: the same operations without its call and frame). */
static Interp interp(float *s, float target, int size) {
  Interp p;
  union { int i; float f; } u;
  float d = target - *s;
  u.i = 0x4B000000 | size;
  p.state = s; p.value = *s; p.increment = d / (*(float *)(void *)&u - 8388608.0f); return p;
}
static float next(Interp *p) { p->value += p->increment; return p->value; }
static float subsample(Interp *p, float t) { return p->value + p->increment * t; }
static void commit(Interp *p) { *p->state = p->value; }

static float SemitonesToRatio(float semitones) {
  float pitch = semitones + 128.0f;
  MAKE_INTEGRAL_FRACTIONAL(pitch)
  return PL_F(pl_lut_pitch_ratio_high)[pitch_integral] *
      PL_F(pl_lut_pitch_ratio_low)[(int)(pitch_fractional * 256.0f)];
}

static float NoteToFrequency(float note) {
  note -= 9.0f;
  CONSTRAIN(note, -128.0f, 127.0f);
  return ((440.0f / 8.0f) / 48000.0f) * 0.25f * SemitonesToRatio(note);
}

static float Interpolate(const float *table, float index, float size) {
  index *= size;
  MAKE_INTEGRAL_FRACTIONAL(index)
  float a = table[index_integral];
  float b = table[index_integral + 1];
  return a + (b - a) * index_fractional;
}

static float ThisBlepSample(float t) { return 0.5f * t * t; }
static float NextBlepSample(float t) { t = 1.0f - t; return -0.5f * t * t; }

/* stmlib::OnePole, set_f<FREQUENCY_DIRTY> and Process<FILTER_MODE_HIGH_PASS>. a = 3.736e-01f * M_PI^3 evaluated in
 * double and rounded to float (11.583945274353027f); M_PI_F = (float)M_PI. */
static void one_pole_set_f(OnePole *s, float f) {
  const float a = 11.583945274353027f;
  s->g_ = f * (3.14159265358979323846f + a * f * f);
  s->gi_ = 1.0f / (1.0f + s->g_);
}
static void one_pole_init(OnePole *s) { one_pole_set_f(s, 0.01f); s->state_ = 0.0f; }
static float one_pole_high_pass(OnePole *s, float in) {
  float lp = (s->g_ * in + s->state_) * s->gi_;
  s->state_ = s->g_ * (in - lp) + lp;
  return in - lp;
}

#endif  // PLAITS_LIB_UNITS_H
