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
// PLAITS for the Digitakt II: the VCF-VA engine (Plaits VirtualAnalogVCFEngine, model 16) in C99, from
// plaits/dsp/engine2/virtual_analog_vcf_engine.{h,cc} and plaits/dsp/oscillator/variable_shape_oscillator.h (the
// <enable_sync, output_phase> = <false, false> instantiation). Not a strike engine: it runs on between notes and reads
// neither the trigger nor the accent. HARMONICS sets the filter's resonance and the gain staging, TIMBRE the cutoff
// (relative to the note), MORPH the oscillator shape and the sub oscillator. OUT is the low pass, AUX the high pass.
// Differences from upstream, none of them in the audio: the pitch reference is exactly 48,000 Hz (lib/units.h
// NoteToFrequency); bool is int; int-to-float conversions go through itof_nonneg; fabsf, max and min are absf, maxf
// and minf; Svf::set_f_q<FREQUENCY_FAST> is tan_fast (lib/drums.h) and svf_set_g_q.
#include "vcf.h"
#include "../lib/drums.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_VCF_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: both oscillators run on PHASE-DIST's oscillator kernel (lib/phase_dist_sm.s, pd_osc_kernel with flags
   2: no sync, no phase output: the VariableShapeOscillator of vs_render), the filter loop in lib/vcf_sm.s; both
   bit-identical to the C below, which stays for the host and as the -DPL_VCF_C reference
   (tests/plaits/test_vcf_kernel.py). */
#define PL_VCF_KERNEL 1
void pd_osc_kernel(void *s, const float *k, float *out, int size, int flags);
void vcf_filter_kernel(Vcf *s, const float *k, float *out, float *aux, int size);
#endif

static float NextIntegratedBlepSample(float t) {
  const float t1 = 0.5f * t;
  const float t2 = t1 * t1;
  const float t4 = t2 * t2;
  return 0.1875f - t1 + 1.5f * t2 - t4;
}

static float ThisIntegratedBlepSample(float t) { return NextIntegratedBlepSample(1.0f - t); }

static void vs_init(VcfOsc *s) {
  s->slave_phase_ = 0.0f;
  s->next_sample_ = 0.0f;
  s->previous_pw_ = 0.5f;
  s->high_ = 0;
  s->slave_frequency_ = 0.01f;
  s->pw_ = 0.5f;
  s->waveshape_ = 0.0f;
}

static float vs_naive(float phase, float pw, float slope_up, float slope_down, float triangle_amount,
                      float square_amount) {
  float saw = phase;
  float square = phase < pw ? 0.0f : 1.0f;
  float triangle = phase < pw ? phase * slope_up : 1.0f - (phase - pw) * slope_down;
  saw += (square - saw) * square_amount;
  saw += (triangle - saw) * triangle_amount;
  return saw;
}

