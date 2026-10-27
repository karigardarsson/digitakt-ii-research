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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' ResonatorSvf and Resonator in C99, from plaits/dsp/physical_modelling/
// resonator.{h,cc}, with stmlib's OnePole::tan<FREQUENCY_FAST> (stmlib/dsp/filter.h), CosineOscillator in
// COSINE_OSCILLATOR_APPROXIMATE mode (stmlib/dsp/cosine_oscillator.h) and Interpolate (stmlib/dsp/dsp.h).
// Differences from upstream, none of which changes a result on the host:
//   - tan<FREQUENCY_FAST>'s constants 3.260e-01f * M_PI^3 and 1.823e-01f * M_PI^5 (evaluated in double, left to right,
//     then rounded to float) are written as the floats they round to: 10.1080465f (0x4121ba8f) and 55.7873878f
//     (0x425f2649); M_PI_F is 3.14159274f (0x40490fdb);
//   - the batch loops of ResonatorSvf<4> keep upstream's order of operations (s_out starts at 0.0f and adds the four
//     modes in order).
// Index ranges: lut_stiffness (65 entries) is read at structure * 64 with structure in [0, 1): entries 0..64 (the
// HARMONICS one-pole of engines/modal.c stays below 1.0 in float); mode_amplitude_ and mode_filters_ are indexed
// below resolution_ = 24.
#include "resonator.h"
#include "units.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

/* stmlib::OnePole::tan<FREQUENCY_FAST>. */
static float tan_fast(float f) {
  const float a = 10.1080465f;
  const float b = 55.7873878f;
  float f2 = f * f;
  return f * (3.14159274f + f2 * (a + b * f2));
}

void resonator_svf1_init(ResonatorSvf1 *s) {
  s->state_1_ = 0.0f;
  s->state_2_ = 0.0f;
}

void resonator_svf1_low_pass(ResonatorSvf1 *s, float f, float q, float gain, const float *in, float *out, int size) {
  float g = tan_fast(f);
  float r = 1.0f / q;
  float h = 1.0f / (1.0f + r * g + g * g);
  float r_plus_g = r + g;
  float state_1 = s->state_1_;
  float state_2 = s->state_2_;
  int n;
  for (n = 0; n < size; ++n) {
    float s_in = in[n];
    float s_out = 0.0f;
    float hp = (s_in - r_plus_g * state_1 - state_2) * h;
    float bp = g * hp + state_1;
    float lp;
    state_1 = g * hp + bp;
    lp = g * bp + state_2;
    state_2 = g * bp + lp;
    s_out += gain * lp;
    out[n] = s_out;
  }
  s->state_1_ = state_1;
  s->state_2_ = state_2;
}

void resonator_svf4_init(ResonatorSvf4 *s) {
  int i;
  for (i = 0; i < kModeBatchSize; ++i) {
    s->state_1_[i] = 0.0f;
    s->state_2_[i] = 0.0f;
  }
}

void resonator_svf4_band_pass_add(ResonatorSvf4 *s, const float *f, const float *q, const float *gain, const float *in,
                                  float *out, int size) {
  float g[kModeBatchSize];
  float r_plus_g[kModeBatchSize];
  float h[kModeBatchSize];
  float state_1[kModeBatchSize];
  float state_2[kModeBatchSize];
  float gains[kModeBatchSize];
  int i, n;
  for (i = 0; i < kModeBatchSize; ++i) {
    float r;
    g[i] = tan_fast(f[i]);
    r = 1.0f / q[i];
    h[i] = 1.0f / (1.0f + r * g[i] + g[i] * g[i]);
    r_plus_g[i] = r + g[i];
    state_1[i] = s->state_1_[i];
    state_2[i] = s->state_2_[i];
    gains[i] = gain[i];
  }
  for (n = 0; n < size; ++n) {
    float s_in = in[n];
    float s_out = 0.0f;
    for (i = 0; i < kModeBatchSize; ++i) {
      float hp = (s_in - r_plus_g[i] * state_1[i] - state_2[i]) * h[i];
      float bp = g[i] * hp + state_1[i];
      float lp;
      state_1[i] = g[i] * hp + bp;
      lp = g[i] * bp + state_2[i];
      state_2[i] = g[i] * bp + lp;
      s_out += gains[i] * bp;
    }
    out[n] += s_out;
  }
  for (i = 0; i < kModeBatchSize; ++i) {
    s->state_1_[i] = state_1[i];
    s->state_2_[i] = state_2[i];
  }
}

