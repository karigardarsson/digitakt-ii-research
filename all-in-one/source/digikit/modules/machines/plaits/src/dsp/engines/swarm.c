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
// PLAITS for the Digitakt II: Plaits' SwarmEngine in C99 (swarm.h).
#include "swarm.h"
#include "../lib/drums.h"
#include "../lib/fast_sine_oscillator.h"
#include "../lib/sine.h"

/* rank_ of grain i: (i - n) / n with n = 3.0f, as float bit patterns (negative float initialisers are wrong on the
 * core: PORTING.md drift 2). */
#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_SWARM_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: each grain's sample loop runs in lib/swarm_sm.s, bit-identical to the C loop below, which stays for the
   host and as the -DPL_SWARM_C reference (tests/plaits/test_swarm_kernel.py). */
#define PL_SWARM_KERNEL 1
void swarm_grain_kernel(float *g, float *out, float *aux, int size);
#endif

static const unsigned int swarm_rank[kSwarmGrains] = {
    0xbf800000u, 0xbf2aaaabu, 0xbeaaaaabu, 0x00000000u, 0x3eaaaaabu, 0x3f2aaaabu, 0x3f800000u, 0x3faaaaabu,
};

void swarm_init(Swarm *s) {
  int i;
  for (i = 0; i < kSwarmGrains; ++i) {
    /* GrainEnvelope::Init */
    s->from_[i] = 0.0f;
    s->interval_[i] = 1.0f;
    s->phase_[i] = 1.0f;
    s->fm_[i] = 0.0f;
    s->amplitude_[i] = 0.5f;
    s->previous_size_ratio_[i] = 0.0f;
    s->filter_coefficient_[i] = 0.0f;
    /* AdditiveSawOscillator::Init */
    s->saw_phase_[i] = 0.0f;
    s->saw_next_sample_[i] = 0.0f;
    s->saw_frequency_[i] = 0.01f;
    s->saw_gain_[i] = 0.0f;
    /* FastSineOscillator::Init */
    s->sine_x_[i] = 1.0f;
    s->sine_y_[i] = 0.0f;
    s->sine_epsilon_[i] = 0.0f;
    s->sine_amplitude_[i] = 0.0f;
  }
  s->rng_ = 0x21u;
}

void swarm_seed(Swarm *s, int track) {
  int k;
  s->rng_ = 0x21u;
  for (k = 0; k < track; ++k) {
    s->rng_ += 0x9e3779b1u;  /* track * 0x9e3779b1 without an integer multiply (the encoding gate refuses one) */
  }
}

/* GrainEnvelope::Step(rate, burst_mode, start_burst) of grain i. */
static void swarm_step(Swarm *s, int i, float rate, int burst_mode, int start_burst) {
  int randomize = 0;
  if (start_burst) {
    s->phase_[i] = 0.5f;
    s->fm_[i] = 16.0f;
    randomize = 1;
  } else {
    float ph = s->phase_[i] + rate * s->fm_[i];
    if (ph >= 1.0f) {
      ph -= itof_nonneg((int)ph);
      randomize = 1;
    }
    s->phase_[i] = ph;
  }
  if (randomize) {
    float r;
    s->from_[i] += s->interval_[i];
    r = rng_float(&s->rng_);
    s->interval_[i] = r - s->from_[i];
    r = rng_float(&s->rng_);
    if (burst_mode) {
      s->fm_[i] *= 0.8f + 0.2f * r;
    } else {
      s->fm_[i] = 0.5f + 1.5f * r;
    }
  }
}

