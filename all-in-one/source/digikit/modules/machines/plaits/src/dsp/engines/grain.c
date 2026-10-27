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

// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' GrainEngine (model FORMANT) in C99, from plaits/dsp/engine/grain_engine.cc,
// plaits/dsp/oscillator/grainlet_oscillator.h and z_oscillator.h. The grainlet oscillator and OUT follow the
// dt2-machine-lab translation (MIT, the notice above); the Z oscillator, the second DC blocker and AUX are from
// upstream directly. Differences from upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - int-to-float conversions go through dsp_util.h `itof_nonneg`, and the tables are the shared bit-pattern tables
//     of lib/tables.h.
// The host test tests/plaits/test_fm_grain_additive_host.py compares it bit for bit with upstream at 48,000 Hz.
#include "grain.h"
#include "../lib/sine.h"

#if defined(__ADSP21569__) && !defined(PL_GRAIN_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: the grainlets' and the Z oscillator's sample loops and both DC blockers run in lib/grain_sm.s,
   bit-identical to the C below, which stays for the host and as the -DPL_GRAIN_C reference
   (tests/plaits/test_grain_kernel.py). */
#define PL_GRAIN_KERNEL 1
void grainlet_kernel(Grainlet *s, const float *k, float *out, int size);
void z_kernel(ZOsc *s, const float *k, float *out, int size);
void grain_hp_kernel(OnePole *f, float *x, const float *y, int size);
#endif

static void grainlet_init(Grainlet *s) {
  s->carrier_phase_ = 0.0f;
  s->formant_phase_ = 0.0f;
  s->next_sample_ = 0.0f;

  s->carrier_frequency_ = 0.0f;
  s->formant_frequency_ = 0.0f;
  s->carrier_shape_ = 0.0f;
  s->carrier_bleed_ = 0.0f;
}

static float grainlet_carrier(float phase, float shape) {
  float t;
  shape *= 3.0f;
  {
    MAKE_INTEGRAL_FRACTIONAL(shape)
    t = 1.0f - shape_fractional;

    if (shape_integral == 0) {
      phase = phase * (1.0f + t * t * t * 15.0f);
      if (phase >= 1.0f) {
        phase = 1.0f;
      }
      phase += 0.75f;
    } else if (shape_integral == 1) {
      float breakpoint = 0.001f + 0.499f * t * t * t;
      if (phase < breakpoint) {
        phase *= (0.5f / breakpoint);
      } else {
        phase = 0.5f + (phase - breakpoint) * 0.5f / (1.0f - breakpoint);
      }
      phase += 0.75f;
    } else {
      t = 1.0f - t;
      phase = 0.25f + phase * (0.5f + t * t * t * 14.5f);
      if (phase >= 0.75f) phase = 0.75f;
    }
  }
  return (Sine(phase) + 1.0f) * 0.25f;
}

static float grainlet(float carrier_phase, float formant_phase, float shape, float bleed) {
  float carrier = grainlet_carrier(carrier_phase, shape);
  float formant = Sine(formant_phase);
  return carrier * (formant + bleed) / (1.0f + bleed);
}

