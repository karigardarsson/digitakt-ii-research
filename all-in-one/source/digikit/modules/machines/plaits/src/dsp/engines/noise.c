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
// PLAITS for the Digitakt II: Plaits' NoiseEngine in C99 (noise.h).
#include "noise.h"
#include "../lib/drums.h"

#if defined(__ADSP21569__) && !defined(PL_NOISE_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: clocked_render's loop and noise_render's filter loop run in lib/noise_sm.s, bit-identical to the C
   below, which stays for the host and as the -DPL_NOISE_C reference (tests/plaits/test_noise_kernel.py). */
#define PL_NOISE_KERNEL 1
void clocked_kernel(ClockedNoise *c, unsigned int *rng, float *k, float *out, int size);
void noise_filter_kernel(Noise *s, const float *k, float *out, float *aux, const float *temp, int size);
#endif

/* 1 / sqrt(x): the bit-pattern start (0x5f3759df) and three Newton steps y = y (1.5 - 0.5 x y^2) (no division: a
 * relative error of 3.4e-2 after the start, about 2.2 ulp (1.2e-7 relative) after the third step: measured worst 2.18 ulp from the true value over 1.5 million log-uniform points of 2^-40..2^40). The float's bits are read through a
 * pointer (no union: tools/patchkit/pk_cunion.py). */
float pl_rsqrt(float x) {
  float y = x;
  float h = 0.5f * x;
  int *bits = (int *)(void *)&y;
  if (!(x > 0.0f)) return 0.0f;
  *bits = 0x5f3759df - (*bits >> 1);
  y = y * (1.5f - h * y * y);
  y = y * (1.5f - h * y * y);
  y = y * (1.5f - h * y * y);
  return y;
}

static void clocked_init(ClockedNoise *c) {
  c->phase_ = 0.0f;
  c->sample_ = 0.0f;
  c->next_sample_ = 0.0f;
  c->frequency_ = 0.001f;
}

static void clocked_render(ClockedNoise *c, unsigned int *rng, int sync, float frequency, float *out, int size) {
  Interp fm;
  float next_sample = c->next_sample_;
  float sample = c->sample_;
  int i;
  CONSTRAIN(frequency, 0.0f, 1.0f);
  fm = interp(&c->frequency_, frequency, size);
  if (sync) {
    c->phase_ = 1.0f;
  }
#ifdef PL_NOISE_KERNEL
  {
    float k[6];
    k[0] = fm.increment;
    k[1] = 8388608.0f;  /* the bits 0x4B000000: itof_nonneg's bias */
    k[2] = 65536.0f;
    k[3] = 4294967296.0f;
    k[4] = 2.0f;
    (void)next_sample;
    (void)sample;
    clocked_kernel(c, rng, k, out, size);
    return;
  }
#endif
  for (i = 0; i < size; ++i) {
    float this_sample = next_sample;
    float freq, raw_sample, raw_amount;
    next_sample = 0.0f;
    freq = next(&fm);
    raw_sample = rng_float(rng) * 2.0f - 1.0f;
    raw_amount = 4.0f * (freq - 0.25f);
    CONSTRAIN(raw_amount, 0.0f, 1.0f);
    c->phase_ += freq;
    if (c->phase_ >= 1.0f) {
      float t, new_sample, discontinuity;
      c->phase_ -= 1.0f;
      t = c->phase_ / freq;
      new_sample = raw_sample;
      discontinuity = new_sample - sample;
      this_sample += discontinuity * ThisBlepSample(t);
      next_sample += discontinuity * NextBlepSample(t);
      sample = new_sample;
    }
    next_sample += sample;
    out[i] = this_sample + raw_amount * (raw_sample - this_sample);
  }
  commit(&fm);
  c->next_sample_ = next_sample;
  c->sample_ = sample;
}

