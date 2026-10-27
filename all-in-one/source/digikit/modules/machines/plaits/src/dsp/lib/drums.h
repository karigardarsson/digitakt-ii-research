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
// PLAITS for the Digitakt II: what the three drum engines (KICK, SNARE, HIHAT) share, in C99 (static: Selache's C has
// no inlining). From stmlib/dsp/filter.h (OnePole and Svf with the FAST and ACCURATE tangent approximations, single
// sample and block processing), stmlib/dsp/dsp.h (SLOPE, SoftLimit, SoftClip, Sqrt), stmlib/utils/random.h (Random)
// and plaits/dsp/fx/overdrive.h.
//   - tan constants: `a = 3.260e-01f * M_PI_POW_3` etc. are evaluated in double (the macros multiply left to right)
//     and rounded to float; the values below are those floats.
//   - Random: stmlib's global state is one per engine here (0x21 at init as at Plaits' power-up; the oracle seeds 0x21
//     when it selects the engine). The two sub-drums of an engine draw from it in upstream's order.
//   - Sqrt: upstream's is the hardware vsqrt (a correctly rounded result). Selache has no square root: pl_sqrt is
//     Newton's iteration (from the exponent halved) on both the host and the core, within a few ulp of it, and the
//     oracle's build of the snare calls the same function (tests/support/plaits_oracle.py). Only the SNARE's
//     two per-block level constants use it.
#ifndef PLAITS_LIB_DRUMS_H
#define PLAITS_LIB_DRUMS_H
#include "units.h"
#include "svf.h"

#define TRIGGER_RISING_EDGE 1

#define SLOPE(out, in, positive, negative) { \
  float error = (in) - out; \
  out += (error > 0.0f ? positive : negative) * error; \
}

static unsigned int rng_word(unsigned int *state) {
  unsigned int x = *state;
  /* 1664525 = 2^20 + 2^19 + 2^16 + 2^14 + 2^13 + 2^10 + 2^9 + 2^3 + 2^2 + 1 */
  unsigned int y = (x << 20) + (x << 19) + (x << 16) + (x << 14) + (x << 13) + (x << 10) + (x << 9) + (x << 3) +
                   (x << 2) + x;
  *state = y + 1013904223u;
  return *state;
}

/* Random::GetFloat: static_cast<float>(word) / 2^32. The word is split in two exact 16-bit halves, so the one
 * addition that rounds is the rounding of static_cast<float>(uint32_t) (to nearest even). */
static float rng_float(unsigned int *state) {
  unsigned int w = rng_word(state);
  float hi = itof_nonneg((int)(w >> 16));
  float lo = itof_nonneg((int)(w & 0xFFFFu));
  return (hi * 65536.0f + lo) / 4294967296.0f;
}

static float pl_sqrt(float x) {
  float y = x;
  int *bits = (int *)(void *)&y;  /* the float's bits through a pointer (no union: tools/patchkit/pk_cunion.py) */
  if (!(x > 0.0f)) return 0.0f;
  *bits = (*bits >> 1) + 0x1fbd1df5;
  y = 0.5f * (y + x / y);
  y = 0.5f * (y + x / y);
  y = 0.5f * (y + x / y);
  y = 0.5f * (y + x / y);
  return y;
}

static float SoftLimit(float x) { return x * (27.0f + x * x) / (27.0f + 9.0f * x * x); }
static float SoftClip(float x) {
  if (x < -3.0f) return -1.0f;
  if (x > 3.0f) return 1.0f;
  return SoftLimit(x);
}

/* OnePole::tan<FREQUENCY_FAST / FREQUENCY_ACCURATE / FREQUENCY_DIRTY> */
static float tan_fast(float f) {
  const float a = 10.1080465f;
  const float b = 55.7873878f;
  float f2 = f * f;
  return f * (3.14159265358979323846f + f2 * (a + b * f2));
}
static float tan_accurate(float f) {
  const float a = 10.3353662f;
  const float b = 40.8206978f;
  const float c = 161.205307f;
  const float d = 86.4620361f;
  const float e = 2799.88354f;
  float f2 = f * f;
  return f * (3.14159265358979323846f + f2 * (a + f2 * (b + f2 * (c + f2 * (d + f2 * e)))));
}
static float tan_dirty(float f) { return f * (3.14159265358979323846f + 11.5839453f * f * f); }

/* Svf::set_f_q with the tangent g given (and the resonance in true units). */
static void svf_set_g_q(Svf *s, float g, float resonance) {
  s->g_ = g;
  s->r_ = 1.0f / resonance;
  s->h_ = 1.0f / (1.0f + s->r_ * s->g_ + s->g_ * s->g_);
}

/* Svf::Process for one sample: the three outputs (the callers use the ones they need). */
static void svf_step(Svf *s, float in, float *lp, float *bp, float *hp) {
  float h = (in - s->r_ * s->state_1_ - s->g_ * s->state_1_ - s->state_2_) * s->h_;
  float b = s->g_ * h + s->state_1_;
  s->state_1_ = s->g_ * h + b;
  float l = s->g_ * b + s->state_2_;
  s->state_2_ = s->g_ * b + l;
  *lp = l;
  *bp = b;
  *hp = h;
}

/* Process<FILTER_MODE_HIGH_PASS>(in, out, size); in may be out. */
static void svf_high_pass(Svf *s, const float *in, float *out, int size) {
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
    out[i] = hp;
  }
  s->state_1_ = state_1;
  s->state_2_ = state_2;
}

/* OnePole::set_f<FREQUENCY_FAST> and Process<FILTER_MODE_LOW_PASS>. */
static void one_pole_set_f_fast(OnePole *s, float f) {
  s->g_ = tan_fast(f);
  s->gi_ = 1.0f / (1.0f + s->g_);
}
static float one_pole_low_pass(OnePole *s, float in) {
  float lp = (s->g_ * in + s->state_) * s->gi_;
  s->state_ = s->g_ * (in - lp) + lp;
  return lp;
}
#endif  // PLAITS_LIB_DRUMS_H
