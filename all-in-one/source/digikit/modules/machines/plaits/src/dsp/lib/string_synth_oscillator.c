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
// PLAITS for the Digitakt II: Plaits' StringSynthOscillator in C99 (lib/string_synth_oscillator.h). The five
// interpolators and the polyBLEP are written out in the loop (Selache's C has no inlining), with the same float
// operations in the same order as upstream; int-to-float conversions of the (non-negative) segment go through
// dsp_util.h `itof_nonneg`.
#include "string_synth_oscillator.h"
#include "units.h"

#if defined(__ADSP21569__) && !defined(PL_SS_C)
/* The DSP build: the sample loop runs in lib/string_synth_sm.s, bit-identical to the C loop below, which stays for the
   host and as the -DPL_SS_C reference (tests/plaits/test_string_synth_kernel.py). */
#define PL_SS_KERNEL 1
void string_synth_kernel(float *st, const float *k, float *out, int size);
#endif

void string_synth_oscillator_init(StringSynthOscillator *s) {
  s->phase_ = 0.0f;
  s->next_sample_ = 0.0f;
  s->segment_ = 0;

  s->frequency_ = 0.001f;
  s->saw_8_gain_ = 0.0f;
  s->saw_4_gain_ = 0.0f;
  s->saw_2_gain_ = 0.0f;
  s->saw_1_gain_ = 0.0f;
}

void string_synth_oscillator_render(StringSynthOscillator *s, float frequency, const float *unshifted_registration,
                                    float gain, float *out, int size) {
  float registration[7];
  Interp fm, saw_8_gain_modulation, saw_4_gain_modulation, saw_2_gain_modulation, saw_1_gain_modulation;
  float phase, next_sample;
  int segment, shift, i;

  frequency *= 8.0f;

  // Deal with very high frequencies by shifting everything 1 or 2 octave
  // down: Instead of playing the 1st harmonic of a 8kHz wave, we play the
  // second harmonic of a 4kHz wave.
  shift = 0;
  while (frequency > 0.5f) {
    shift += 2;
    frequency *= 0.5f;
  }
  // Frequency is just too high.
  if (shift >= 8) {
    return;
  }

  for (i = 0; i < shift; ++i) {
    registration[i] = 0.0f;
  }
  for (i = shift; i < 7; ++i) {
    registration[i] = unshifted_registration[i - shift];
  }

  fm = interp(&s->frequency_, frequency, size);
  saw_8_gain_modulation = interp(
      &s->saw_8_gain_,
      (registration[0] + 2.0f * registration[1]) * gain,
      size);
  saw_4_gain_modulation = interp(
      &s->saw_4_gain_,
      (registration[2] - registration[1] + 2.0f * registration[3]) * gain,
      size);
  saw_2_gain_modulation = interp(
      &s->saw_2_gain_,
      (registration[4] - registration[3] + 2.0f * registration[5]) * gain,
      size);
  saw_1_gain_modulation = interp(
      &s->saw_1_gain_,
      (registration[6] - registration[5]) * gain,
      size);

#ifdef PL_SS_KERNEL
  {
    float st[8], k[14];
    st[0] = fm.value;
    st[1] = saw_8_gain_modulation.value;
    st[2] = saw_4_gain_modulation.value;
    st[3] = saw_2_gain_modulation.value;
    st[4] = saw_1_gain_modulation.value;
    st[5] = s->phase_;
    st[6] = s->next_sample_;
    *(int *)(void *)&st[7] = s->segment_;
    k[0] = fm.increment;
    k[1] = saw_8_gain_modulation.increment;
    k[2] = saw_4_gain_modulation.increment;
    k[3] = saw_2_gain_modulation.increment;
    k[4] = saw_1_gain_modulation.increment;
    k[5] = 8.0f;
    k[6] = 2.0f;
    k[7] = 1.0f;
    k[8] = 0.5f;
    k[9] = -0.5f;
    k[10] = 4.0f;
    k[11] = 0.125f;
    k[12] = 0.25f;
    k[13] = 8388608.0f;
    string_synth_kernel(st, k, out, size);
    fm.value = st[0];
    saw_8_gain_modulation.value = st[1];
    saw_4_gain_modulation.value = st[2];
    saw_2_gain_modulation.value = st[3];
    saw_1_gain_modulation.value = st[4];
    phase = st[5];
    next_sample = st[6];
    segment = *(int *)(void *)&st[7];
  }
#else
  phase = s->phase_;
  next_sample = s->next_sample_;
  segment = s->segment_;
  while (size--) {
    float this_sample = next_sample;
    float f, saw_8_gain, saw_4_gain, saw_2_gain, saw_1_gain;
    int next_segment;
    next_sample = 0.0f;

    fm.value += fm.increment;  /* the five Next() calls */
    f = fm.value;
    saw_8_gain_modulation.value += saw_8_gain_modulation.increment;
    saw_8_gain = saw_8_gain_modulation.value;
    saw_4_gain_modulation.value += saw_4_gain_modulation.increment;
    saw_4_gain = saw_4_gain_modulation.value;
    saw_2_gain_modulation.value += saw_2_gain_modulation.increment;
    saw_2_gain = saw_2_gain_modulation.value;
    saw_1_gain_modulation.value += saw_1_gain_modulation.increment;
    saw_1_gain = saw_1_gain_modulation.value;

    phase += f;
    next_segment = (int)phase;
    if (next_segment != segment) {
      float discontinuity = 0.0f;
      if (next_segment == 8) {
        phase -= 8.0f;
        next_segment -= 8;
        discontinuity -= saw_8_gain;
      }
      if ((next_segment & 3) == 0) {
        discontinuity -= saw_4_gain;
      }
      if ((next_segment & 1) == 0) {
        discontinuity -= saw_2_gain;
      }
      discontinuity -= saw_1_gain;
      if (discontinuity != 0.0f) {
        float fraction = phase - itof_nonneg(next_segment);
        float t = fraction / f;
        float u = 1.0f - t;
        this_sample += 0.5f * t * t * discontinuity;    /* ThisBlepSample(t) * discontinuity */
        next_sample += -0.5f * u * u * discontinuity;   /* NextBlepSample(t) * discontinuity */
      }
    }
    segment = next_segment;

    next_sample += (phase - 4.0f) * saw_8_gain * 0.125f;
    next_sample += (phase - itof_nonneg(segment & 4) - 2.0f) * saw_4_gain * 0.25f;
    next_sample += (phase - itof_nonneg(segment & 6) - 1.0f) * saw_2_gain * 0.5f;
    next_sample += (phase - itof_nonneg(segment & 7) - 0.5f) * saw_1_gain;
    *out++ += 2.0f * this_sample;
  }
#endif
  s->next_sample_ = next_sample;
  s->phase_ = phase;
  s->segment_ = segment;
  commit(&fm);
  commit(&saw_8_gain_modulation);
  commit(&saw_4_gain_modulation);
  commit(&saw_2_gain_modulation);
  commit(&saw_1_gain_modulation);
}