void swarm_render(Swarm *s, const Params *p, float *out, float *aux, int size) {
  const float f0 = NoteToFrequency(p->note);
  const float control_rate = itof_nonneg(size);
  const float density = NoteToFrequency(p->timbre * 120.0f) * 0.025f * control_rate;
  const float spread = p->harmonics * p->harmonics * p->harmonics;
  float size_ratio = 0.25f * SemitonesToRatio((1.0f - p->morph) * 84.0f);
  const int burst_mode = !(p->trigger & 2);  /* !(trigger & TRIGGER_UNPATCHED) */
  const int start_burst = p->trigger & 1;
  int i, n;

  for (n = 0; n < size; ++n) {
    out[n] = 0.0f;
    aux[n] = 0.0f;
  }

  for (i = 0; i < kSwarmGrains; ++i) {
    const float rank = PL_F(swarm_rank)[i];
    const int big = size_ratio >= 1.0f;
    float amplitude, expo_amount, frequency, linear_amount;
    float fm_v, fm_i, gn_v, gn_i, ep_v, ep_i, am_v, am_i;
    float saw_phase, saw_next, sx, sy, norm;
    float saw_f, sine_f, sine_a;

    swarm_step(s, i, density, burst_mode, start_burst);

    /* GrainEnvelope::amplitude(size_ratio) */
    {
      float target = 1.0f;
      if (big) {
        float phase = (s->phase_[i] - 0.5f) * size_ratio;
        float e;
        CONSTRAIN(phase, -1.0f, 1.0f);
        e = Sine(0.5f * phase + 1.25f);
        target = 0.5f * (e + 1.0f);
      }
      if (big != (s->previous_size_ratio_[i] >= 1.0f)) {
        s->filter_coefficient_[i] = 0.5f;
      }
      s->filter_coefficient_[i] *= 0.95f;
      s->previous_size_ratio_[i] = size_ratio;
      ONE_POLE(s->amplitude_[i], target, 0.5f - s->filter_coefficient_[i]);
      amplitude = s->amplitude_[i] * 0.125f;  /* scale = 1 / kNumSwarmVoices */
    }

    /* GrainEnvelope::frequency(size_ratio) */
    if (big) {
      expo_amount = s->from_[i];
    } else {
      expo_amount = 2.0f * (s->from_[i] + s->interval_[i] * s->phase_[i]) - 1.0f;
    }
    {
      const float semitones = 48.0f * expo_amount * spread * rank;
      PL_IDX(38, (int)(semitones + 128.0f))
      frequency = f0 * SemitonesToRatio(semitones);
    }
    linear_amount = rank * (rank + 0.01f) * spread * 0.25f;
    frequency *= 1.0f + linear_amount;

    /* AdditiveSawOscillator: its interpolators (frequency, gain) */
    saw_f = frequency;
    if (saw_f >= kMaxFrequency) {
      saw_f = kMaxFrequency;
    }
    fm_v = s->saw_frequency_[i];
    fm_i = (saw_f - fm_v) / control_rate;
    gn_v = s->saw_gain_[i];
    gn_i = (amplitude - gn_v) / control_rate;
    saw_phase = s->saw_phase_[i];
    saw_next = s->saw_next_sample_[i];

    /* FastSineOscillator (ADDITIVE): its interpolators (epsilon, amplitude) */
    sine_f = frequency;
    sine_a = amplitude;
    if (sine_f >= 0.25f) {
      sine_f = 0.25f;
      sine_a = 0.0f;
    } else {
      sine_a *= 1.0f - sine_f * 4.0f;
    }
    ep_v = s->sine_epsilon_[i];
    ep_i = (fast_2_sin(sine_f) - ep_v) / control_rate;
    am_v = s->sine_amplitude_[i];
    am_i = (sine_a - am_v) / control_rate;
    sx = s->sine_x_[i];
    sy = s->sine_y_[i];
    norm = sx * sx + sy * sy;
    if (norm <= 0.5f || norm >= 2.0f) {
      const float scale = fast_rsqrt_carmack(norm);
      sx *= scale;
      sy *= scale;
    }

#ifdef PL_SWARM_KERNEL
    {
      float g[12];
      g[0] = fm_v;
      g[1] = fm_i;
      g[2] = gn_v;
      g[3] = gn_i;
      g[4] = ep_v;
      g[5] = ep_i;
      g[6] = am_v;
      g[7] = am_i;
      g[8] = saw_phase;
      g[9] = saw_next;
      g[10] = sx;
      g[11] = sy;
      swarm_grain_kernel(g, out, aux, size);
      fm_v = g[0];
      gn_v = g[2];
      ep_v = g[4];
      am_v = g[6];
      saw_phase = g[8];
      saw_next = g[9];
      sx = g[10];
      sy = g[11];
    }
#else
    for (n = 0; n < size; ++n) {
      float this_sample = saw_next;
      float fq, e;
      saw_next = 0.0f;
      fm_v += fm_i;
      fq = fm_v;
      saw_phase += fq;
      if (saw_phase >= 1.0f) {
        float t;
        saw_phase -= 1.0f;
        t = saw_phase / fq;
        this_sample -= ThisBlepSample(t);
        saw_next -= NextBlepSample(t);
      }
      saw_next += saw_phase;
      gn_v += gn_i;
      out[n] += (2.0f * this_sample - 1.0f) * gn_v;

      ep_v += ep_i;
      e = ep_v;
      sx += e * sy;
      sy -= e * sx;
      am_v += am_i;
      aux[n] += am_v * sx;
    }
#endif

    s->saw_frequency_[i] = fm_v;
    s->saw_gain_[i] = gn_v;
    s->saw_phase_[i] = saw_phase;
    s->saw_next_sample_[i] = saw_next;
    s->sine_epsilon_[i] = ep_v;
    s->sine_amplitude_[i] = am_v;
    s->sine_x_[i] = sx;
    s->sine_y_[i] = sy;
    size_ratio *= 0.97f;
  }
}