static void grainlet_render(Grainlet *s, float carrier_frequency, float formant_frequency, float carrier_shape,
                            float carrier_bleed, float *out, int size) {
  Interp carrier_frequency_modulation, formant_frequency_modulation, carrier_shape_modulation,
      carrier_bleed_modulation;
  float next_sample;

  if (carrier_frequency >= kMaxFrequency * 0.5f) {
    carrier_frequency = kMaxFrequency * 0.5f;
  }
  if (formant_frequency >= kMaxFrequency) {
    formant_frequency = kMaxFrequency;
  }

  carrier_frequency_modulation = interp(&s->carrier_frequency_, carrier_frequency, size);
  formant_frequency_modulation = interp(&s->formant_frequency_, formant_frequency, size);
  carrier_shape_modulation = interp(&s->carrier_shape_, carrier_shape, size);
  carrier_bleed_modulation = interp(&s->carrier_bleed_, carrier_bleed, size);

#ifdef PL_GRAIN_KERNEL
  {
    float k[4];
    k[0] = carrier_frequency_modulation.increment;
    k[1] = formant_frequency_modulation.increment;
    k[2] = carrier_shape_modulation.increment;
    k[3] = carrier_bleed_modulation.increment;
    grainlet_kernel(s, k, out, size);
    return;
  }
#endif
  next_sample = s->next_sample_;

  while (size--) {
    int reset = 0;
    float reset_time = 0.0f;

    float this_sample = next_sample;
    float f0, f1;
    next_sample = 0.0f;

    f0 = next(&carrier_frequency_modulation);
    f1 = next(&formant_frequency_modulation);

    s->carrier_phase_ += f0;
    reset = s->carrier_phase_ >= 1.0f;

    if (reset) {
      float before, after, discontinuity;
      s->carrier_phase_ -= 1.0f;
      reset_time = s->carrier_phase_ / f0;
      before = grainlet(
          1.0f,
          s->formant_phase_ + (1.0f - reset_time) * f1,
          subsample(&carrier_shape_modulation, 1.0f - reset_time),
          subsample(&carrier_bleed_modulation, 1.0f - reset_time));

      after = grainlet(
          0.0f,
          0.0f,
          subsample(&carrier_shape_modulation, 1.0f),
          subsample(&carrier_bleed_modulation, 1.0f));

      discontinuity = after - before;
      this_sample += discontinuity * ThisBlepSample(reset_time);
      next_sample += discontinuity * NextBlepSample(reset_time);
      s->formant_phase_ = reset_time * f1;
    } else {
      s->formant_phase_ += f1;
      if (s->formant_phase_ >= 1.0f) {
        s->formant_phase_ -= 1.0f;
      }
    }

    next_sample += grainlet(
        s->carrier_phase_,
        s->formant_phase_,
        next(&carrier_shape_modulation),
        next(&carrier_bleed_modulation));
    *out++ = this_sample;
  }

  s->next_sample_ = next_sample;
  commit(&carrier_frequency_modulation);
  commit(&formant_frequency_modulation);
  commit(&carrier_shape_modulation);
  commit(&carrier_bleed_modulation);
}

static void z_init(ZOsc *s) {
  s->carrier_phase_ = 0.0f;
  s->discontinuity_phase_ = 0.0f;
  s->formant_phase_ = 0.0f;
  s->next_sample_ = 0.0f;

  s->carrier_frequency_ = 0.0f;
  s->formant_frequency_ = 0.0f;
  s->carrier_shape_ = 0.0f;
  s->mode_ = 0.0f;
}

static float z_value(float c, float d, float f, float shape, float mode) {
  float ramp_down = 0.5f * (1.0f + Sine(0.5f * d + 0.25f));
  float offset;
  float phase_shift;
  float discontinuity, contour;

  if (mode < 0.333f) {
    offset = 1.0f;
    phase_shift = 0.25f + mode * 1.50f;
  } else if (mode < 0.666f) {
    phase_shift = 0.7495f - (mode - 0.33f) * 0.75f;
    offset = -Sine(phase_shift);
  } else {
    phase_shift = 0.7495f - (mode - 0.33f) * 0.75f;
    offset = 0.001f;
  }

  discontinuity = Sine(f + phase_shift);
  if (shape < 0.5f) {
    shape *= 2.0f;
    if (c >= 0.5f) {
      ramp_down *= shape;
    }
    contour = 1.0f + (Sine(c + 0.25f) - 1.0f) * shape;
  } else {
    contour = Sine(c + shape * 0.5f);
  }
  return (ramp_down * (offset + discontinuity) - offset) * contour;
}