/* VariableShapeOscillator::Render(frequency, pw, waveshape, out, size): no sync, no phase output. */
static void vs_render(VcfOsc *s, float frequency, float pw, float waveshape, float *out, int size) {
  Interp fm, pwm, waveshape_modulation;
  float next_sample;
  if (frequency >= kMaxFrequency) {
    frequency = kMaxFrequency;
  }
  if (frequency >= 0.25f) {
    pw = 0.5f;
  } else {
    CONSTRAIN(pw, frequency * 2.0f, 1.0f - 2.0f * frequency);
  }
  fm = interp(&s->slave_frequency_, frequency, size);
  pwm = interp(&s->pw_, pw, size);
  waveshape_modulation = interp(&s->waveshape_, waveshape, size);
#ifdef PL_VCF_KERNEL
  {
    /* pd_osc_kernel's PdOsc layout: master_phase_, slave_phase_, next_sample_, previous_pw_, high_, then the
       interpolators master_frequency_, slave_frequency_, pw_, waveshape_, phase_modulation_ (the master and the phase
       modulation are not used with flags 2). */
    float t[10];
    float k[5];
    t[0] = 0.0f;
    t[1] = s->slave_phase_;
    t[2] = s->next_sample_;
    t[3] = s->previous_pw_;
    ((int *)(void *)t)[4] = s->high_;
    t[5] = 0.0f;
    t[6] = s->slave_frequency_;
    t[7] = s->pw_;
    t[8] = s->waveshape_;
    t[9] = 0.0f;
    k[0] = 0.0f;
    k[1] = fm.increment;
    k[2] = pwm.increment;
    k[3] = waveshape_modulation.increment;
    k[4] = 0.0f;
    (void)next_sample;
    pd_osc_kernel(t, k, out, size, 2);
    s->slave_phase_ = t[1];
    s->next_sample_ = t[2];
    s->previous_pw_ = t[3];
    s->high_ = ((int *)(void *)t)[4];
    s->slave_frequency_ = t[6];
    s->pw_ = t[7];
    s->waveshape_ = t[8];
    return;
  }
#endif
  next_sample = s->next_sample_;
  while (size--) {
    float this_sample = next_sample;
    float slave_frequency, pw_i, ws, square_amount, triangle_amount, slope_up, slope_down;
    next_sample = 0.0f;
    slave_frequency = next(&fm);
    pw_i = next(&pwm);
    ws = next(&waveshape_modulation);
    square_amount = maxf(ws - 0.5f, 0.0f) * 2.0f;
    triangle_amount = maxf(1.0f - ws * 2.0f, 0.0f);
    slope_up = 1.0f / pw_i;
    slope_down = 1.0f / (1.0f - pw_i);
    s->slave_phase_ += slave_frequency;
    for (;;) {
      float t, triangle_step;
      if (!s->high_) {
        if (s->slave_phase_ < pw_i) {
          break;
        }
        t = (s->slave_phase_ - pw_i) / (s->previous_pw_ - pw_i + slave_frequency);
        triangle_step = (slope_up + slope_down) * slave_frequency;
        triangle_step *= triangle_amount;
        this_sample += square_amount * ThisBlepSample(t);
        next_sample += square_amount * NextBlepSample(t);
        this_sample -= triangle_step * ThisIntegratedBlepSample(t);
        next_sample -= triangle_step * NextIntegratedBlepSample(t);
        s->high_ = 1;
      }
      if (s->high_) {
        if (s->slave_phase_ < 1.0f) {
          break;
        }
        s->slave_phase_ -= 1.0f;
        t = s->slave_phase_ / slave_frequency;
        triangle_step = (slope_up + slope_down) * slave_frequency;
        triangle_step *= triangle_amount;
        this_sample -= (1.0f - triangle_amount) * ThisBlepSample(t);
        next_sample -= (1.0f - triangle_amount) * NextBlepSample(t);
        this_sample += triangle_step * ThisIntegratedBlepSample(t);
        next_sample += triangle_step * NextIntegratedBlepSample(t);
        s->high_ = 0;
      }
    }
    next_sample += vs_naive(s->slave_phase_, pw_i, slope_up, slope_down, triangle_amount, square_amount);
    s->previous_pw_ = pw_i;
    *out++ = 2.0f * this_sample - 1.0f;
  }
  s->next_sample_ = next_sample;
  commit(&fm);
  commit(&pwm);
  commit(&waveshape_modulation);
}

void vcf_init(Vcf *s) {
  int i;
  vs_init(&s->oscillator_);
  vs_init(&s->sub_oscillator_);
  /* Svf::Init: set_f_q<FREQUENCY_DIRTY>(0.01f, 100.0f) (g = 0.01 * (pi + a * 0.01^2), r = 0.01f) and Reset, both lanes;
   * the values come from svf_init so the two stay one definition. */
  for (i = 0; i < 2; ++i) {
    Svf t;
    svf_init(&t);
    s->svf_.g_[i] = t.g_;
    s->svf_.r_[i] = t.r_;
    s->svf_.h_[i] = t.h_;
    s->svf_.state_1_[i] = 0.0f;
    s->svf_.state_2_[i] = 0.0f;
  }
  s->previous_sub_gain_ = 0.0f;
  s->previous_cutoff_ = 0.0f;
  s->previous_stage2_gain_ = 0.0f;
  s->previous_q_ = 0.0f;
  s->previous_gain_ = 0.0f;
}

