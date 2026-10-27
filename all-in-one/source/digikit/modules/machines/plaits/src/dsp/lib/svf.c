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
// PLAITS for the Digitakt II: stmlib's Svf (stmlib/dsp/filter.h) in C99, the parts the SPEECH model uses (lib/svf.h).
// OnePole::tan<FREQUENCY_DIRTY>(f) = f * (M_PI_F + a * f * f), a = 3.736e-01f * M_PI^3 evaluated in double and
// rounded to float (11.583945274353027f, as lib/units.h); M_PI_F = (float)M_PI.
#include "svf.h"

void svf_set_f_r(Svf *s, float f, float r) {
  const float a = 11.583945274353027f;
  s->g_ = f * (3.14159265358979323846f + a * f * f);
  s->r_ = r;
  s->h_ = 1.0f / (1.0f + s->r_ * s->g_ + s->g_ * s->g_);
}

void svf_init(Svf *s) {
  svf_set_f_r(s, 0.01f, 0.01f);  /* resonance 100: 1.0f / 100.0f rounds to 0.01f */
  s->state_1_ = 0.0f;
  s->state_2_ = 0.0f;
}

#if defined(__ADSP21569__) && !defined(PL_SVF_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: both band-pass loops run in lib/svf_sm.s, bit-identical to the C loops below, which stay for the host
   and as the -DPL_SVF_C reference (tests/plaits/test_svf_kernel.py). */
#define PL_SVF_KERNEL 1
void svf_bp_kernel(Svf *s, const float *in, float *out, int size, float *gain);
#endif

void svf_band_pass(Svf *s, const float *in, float *out, int size) {
#ifdef PL_SVF_KERNEL
  svf_bp_kernel(s, in, out, size, 0);
}
static void svf_band_pass_c(Svf *s, const float *in, float *out, int size) {
#endif
  float g = s->g_, r = s->r_, h = s->h_;
  float state_1 = s->state_1_;
  float state_2 = s->state_2_;
  float hp, bp, lp;
  int i;
  for (i = 0; i < size; ++i) {
    hp = (in[i] - r * state_1 - g * state_1 - state_2) * h;
    bp = g * hp + state_1;
    state_1 = g * hp + bp;
    lp = g * bp + state_2;
    state_2 = g * bp + lp;
    out[i] = bp;
  }
  s->state_1_ = state_1;
  s->state_2_ = state_2;
}

void svf_band_pass_add(Svf *s, const float *in, float *out, int size, float gain) {
#ifdef PL_SVF_KERNEL
  svf_bp_kernel(s, in, out, size, &gain);
}
static void svf_band_pass_add_c(Svf *s, const float *in, float *out, int size, float gain) {
#endif
  float g = s->g_, r = s->r_, h = s->h_;
  float state_1 = s->state_1_;
  float state_2 = s->state_2_;
  float hp, bp, lp;
  int i;
  for (i = 0; i < size; ++i) {
    hp = (in[i] - r * state_1 - g * state_1 - state_2) * h;
    bp = g * hp + state_1;
    state_1 = g * hp + bp;
    lp = g * bp + state_2;
    state_2 = g * bp + lp;
    out[i] += gain * bp;
  }
  s->state_1_ = state_1;
  s->state_2_ = state_2;
}