#if defined(__ADSP21569__) && !defined(PL_SVF4_C)
/* The DSP build: resonator_svf4_band_pass_add's work (coefficients and sample loop) runs in lib/resonator_svf4.s
   (SIMD, two filters per instruction), bit-identical to the C above: the same float operations in the same order per
   filter, s_out summed from 0.0f in mode order, then out[n] += s_out. It takes even sizes 4..16 (RINGS calls with
   PLAITS_SUB = 16). -DPL_SVF4_C keeps the C only (the kernel test's reference); the host build never sees it. */
#define PL_SVF4_KERNEL 1
void resonator_svf4_kernel(ResonatorSvf4 *s, const float *f, const float *q, const float *gain, const float *in,
                           float *out, int size);
#endif

void resonator_init(Resonator *s, float position, int resolution) {
  /* CosineOscillator::InitApproximate(position), Start, then Next() per mode */
  float sign = 16.0f;
  float frequency = position;
  float iir_coefficient, y0, y1;
  int i;
  s->resolution_ = resolution < kMaxNumModes ? resolution : kMaxNumModes;
  frequency -= 0.25f;
  if (frequency < 0.0f) {
    frequency = -frequency;
  } else {
    if (frequency > 0.5f) {
      frequency -= 0.5f;
    } else {
      sign = -16.0f;
    }
  }
  iir_coefficient = sign * frequency * (1.0f - 2.0f * frequency);
  y1 = iir_coefficient * 0.25f;
  y0 = 0.5f;
  for (i = 0; i < resolution; ++i) {
    float temp = y0;
    y0 = iir_coefficient * y0 - y1;
    y1 = temp;
    s->mode_amplitude_[i] = (temp + 0.5f) * 0.25f;
  }
  for (i = 0; i < kMaxNumModes / kModeBatchSize; ++i) {
    resonator_svf4_init(&s->mode_filters_[i]);
  }
}

/* NthHarmonicCompensation(n, stiffness). */
static float nth_harmonic_compensation(int n, float stiffness) {
  float stretch_factor = 1.0f;
  int i;
  for (i = 0; i < n - 1; ++i) {
    stretch_factor += stiffness;
    if (stiffness < 0.0f) {
      stiffness *= 0.93f;
    } else {
      stiffness *= 0.98f;
    }
  }
  return 1.0f / stretch_factor;
}

void resonator_process(Resonator *s, float f0, float structure, float brightness, float damping, const float *in,
                       float *out, int size) {
  float stiffness, harmonic, stretch_factor, q_sqrt, q, q_loss;
  float mode_q[kModeBatchSize];
  float mode_f[kModeBatchSize];
  float mode_a[kModeBatchSize];
  int batch_counter = 0;
  int batch = 0;
  int i;

  PL_IDX(18, (int)(structure * 64.0f))
  stiffness = Interpolate(PL_F(pl_lut_stiffness), structure, 64.0f);
  f0 *= nth_harmonic_compensation(3, stiffness);

  harmonic = f0;
  stretch_factor = 1.0f;
  q_sqrt = SemitonesToRatio(damping * 79.7f);
  q = 500.0f * q_sqrt * q_sqrt;
  brightness *= 1.0f - structure * 0.3f;
  brightness *= 1.0f - damping * 0.3f;
  q_loss = brightness * (2.0f - brightness) * 0.85f + 0.15f;

  for (i = 0; i < s->resolution_; ++i) {
    float mode_frequency = harmonic * stretch_factor;
    float mode_attenuation;
    if (mode_frequency >= 0.499f) {
      mode_frequency = 0.499f;
    }
    mode_attenuation = 1.0f - mode_frequency * 2.0f;

    mode_f[batch_counter] = mode_frequency;
    mode_q[batch_counter] = 1.0f + mode_frequency * q;
    mode_a[batch_counter] = s->mode_amplitude_[i] * mode_attenuation;
    ++batch_counter;

    if (batch_counter == kModeBatchSize) {
      batch_counter = 0;
#ifdef PL_SVF4_KERNEL
      if (size >= 4 && size <= 16 && !(size & 1)) {
        resonator_svf4_kernel(&s->mode_filters_[batch], mode_f, mode_q, mode_a, in, out, size);
      } else
#endif
      {
        resonator_svf4_band_pass_add(&s->mode_filters_[batch], mode_f, mode_q, mode_a, in, out, size);
      }
      ++batch;
    }

    stretch_factor += stiffness;
    if (stiffness < 0.0f) {
      stiffness *= 0.93f;
    } else {
      stiffness *= 0.98f;
    }
    harmonic += f0;
    q *= q_loss;
  }
}