static void z_render(ZOsc *s, float carrier_frequency, float formant_frequency, float carrier_shape, float mode,
                     float *out, int size) {
  Interp carrier_frequency_modulation, formant_frequency_modulation, carrier_shape_modulation, mode_modulation;
  float next_sample;

  if (carrier_frequency >= kMaxFrequency * 0.5f) {
    carrier_frequency = kMaxFrequency * 0.5f;
  }
  if (formant_frequency >= kMaxFrequency) {
    formant_frequency = kMaxFrequency;
  }

  carrier_frequency_modulation = interp(&s->carrier_frequency_, carrier_frequency, size);
  formant_frequency_modulation = interp(&s->formant_frequency_, formant_frequency, size);
  carrier_shape_modulation = interp(&s->carrier_shape_, carrier_shape, size);
  mode_modulation = interp(&s->mode_, mode, size);

#ifdef PL_GRAIN_KERNEL
  {
    float k[4];
    k[0] = carrier_frequency_modulation.increment;
    k[1] = formant_frequency_modulation.increment;
    k[2] = carrier_shape_modulation.increment;
    k[3] = mode_modulation.increment;
    z_kernel(s, k, out, size);
    return;
  }
#endif
  next_sample = s->next_sample_;

  while (size--) {
    int reset = 0;
    float reset_time = 0.0f;

    float this_sample = next_sample;
    float f0, f1;
    next_sample = 0.0f;

    f0 = next(&carrier_frequency_modulation);
    f1 = next(&formant_frequency_modulation);

    s->discontinuity_phase_ += 2.0f * f0;
    s->carrier_phase_ += f0;
    reset = s->discontinuity_phase_ >= 1.0f;

    if (reset) {
      float carrier_phase_before, carrier_phase_after, before, after, discontinuity;
      s->discontinuity_phase_ -= 1.0f;
      reset_time = s->discontinuity_phase_ / (2.0f * f0);

      carrier_phase_before = s->carrier_phase_ >= 1.0f ? 1.0f : 0.5f;
      carrier_phase_after = s->carrier_phase_ >= 1.0f ? 0.0f : 0.5f;
      before = z_value(
          carrier_phase_before,
          1.0f,
          s->formant_phase_ + (1.0f - reset_time) * f1,
          subsample(&carrier_shape_modulation, 1.0f - reset_time),
          subsample(&mode_modulation, 1.0f - reset_time));

      after = z_value(
          carrier_phase_after,
          0.0f,
          0.0f,
          subsample(&carrier_shape_modulation, 1.0f),
          subsample(&mode_modulation, 1.0f));

      discontinuity = after - before;
      this_sample += discontinuity * ThisBlepSample(reset_time);
      next_sample += discontinuity * NextBlepSample(reset_time);
      s->formant_phase_ = reset_time * f1;

      if (s->carrier_phase_ > 1.0f) {
        s->carrier_phase_ = s->discontinuity_phase_ * 0.5f;
      }
    } else {
      s->formant_phase_ += f1;
      if (s->formant_phase_ >= 1.0f) {
        s->formant_phase_ -= 1.0f;
      }
    }

    if (s->carrier_phase_ >= 1.0f) {
      s->carrier_phase_ -= 1.0f;
    }

    next_sample += z_value(
        s->carrier_phase_,
        s->discontinuity_phase_,
        s->formant_phase_,
        next(&carrier_shape_modulation),
        next(&mode_modulation));
    *out++ = this_sample;
  }

  s->next_sample_ = next_sample;
  commit(&carrier_frequency_modulation);
  commit(&formant_frequency_modulation);
  commit(&carrier_shape_modulation);
  commit(&mode_modulation);
}

void grain_init(Grain *s) {
  grainlet_init(&s->grainlet_[0]);
  grainlet_init(&s->grainlet_[1]);
  z_init(&s->z_oscillator_);
  one_pole_init(&s->dc_blocker_[0]);
  one_pole_init(&s->dc_blocker_[1]);
}

void grain_render(Grain *s, const Params *p, float *out, float *aux, int size) {
  const float root = p->note;
  const float f0 = NoteToFrequency(root);

  const float f1 = NoteToFrequency(24.0f + 84.0f * p->timbre);
  const float ratio = SemitonesToRatio(-24.0f + 48.0f * p->harmonics);
  const float carrier_bleed = p->harmonics < 0.5f
      ? 1.0f - 2.0f * p->harmonics
      : 0.0f;
  const float carrier_bleed_fixed = carrier_bleed * (2.0f - carrier_bleed);
  const float carrier_shape = 0.33f + (p->morph - 0.33f) *
      maxf(1.0f - f0 * 24.0f, 0.0f);
  float cutoff;
  int i;

  grainlet_render(&s->grainlet_[0], f0, f1, carrier_shape, carrier_bleed_fixed, out, size);
  grainlet_render(&s->grainlet_[1], f0, f1 * ratio, carrier_shape, carrier_bleed_fixed, aux, size);
  one_pole_set_f(&s->dc_blocker_[0], 0.3f * f0);
#ifdef PL_GRAIN_KERNEL
  grain_hp_kernel(&s->dc_blocker_[0], out, aux, size);
#else
  for (i = 0; i < size; ++i) {
    out[i] = one_pole_high_pass(&s->dc_blocker_[0], out[i] + aux[i]);
  }
#endif

  cutoff = NoteToFrequency(root + 96.0f * p->timbre);
  z_render(
      &s->z_oscillator_,
      f0,
      cutoff,
      p->morph,
      p->harmonics,
      aux,
      size);

  one_pole_set_f(&s->dc_blocker_[1], 0.3f * f0);
#ifdef PL_GRAIN_KERNEL
  grain_hp_kernel(&s->dc_blocker_[1], aux, 0, size);
#else
  for (i = 0; i < size; ++i) {
    aux[i] = one_pole_high_pass(&s->dc_blocker_[1], aux[i]);
  }
#endif
}
