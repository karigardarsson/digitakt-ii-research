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
// PLAITS for the Digitakt II: Plaits' NaiveSpeechSynth in C99 (lib/naive_speech_synth.h), from
// plaits/dsp/speech/naive_speech_synth.cc and plaits/dsp/oscillator/oscillator.h. Differences from upstream, none of
// which changes a result on the host:
//   - a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - the phoneme table holds floats (lib/speech_tables.c), and the amplitude's `/ 256.0f` is `* 0.00390625f` (the
//     same float: 1 / 256 is exact);
//   - (kNaiveSpeechNumPhonemes - 1.001f) and (kNaiveSpeechNumRegisters - 1.001f) are written as the float they round
//     to, 3.999f (5.0f - 1.001f, 0x407fef9e);
//   - 1.0f / resonance: 0.05f for resonance 20 and 2.0f for 0.5 (the quotients upstream's division gives), and
//     800.0f / kSampleRate as its float, 0.0166666675f;
//   - the oscillator is the IMPULSE_TRAIN shape of Plaits' Oscillator::Render<shape, false, false>, written out.
// Index ranges, for phoneme and vocal_register in [0, 1]: p and r are at most 3.999, so the rows read are 0..4.
// tests/plaits/test_speech_host.py compares the engine bit for bit with upstream and checks the ranges.
#include "naive_speech_synth.h"
#include "units.h"
#include "speech_tables.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

static void impulse_init(ImpulseOscillator *o) {
  o->phase_ = 0.5f;
  o->next_sample_ = 0.0f;
  o->lp_state_ = 1.0f;
  o->hp_state_ = 0.0f;
  o->frequency_ = 0.001f;
}

/* Oscillator::Render<OSCILLATOR_SHAPE_IMPULSE_TRAIN>(frequency, 0.5f, out, size). */
static void impulse_render(ImpulseOscillator *o, float frequency, float *out, int size) {
  Interp fm;
  float next_sample, this_sample, f, t;
  float phase = o->phase_, lp_state = o->lp_state_, hp_state = o->hp_state_;
  int i;
  CONSTRAIN(frequency, 0.000001f, 0.25f);  /* kMinFrequency, kMaxFrequency */
  fm = interp(&o->frequency_, frequency, size);
  next_sample = o->next_sample_;
  for (i = 0; i < size; ++i) {
    this_sample = next_sample;
    next_sample = 0.0f;
    f = next(&fm);
    phase += f;
    if (phase >= 1.0f) {
      phase -= 1.0f;
      t = phase / f;
      this_sample -= ThisBlepSample(t);
      next_sample -= NextBlepSample(t);
    }
    next_sample += phase;
    lp_state += 0.25f * ((hp_state - this_sample) - lp_state);
    out[i] = 4.0f * lp_state;
    hp_state = this_sample;
  }
  commit(&fm);
  o->phase_ = phase;
  o->lp_state_ = lp_state;
  o->hp_state_ = hp_state;
  o->next_sample_ = next_sample;
}

void naive_speech_synth_init(NaiveSpeechSynth *s) {
  int i;
  impulse_init(&s->pulse_);
  s->frequency_ = 0.0f;
  s->click_duration_ = 0;
  for (i = 0; i < kNaiveSpeechNumFormants; ++i) {
    svf_init(&s->filter_[i]);
  }
  svf_init(&s->pulse_coloration_);
  svf_set_f_r(&s->pulse_coloration_, 0.0166666675f, 2.0f);  /* 800.0f / kSampleRate */
}

void naive_speech_synth_render(NaiveSpeechSynth *s, int click, float frequency, float phoneme, float vocal_register,
                               float *excitation, float *output, int size) {
  const float *ph = PL_F(pl_naive_phonemes);
  float p, r;
  int i;

  if (click) {
    s->click_duration_ = 2400;  /* kSampleRate * 0.05f */
  }
  s->click_duration_ -= s->click_duration_ < size ? s->click_duration_ : size;
  if (s->click_duration_) {
    frequency *= 0.5f;
  }

  /* the glottal pulse */
  impulse_render(&s->pulse_, frequency, excitation, size);
  svf_band_pass(&s->pulse_coloration_, excitation, excitation, size);
  for (i = 0; i < size; ++i) {
    excitation[i] *= 4.0f;
  }

  p = phoneme * 3.999f;
  r = vocal_register * 3.999f;
  {
    MAKE_INTEGRAL_FRACTIONAL(p)
    MAKE_INTEGRAL_FRACTIONAL(r)
    /* the rows phonemes_[p][r], [p][r + 1], [p + 1][r], [p + 1][r + 1]: 50 floats per phoneme, 10 per register (5
     * formants of frequency, amplitude); * 50 and * 10 as shifts (Selache's integer multiply by a constant that is not
     * a power of two is an unproven encoding shape, docs/findings/20-patch-toolchains.md) */
    int p50 = (p_integral << 5) + (p_integral << 4) + (p_integral << 1);
    int r10 = (r_integral << 3) + (r_integral << 1);
    const float *p0r0 = ph + p50 + r10;
    const float *p0r1 = p0r0 + 10;
    const float *p1r0 = p0r0 + 50;
    const float *p1r1 = p1r0 + 10;
    PL_IDX(7, p_integral)
    PL_IDX(7, p_integral + 1)
    PL_IDX(8, r_integral)
    PL_IDX(8, r_integral + 1)

    for (i = 0; i < size; ++i) {
      output[i] = 0.0f;
    }
    for (i = 0; i < kNaiveSpeechNumFormants; ++i) {
      int k = i << 1;
      float p0r_f = p0r0[k] + (p0r1[k] - p0r0[k]) * r_fractional;
      float p1r_f = p1r0[k] + (p1r1[k] - p1r0[k]) * r_fractional;
      float f = p0r_f + (p1r_f - p0r_f) * p_fractional;
      float p0r_a = p0r0[k + 1] + (p0r1[k + 1] - p0r0[k + 1]) * r_fractional;
      float p1r_a = p1r0[k + 1] + (p1r1[k + 1] - p1r0[k + 1]) * r_fractional;
      float a = (p0r_a + (p1r_a - p0r_a) * p_fractional) * 0.00390625f;
      if (f >= 160.0f) {
        f = 160.0f;
      }
      f = ((440.0f / 8.0f) / 48000.0f) * SemitonesToRatio(f - 33.0f);
      if (s->click_duration_ && i == 0) {
        f *= 0.5f;
      }
      svf_set_f_r(&s->filter_[i], f, 0.05f);
      svf_band_pass_add(&s->filter_[i], excitation, output, size, a);
    }
  }
}
