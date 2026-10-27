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
// PLAITS for the Digitakt II: Plaits' WavetableOscillator in C99 (lib/wavetable_oscillator.h). The interpolators,
// InterpolateWave and the Differentiator are written out in the loop (Selache's C has no inlining), with the same
// float operations in the same order as upstream. With approximate_scale, upstream's `scale` is 1.0f and its product
// is left out (x * 1.0f is x). Index ranges, for frequency in [1e-7, 0.25] and waveform in [0, 1]: the wave index is
// at most int(waveform * (num_waves - 1.0001)) + 1 <= num_waves - 1, and the sample index at most 127 + 1 (upstream's
// table holds 132 entries per wave). tests/plaits/test_wavetable_chord_host.py checks both on the host.
#include "wavetable_oscillator.h"
#include "units.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_WT_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: the sample loop runs in lib/wavetable_sm.s, bit-identical to the C loop below, which stays for the
   host and as the -DPL_WT_C reference (tests/plaits/test_wavetable_kernel.py). */
#define PL_WT_KERNEL 1
void wavetable_kernel(float *st, const float *k, float *out, int size, const float *waves, const int *offsets);
#endif

void differentiator_init(Differentiator *d) {
  d->previous_ = 0.0f;
  d->lp_ = 0.0f;
}

void wavetable_oscillator_init(WavetableOscillator *s) {
  s->phase_ = 0.0f;
  s->frequency_ = 0.0f;
  s->amplitude_ = 0.0f;
  s->waveform_ = 0.0f;
  s->lp_ = 0.0f;
  differentiator_init(&s->differentiator_);
}

void wavetable_oscillator_render(WavetableOscillator *s, float frequency, float amplitude, float waveform,
                                 const float *waves, const int *offsets, int num_waves, float *out, int size) {
  Interp frequency_modulation, amplitude_modulation, waveform_modulation;
  float lp, phase, d_lp, d_previous;

  CONSTRAIN(frequency, 0.0000001f, kMaxFrequency)

  amplitude *= 1.0f - 2.0f * frequency;            /* attenuate_high_frequencies */
  amplitude *= 1.0f / (frequency * 131072.0f);     /* approximate_scale */

  frequency_modulation = interp(&s->frequency_, frequency, size);
  amplitude_modulation = interp(&s->amplitude_, amplitude, size);
  waveform_modulation = interp(&s->waveform_, waveform * (itof_nonneg(num_waves) - 1.0001f), size);

#ifdef PL_WT_KERNEL
  {
    float st[7], k[6];
    st[0] = frequency_modulation.value;
    st[1] = waveform_modulation.value;
    st[2] = amplitude_modulation.value;
    st[3] = s->phase_;
    st[4] = s->lp_;
    st[5] = s->differentiator_.lp_;
    st[6] = s->differentiator_.previous_;
    k[0] = frequency_modulation.increment;
    k[1] = waveform_modulation.increment;
    k[2] = amplitude_modulation.increment;
    k[3] = 128.0f;
    k[4] = 1.0f;
    k[5] = 8388608.0f;
    wavetable_kernel(st, k, out, size, waves, offsets);
    frequency_modulation.value = st[0];
    waveform_modulation.value = st[1];
    amplitude_modulation.value = st[2];
    phase = st[3];
    lp = st[4];
    d_lp = st[5];
    d_previous = st[6];
  }
#else
  lp = s->lp_;
  phase = s->phase_;
  d_lp = s->differentiator_.lp_;
  d_previous = s->differentiator_.previous_;
  while (size--) {
    float f0, cutoff, wf, p, sample;
    int waveform_integral, p_integral;
    float waveform_fractional, p_fractional, x0, x1;
    const float *t;

    frequency_modulation.value += frequency_modulation.increment;  /* frequency_modulation.Next() */
    f0 = frequency_modulation.value;
    cutoff = minf(128.0f * f0, 1.0f);

    phase += f0;
    if (phase >= 1.0f) {
      phase -= 1.0f;
    }

    waveform_modulation.value += waveform_modulation.increment;
    wf = waveform_modulation.value;
    waveform_integral = (int)wf;
    waveform_fractional = wf - itof_nonneg(waveform_integral);

    p = phase * 128.0f;
    p_integral = (int)p;
    p_fractional = p - itof_nonneg(p_integral);
    PL_IDX(2, waveform_integral)
    PL_IDX(2, waveform_integral + 1)
    PL_IDX(3, p_integral)
    PL_IDX(3, p_integral + 1)

    t = waves + offsets[waveform_integral];        /* InterpolateWave(wavetable[waveform_integral], ...) */
    x0 = t[p_integral] + (t[p_integral + 1] - t[p_integral]) * p_fractional;
    t = waves + offsets[waveform_integral + 1];
    x1 = t[p_integral] + (t[p_integral + 1] - t[p_integral]) * p_fractional;

    sample = x0 + (x1 - x0) * waveform_fractional;  /* * scale (1.0f) */
    ONE_POLE(d_lp, sample - d_previous, cutoff)     /* differentiator_.Process(cutoff, sample) */
    d_previous = sample;
    ONE_POLE(lp, d_lp, cutoff)
    amplitude_modulation.value += amplitude_modulation.increment;
    *out++ += amplitude_modulation.value * lp;
  }
#endif
  s->lp_ = lp;
  s->phase_ = phase;
  s->differentiator_.lp_ = d_lp;
  s->differentiator_.previous_ = d_previous;
  commit(&frequency_modulation);
  commit(&amplitude_modulation);
  commit(&waveform_modulation);
}
