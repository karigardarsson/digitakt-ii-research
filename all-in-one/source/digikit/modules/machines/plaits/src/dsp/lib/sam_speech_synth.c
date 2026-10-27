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
// PLAITS for the Digitakt II: Plaits' SAMSpeechSynth in C99 (lib/sam_speech_synth.h), from
// plaits/dsp/speech/sam_speech_synth.cc. Differences from upstream, none of which changes a result on the host:
//   - the phoneme table holds each formant's frequency as a float and its amplitude already looked up in
//     formant_amplitude_lut (lib/speech_tables.c, pl_sam_phonemes);
//   - static_cast<float>(formant_frequency[i]) is kept as a float beside the uint32 (formant_frequency_f): for f below
//     2^23 the truncation is exact in float, for f at or above 2^23 the float f is an integer already, so
//     float(uint32(f)) is computed without an int-to-float conversion (no FLOAT the encoding gate refuses);
//   - (kSAMNumVowels - 1.0001f) is written as the float it rounds to, 7.9999f (9.0f - 1.0001f, 0x40ffff2e);
//   - kSampleRate * 0.05f is 2400 (the float product is exactly 2400);
//   - r % kSAMNumConsonants is r & 7 (r >= 0: vowel, frequency and formant_shift are >= 0).
// Index ranges, for vowel and formant_shift in [0, 1]: the phoneme read is at most 16 + 1 (the guard entry upstream
// has at 17); SineRaw reads lut_sine at phase >> 23, 0..511. tests/plaits/test_speech_host.py checks both.
#include "sam_speech_synth.h"
#include "units.h"
#include "speech_tables.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_SAM_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: the sample loop runs in lib/sam_sm.s, bit-identical to the C loop below, which stays for the host and
   as the -DPL_SAM_C reference (tests/plaits/test_sam_kernel.py). */
#define PL_SAM_KERNEL 1
void sam_kernel(SAMSpeechSynth *s, const float *k, float *excitation, float *output, int size);
#endif

void sam_speech_synth_init(SAMSpeechSynth *s) {
  s->phase_ = 0.0f;
  s->frequency_ = 0.0f;
  s->pulse_next_sample_ = 0.0f;
  s->pulse_lp_ = 0.0f;
  s->formant_phase_[0] = 0;
  s->formant_phase_[1] = 0;
  s->formant_phase_[2] = 0;
  s->consonant_samples_ = 0;
  s->consonant_index_ = 0.0f;
}

void sam_speech_synth_render(SAMSpeechSynth *s, int consonant, float frequency, float vowel, float formant_shift,
                             float *excitation, float *output, int size) {
  const float *sine = PL_F(pl_lut_sine);
  unsigned int formant_frequency[kSAMNumFormants];
  float formant_frequency_f[kSAMNumFormants];
  float formant_amplitude[kSAMNumFormants];
  float phoneme, pulse_next_sample, pulse_this_sample, f, t, d, sum;
  Interp fm;
  int i, n;

  if (frequency >= 0.0625f) {
    frequency = 0.0625f;
  }

  if (consonant) {
    int r;
    s->consonant_samples_ = 2400;  /* kSampleRate * 0.05f */
    r = (int)((vowel + 3.0f * frequency + 7.0f * formant_shift) * 8.0f);
    s->consonant_index_ = itof_nonneg(r & 7);  /* r % kSAMNumConsonants */
  }
  s->consonant_samples_ -= s->consonant_samples_ < size ? s->consonant_samples_ : size;

  phoneme = s->consonant_samples_ ? (s->consonant_index_ + 9.0f) : vowel * 7.9999f;

  /* InterpolatePhonemeData */
  {
    const float *ph = PL_F(pl_sam_phonemes);
    MAKE_INTEGRAL_FRACTIONAL(phoneme)
    /* 6 floats per phoneme: (phoneme << 2) + (phoneme << 1) */
    const float *p_1 = ph + (phoneme_integral << 2) + (phoneme_integral << 1);
    const float *p_2 = p_1 + 6;
    PL_IDX(9, phoneme_integral)
    PL_IDX(9, phoneme_integral + 1)
    formant_shift = 1.0f + formant_shift * 2.5f;
    for (i = 0; i < kSAMNumFormants; ++i) {
      int k = i << 1;
      float f_1 = p_1[k];
      float f_2 = p_2[k];
      float a_1 = p_1[k + 1];
      float a_2 = p_2[k + 1];
      f = f_1 + (f_2 - f_1) * phoneme_fractional;
      f *= 8.0f * formant_shift * 4294967296.0f / 48000.0f;
      /* f < 254 * 8 * 3.5 * 2^32 / 48000 < 2^31 */
      formant_frequency[i] = (unsigned int)(int)f;
      formant_frequency_f[i] = f >= 8388608.0f ? f : itof_nonneg((int)f);
      formant_amplitude[i] = a_1 + (a_2 - a_1) * phoneme_fractional;
    }
  }

  fm = interp(&s->frequency_, frequency, size);
#ifdef PL_SAM_KERNEL
  {
    float k[10];
    k[0] = fm.increment;
    for (i = 0; i < kSAMNumFormants; ++i) {
      ((unsigned int *)(void *)k)[1 + i] = formant_frequency[i];
      k[4 + i] = formant_frequency_f[i];
      k[7 + i] = formant_amplitude[i];
    }
    (void)sine;
    (void)n;
    (void)t;
    (void)d;
    (void)sum;
    (void)pulse_this_sample;
    (void)pulse_next_sample;
    sam_kernel(s, k, excitation, output, size);
    return;
  }
#endif
  pulse_next_sample = s->pulse_next_sample_;

  for (n = 0; n < size; ++n) {
    pulse_this_sample = pulse_next_sample;
    pulse_next_sample = 0.0f;
    f = next(&fm);
    s->phase_ += f;

    if (s->phase_ >= 1.0f) {
      s->phase_ -= 1.0f;
      t = s->phase_ / f;
      /* t * formant_frequency < 2^31 */
      s->formant_phase_[0] = (unsigned int)(int)(t * formant_frequency_f[0]);
      s->formant_phase_[1] = (unsigned int)(int)(t * formant_frequency_f[1]);
      s->formant_phase_[2] = (unsigned int)(int)(t * formant_frequency_f[2]);
      pulse_this_sample -= ThisBlepSample(t);
      pulse_next_sample -= NextBlepSample(t);
    } else {
      s->formant_phase_[0] += formant_frequency[0];
      s->formant_phase_[1] += formant_frequency[1];
      s->formant_phase_[2] += formant_frequency[2];
    }
    pulse_next_sample += s->phase_;

    d = pulse_this_sample - 0.5f - s->pulse_lp_;
    s->pulse_lp_ += minf(16.0f * f, 1.0f) * d;
    excitation[n] = d;

    PL_IDX(10, (int)(s->formant_phase_[0] >> 23))
    PL_IDX(10, (int)(s->formant_phase_[1] >> 23))
    PL_IDX(10, (int)(s->formant_phase_[2] >> 23))
    sum = 0.0f;
    sum += sine[s->formant_phase_[0] >> 23] * formant_amplitude[0];
    sum += sine[s->formant_phase_[1] >> 23] * formant_amplitude[1];
    sum += sine[s->formant_phase_[2] >> 23] * formant_amplitude[2];
    sum *= (1.0f - s->phase_);
    output[n] = sum;
  }
  commit(&fm);
  s->pulse_next_sample_ = pulse_next_sample;
}