void noise_init(Noise *s) {
  clocked_init(&s->clocked_noise_[0]);
  clocked_init(&s->clocked_noise_[1]);
  svf_init(&s->lp_hp_filter_);
  svf_init(&s->bp_filter_[0]);
  svf_init(&s->bp_filter_[1]);
  s->previous_f0_ = 0.0f;
  s->previous_f1_ = 0.0f;
  s->previous_q_ = 0.0f;
  s->previous_mode_ = 0.0f;
  s->rng_ = 0x21u;
}

void noise_seed(Noise *s, int track) {
  int k;
  s->rng_ = 0x21u;
  for (k = 0; k < track; ++k) {
    s->rng_ += 0x9e3779b1u;  /* track * 0x9e3779b1 without an integer multiply (the encoding gate refuses one) */
  }
}

void noise_render(Noise *s, const Params *p, float *out, float *aux, int size) {
  float temp[16];
  const float f0 = NoteToFrequency(p->note);
  const float f1 = NoteToFrequency(p->note + p->harmonics * 48.0f - 24.0f);
  const float clock_lowest_note = -24.0f;
  const float clock_f = NoteToFrequency(p->timbre * (128.0f - clock_lowest_note) + clock_lowest_note);
  const float q = 0.5f * SemitonesToRatio(p->morph * 120.0f);
  const int sync = p->trigger & TRIGGER_RISING_EDGE;
  Interp f0_modulation, f1_modulation, q_modulation, mode_modulation;
  int i;
  clocked_render(&s->clocked_noise_[0], &s->rng_, sync, clock_f, aux, size);
  clocked_render(&s->clocked_noise_[1], &s->rng_, sync, clock_f * f1 / f0, temp, size);

  f0_modulation = interp(&s->previous_f0_, f0, size);
  f1_modulation = interp(&s->previous_f1_, f1, size);
  q_modulation = interp(&s->previous_q_, q, size);
  mode_modulation = interp(&s->previous_mode_, p->harmonics, size);

#ifdef PL_NOISE_KERNEL
  {
    float k[4];
    k[0] = f0_modulation.increment;
    k[1] = f1_modulation.increment;
    k[2] = q_modulation.increment;
    k[3] = mode_modulation.increment;
    noise_filter_kernel(s, k, out, aux, temp, size);
    return;
  }
#endif
  for (i = 0; i < size; ++i) {
    const float g_f0 = next(&f0_modulation);
    const float g_f1 = next(&f1_modulation);
    const float g_q = next(&q_modulation);
    const float gain = pl_rsqrt((0.5f + g_q) * 40.0f * g_f0);
    float input_1, input_2, mode, hp_gain, bp_gain, lp_gain, hp, bp, lp;
    Svf *f = &s->lp_hp_filter_;
    svf_set_g_q(&s->lp_hp_filter_, tan_accurate(g_f0), g_q);
    svf_set_g_q(&s->bp_filter_[0], tan_accurate(g_f0), g_q);
    svf_set_g_q(&s->bp_filter_[1], tan_accurate(g_f1), g_q);

    input_1 = aux[i] * gain;
    input_2 = temp[i] * gain;
    /* ProcessMultimodeLPtoHP over one sample */
    mode = next(&mode_modulation);
    hp_gain = minf(-mode * 2.0f + 1.0f, 0.0f);
    bp_gain = 1.0f - 2.0f * absf(mode - 0.5f);
    lp_gain = maxf(1.0f - mode * 2.0f, 0.0f);
    svf_step(f, input_1, &lp, &bp, &hp);
    out[i] = hp_gain * hp + bp_gain * bp + lp_gain * lp;
    {
      float b0, b1, l, h;
      svf_step(&s->bp_filter_[0], input_1, &l, &b0, &h);
      svf_step(&s->bp_filter_[1], input_2, &l, &b1, &h);
      aux[i] = b0 + b1;
    }
  }
  commit(&f0_modulation);
  commit(&f1_modulation);
  commit(&q_modulation);
  commit(&mode_modulation);
}