void vcf_render(Vcf *s, const Params *p, float *out, float *aux, int size) {
  const float f0 = NoteToFrequency(p->note);
  float shape = (p->morph - 0.25f) * 2.0f + 0.5f;
  float pw = (p->morph - 0.5f) * 2.0f + 0.5f;
  float sub_gain, cutoff, stage2_gain, resonance, resonance_sqr, q, gain, semitones;
  Interp sub_gain_modulation, cutoff_modulation, stage2_gain_modulation, q_modulation, gain_modulation;
  int i;
  CONSTRAIN(shape, 0.5f, 1.0f);
  if (p->morph > 0.75f) {
    pw = 2.5f - p->morph * 2.0f;
  }
  CONSTRAIN(pw, 0.5f, 0.98f);
  sub_gain = maxf(absf(p->morph - 0.5f) - 0.3f, 0.0f) * 5.0f;
  vs_render(&s->oscillator_, f0, pw, shape, out, size);
  vs_render(&s->sub_oscillator_, f0 * 0.501f, 0.5f, 1.0f, aux, size);
  /* SemitonesToRatio reads lut_pitch_ratio_high at (int)(semitones + 128): 104..224 for TIMBRE in [0, 1] (the wrapper
   * clamps the knobs), inside the 256 entries. Recorded for the tests (table 29). */
  semitones = (p->timbre - 0.2f) * 120.0f;
  PL_IDX(29, (int)(semitones + 128.0f))
  cutoff = f0 * SemitonesToRatio(semitones);
  stage2_gain = 1.0f - (p->harmonics - 0.4f) * 4.0f;
  CONSTRAIN(stage2_gain, 0.0f, 1.0f);
  resonance = 2.667f * maxf(absf(p->harmonics - 0.5f) - 0.125f, 0.0f);
  resonance_sqr = resonance * resonance;
  q = resonance_sqr * resonance_sqr * 48.0f;
  gain = (p->harmonics - 0.7f) + 0.85f;
  CONSTRAIN(gain, 0.7f - resonance_sqr * 0.3f, 1.0f);
  sub_gain_modulation = interp(&s->previous_sub_gain_, sub_gain, size);
  cutoff_modulation = interp(&s->previous_cutoff_, cutoff, size);
  stage2_gain_modulation = interp(&s->previous_stage2_gain_, stage2_gain, size);
  q_modulation = interp(&s->previous_q_, q, size);
  gain_modulation = interp(&s->previous_gain_, gain, size);
#ifdef PL_VCF_KERNEL
  {
    float k[5];
    k[0] = cutoff_modulation.increment;
    k[1] = stage2_gain_modulation.increment;
    k[2] = q_modulation.increment;
    k[3] = gain_modulation.increment;
    k[4] = sub_gain_modulation.increment;
    (void)i;
    vcf_filter_kernel(s, k, out, aux, size);
    return;
  }
#endif
  /* Block loop with the filter state in locals (loaded once, stored once), no calls: Svf::Process written out. */
  {
    float g0, g1, r0, r1, h0, h1, a1, a2, b1, b2;
    a1 = s->svf_.state_1_[0]; a2 = s->svf_.state_2_[0];
    b1 = s->svf_.state_1_[1]; b2 = s->svf_.state_2_[1];
    for (i = 0; i < size; ++i) {
      const float c = minf(next(&cutoff_modulation), 0.25f);
      const float qi = next(&q_modulation);
      const float st2 = next(&stage2_gain_modulation);
      const float g = tan_fast(c);
      float gi, input, hp, bp, lp, lp2, bp2;
      /* set_f_q<FREQUENCY_FAST>(cutoff, 0.5f + q) and (cutoff, 0.5f + 0.025f * q) */
      g0 = g; r0 = 1.0f / (0.5f + qi); h0 = 1.0f / (1.0f + r0 * g0 + g0 * g0);
      g1 = g; r1 = 1.0f / (0.5f + 0.025f * qi); h1 = 1.0f / (1.0f + r1 * g1 + g1 * g1);
      gi = next(&gain_modulation);
      input = SoftClip((out[i] + aux[i] * next(&sub_gain_modulation)) * gi);
      hp = (input - r0 * a1 - g0 * a1 - a2) * h0;
      bp = g0 * hp + a1;
      a1 = g0 * hp + bp;
      lp = g0 * bp + a2;
      a2 = g0 * bp + lp;
      lp = SoftClip(lp * gi);
      {
        float hp2 = (lp - r1 * b1 - g1 * b1 - b2) * h1;
        bp2 = g1 * hp2 + b1;
        b1 = g1 * hp2 + bp2;
        lp2 = g1 * bp2 + b2;
        b2 = g1 * bp2 + lp2;
      }
      lp += st2 * (SoftClip(lp2) - lp);
      out[i] = lp;
      aux[i] = SoftClip(hp * gi);
    }
    s->svf_.g_[0] = g0; s->svf_.r_[0] = r0; s->svf_.h_[0] = h0;
    s->svf_.g_[1] = g1; s->svf_.r_[1] = r1; s->svf_.h_[1] = h1;
    s->svf_.state_1_[0] = a1; s->svf_.state_2_[0] = a2;
    s->svf_.state_1_[1] = b1; s->svf_.state_2_[1] = b2;
  }
  commit(&sub_gain_modulation);
  commit(&cutoff_modulation);
  commit(&stage2_gain_modulation);
  commit(&q_modulation);
  commit(&gain_modulation);
}
