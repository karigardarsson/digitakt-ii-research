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
// PLAITS for the Digitakt II: Plaits' LPCSpeechSynth, LPCSpeechSynthWordBank and LPCSpeechSynthController in C99
// (lib/lpc_speech_synth.h), from plaits/dsp/speech/lpc_speech_synth.{h,cc} and lpc_speech_synth_controller.{h,cc}.
//
// The word banks. Upstream's LPCSpeechSynthWordBank::Load decodes a bank's LPC10 bit stream into frames in Plaits'
// arena (up to 1024 frames and 32 word boundaries) whenever the bank changes. The result depends on the bank only, so
// lib/gen_speech_tables.py decodes all five banks with the same algorithm, and Load here only records which bank is
// loaded (and returns whether it changed, as upstream's does). The frames are the generated tables of
// lib/speech_tables.c (2,512 frames, 2 words each, and 97 word boundaries; 20,484 bytes once, against 14,464 bytes of
// arena per voice upstream: 231 KB for 16 voices) and decoding no longer costs a block's worth of DSP time at a bank
// change. tests/plaits/test_speech_host.py compares every decoded frame and boundary with upstream's Load.
//
// Other differences from upstream, none of which changes a result on the host:
//   - kCorrectedSampleRate is 48,000 Hz (the pitch shift; Plaits' 47,872.34);
//   - frames are read as LPCFrame floats (1 / period or 0, energy / 256, k / scale: the values upstream's PlayFrame and
//     BlendCoefficient compute, lib/speech_tables.h); PlayFrame without interpolation uses the first frame only
//     (upstream blends with the next frame at 0: f1 + (f2 - f1) * 0 is f1 for the finite, non-negative-zero values
//     here), so it does not read past a table;
//   - stmlib's Random is a state per voice (rng_state_, 0x21 at init as at Plaits' power-up), its multiply by 1664525
//     written as shifts and adds (Selache's integer multiply by such a constant is an unproven encoding shape,
//     docs/findings/20-patch-toolchains.md); GetSample() > 0 is tested on the top 16 bits;
//   - lut_lpc_excitation_pulse holds s / 128 as floats (lib/speech_tables.c);
//   - r % kLPCSpeechSynthNumConsonants is a loop of subtractions (r >= 0; Selache's C has no divide routine);
//   - kSampleRate / kLPCSpeechSynthFPS is 1200.0f (exact).
// One difference that does change a result, where upstream's does not have one: when the controller renders the vowel
// and consonant table (bank -1: HARMONICS in the LPC vowel range) while a word's frame index is still current (a word
// was triggered at a word bank and HARMONICS then moved down without a new trigger), upstream reads
// phonemes_[playback_frame_] past the end of its 15-entry table (memory after it) until the word's last frame; here a
// frame index outside the table ends the playback, as upstream does after the last frame (back to scan mode).
//
// Index ranges (tests/plaits/test_speech_host.py checks them on the host): the excitation pulse index is in 0..639;
// the vowel and consonant frames in 0..14; a word bank's frames in 0..num_frames - 1 of its bank; the word in
// 0..num_words - 1 and its end boundary at most num_words.
#include "lpc_speech_synth.h"
#include "units.h"
#include "speech_tables.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

/* ---- frames */

static void phoneme_frame(int i, LPCFrame *f) {
  const float *p = PL_F(pl_lpc_phonemes) + (i << 3) + (i << 2);  /* PL_LPC_FRAME = 12 floats */
  int j;
  PL_IDX(12, i)
  f->inv_period = p[0];
  f->energy = p[1];
  for (j = 0; j < kLPCOrder; ++j) {
    f->k[j] = p[2 + j];
  }
}

/* Frame i of the packed word frames (lib/speech_tables.h: the code-table index of each field). */
static void packed_frame(int i, LPCFrame *f) {
  const float *code = PL_F(pl_lpc_code);
  unsigned int a = pl_lpc_word_frames[i << 1];
  unsigned int b = pl_lpc_word_frames[(i << 1) + 1];
  f->energy = code[PL_LPC_CODE_ENERGY + (int)(a & 15u)];
  f->inv_period = code[PL_LPC_CODE_PERIOD + (int)((a >> 4) & 63u)];
  f->k[0] = code[PL_LPC_CODE_K0 + (int)((a >> 10) & 63u)];
  f->k[1] = code[PL_LPC_CODE_K1 + (int)((a >> 16) & 63u)];
  f->k[2] = code[PL_LPC_CODE_K2 + (int)((a >> 22) & 31u)];
  f->k[3] = code[PL_LPC_CODE_K3 + (int)((a >> 27) & 31u)];
  f->k[4] = code[PL_LPC_CODE_K4 + (int)(b & 31u)];
  f->k[5] = code[PL_LPC_CODE_K5 + (int)((b >> 5) & 31u)];
  f->k[6] = code[PL_LPC_CODE_K6 + (int)((b >> 10) & 31u)];
  f->k[7] = code[PL_LPC_CODE_K7 + (int)((b >> 15) & 15u)];
  f->k[8] = code[PL_LPC_CODE_K8 + (int)((b >> 19) & 15u)];
  f->k[9] = code[PL_LPC_CODE_K9 + (int)((b >> 23) & 15u)];
}

void lpc_word_frame(int bank, int frame, LPCFrame *f) {
  packed_frame(pl_lpc_bank_frame0[bank] + frame, f);
}

/* frames[i] of the controller's current table: the vowels and consonants (bank -1) or the loaded word bank. */
static void get_frame(const LPCSpeechSynthController *c, int bank, int i, LPCFrame *f) {
  if (bank == -1) {
    phoneme_frame(i, f);
  } else {
    PL_IDX(13, i)
    PL_IDX(14, c->word_bank_.num_frames_ - 1 - i)
    packed_frame(pl_lpc_bank_frame0[c->word_bank_.loaded_bank_] + i, f);
  }
}

/* ---- LPCSpeechSynth */

static void synth_init(LPCSpeechSynth *s) {
  int i;
  s->phase_ = 0.0f;
  s->frequency_ = 0.0125f;
  s->noise_energy_ = 0.0f;
  s->pulse_energy_ = 0.0f;
  s->next_sample_ = 0.0f;
  s->excitation_pulse_sample_index_ = 0;
  for (i = 0; i < kLPCOrder; ++i) {
    s->k_[i] = 0.0f;
  }
  for (i = 0; i < kLPCOrder + 1; ++i) {
    s->s_[i] = 0.0f;
  }
  s->rng_state_ = 0x21u;
}

/* PlayFrame(f1, f2, blend). */
static void play_frame(LPCSpeechSynth *s, const LPCFrame *f1, const LPCFrame *f2, float blend) {
  float frequency_1 = f1->inv_period == 0.0f ? s->frequency_ : f1->inv_period;
  float frequency_2 = f2->inv_period == 0.0f ? s->frequency_ : f2->inv_period;
  float energy_1 = f1->energy;
  float energy_2 = f2->energy;
  float noise_energy_1 = f1->inv_period == 0.0f ? energy_1 : 0.0f;
  float noise_energy_2 = f2->inv_period == 0.0f ? energy_2 : 0.0f;
  float pulse_energy_1 = f1->inv_period != 0.0f ? energy_1 : 0.0f;
  float pulse_energy_2 = f2->inv_period != 0.0f ? energy_2 : 0.0f;
  int i;
  s->frequency_ = frequency_1 + (frequency_2 - frequency_1) * blend;
  s->noise_energy_ = noise_energy_1 + (noise_energy_2 - noise_energy_1) * blend;
  s->pulse_energy_ = pulse_energy_1 + (pulse_energy_2 - pulse_energy_1) * blend;
  for (i = 0; i < kLPCOrder; ++i) {
    s->k_[i] = f1->k[i] + (f2->k[i] - f1->k[i]) * blend;
  }
}

/* PlayFrame(frames, frame, false): blend 0, so f1's values. */
static void play_one_frame(LPCSpeechSynth *s, const LPCFrame *f1) {
  int i;
  if (f1->inv_period != 0.0f) {
    s->frequency_ = f1->inv_period;
  }
  s->noise_energy_ = f1->inv_period == 0.0f ? f1->energy : 0.0f;
  s->pulse_energy_ = f1->inv_period != 0.0f ? f1->energy : 0.0f;
  for (i = 0; i < kLPCOrder; ++i) {
    s->k_[i] = f1->k[i];
  }
}

/* Random::GetWord: state * 1664525 + 1013904223 mod 2^32; 1664525 = 0x19660d = 2^20 + 2^19 + 2^16 + 2^14 + 2^13 +
 * 2^10 + 2^9 + 2^3 + 2^2 + 1. */
static unsigned int random_word(LPCSpeechSynth *s) {
  unsigned int x = s->rng_state_;
  unsigned int y = (x << 20) + (x << 19) + (x << 16) + (x << 14) + (x << 13) + (x << 10) + (x << 9) + (x << 3) +
                   (x << 2) + x;
  s->rng_state_ = y + 1013904223u;
  return s->rng_state_;
}

/* LPCSpeechSynth::Render(prosody_amount, pitch_shift, excitation, output, 1). */
static void synth_render_one(LPCSpeechSynth *s, float prosody_amount, float pitch_shift, float *excitation,
                             float *output) {
  const float *pulse = PL_F(pl_lpc_excitation_pulse);
  const float base_f0 = 0.0125f;  /* kLPCSpeechSynthDefaultF0 / 8000.0f */
  float d = s->frequency_ - base_f0;
  float f = (base_f0 + d * prosody_amount) * pitch_shift;
  float next_sample, this_sample;
  float e[11];
  unsigned int top;
  CONSTRAIN(f, 0.0f, 0.5f);

  next_sample = s->next_sample_;
  s->phase_ += f;
  this_sample = next_sample;
  next_sample = 0.0f;

  if (s->phase_ >= 1.0f) {
    float reset_time, discontinuity;
    int reset_sample;
    s->phase_ -= 1.0f;
    reset_time = s->phase_ / f;
    reset_sample = (int)(32.0f * reset_time);
    discontinuity = 0.0f;
    if (s->excitation_pulse_sample_index_ < PL_LPC_EXCITATION_PULSE_SIZE) {
      s->excitation_pulse_sample_index_ -= reset_sample;
      PL_IDX(11, s->excitation_pulse_sample_index_)
      discontinuity = pulse[s->excitation_pulse_sample_index_] * s->pulse_energy_;
    }
    this_sample += -discontinuity * ThisBlepSample(reset_time);
    next_sample += -discontinuity * NextBlepSample(reset_time);
    s->excitation_pulse_sample_index_ = reset_sample;
  }

  /* Random::GetSample() > 0: the top 16 bits as an int16 */
  top = random_word(s) >> 16;
  e[10] = (top != 0u && top < 0x8000u) ? s->noise_energy_ : -s->noise_energy_;
  if (s->excitation_pulse_sample_index_ < PL_LPC_EXCITATION_PULSE_SIZE) {
    PL_IDX(11, s->excitation_pulse_sample_index_)
    next_sample += pulse[s->excitation_pulse_sample_index_] * s->pulse_energy_;
    s->excitation_pulse_sample_index_ += 32;
  }
  e[10] += this_sample;
  e[10] *= 1.5f;

  e[9] = e[10] - s->k_[9] * s->s_[9];
  e[8] = e[9] - s->k_[8] * s->s_[8];
  e[7] = e[8] - s->k_[7] * s->s_[7];
  e[6] = e[7] - s->k_[6] * s->s_[6];
  e[5] = e[6] - s->k_[5] * s->s_[5];
  e[4] = e[5] - s->k_[4] * s->s_[4];
  e[3] = e[4] - s->k_[3] * s->s_[3];
  e[2] = e[3] - s->k_[2] * s->s_[2];
  e[1] = e[2] - s->k_[1] * s->s_[1];
  e[0] = e[1] - s->k_[0] * s->s_[0];

  CONSTRAIN(e[0], -2.0f, 2.0f);

  s->s_[9] = s->s_[8] + s->k_[8] * e[8];
  s->s_[8] = s->s_[7] + s->k_[7] * e[7];
  s->s_[7] = s->s_[6] + s->k_[6] * e[6];
  s->s_[6] = s->s_[5] + s->k_[5] * e[5];
  s->s_[5] = s->s_[4] + s->k_[4] * e[4];
  s->s_[4] = s->s_[3] + s->k_[3] * e[3];
  s->s_[3] = s->s_[2] + s->k_[2] * e[2];
  s->s_[2] = s->s_[1] + s->k_[1] * e[1];
  s->s_[1] = s->s_[0] + s->k_[0] * e[0];
  s->s_[0] = e[0];

  *excitation = e[10];
  *output = e[0];
  s->next_sample_ = next_sample;
}

/* ---- LPCSpeechSynthWordBank */

void lpc_speech_synth_word_bank_reset(LPCSpeechSynthWordBank *b) {
  b->loaded_bank_ = -1;
  b->num_frames_ = 0;
  b->num_words_ = 0;
}

/* Load(bank): 1 when the bank changed. */
static int word_bank_load(LPCSpeechSynthWordBank *b, int bank) {
  if (bank == b->loaded_bank_ || bank >= PL_LPC_NUM_BANKS) {
    return 0;
  }
  PL_IDX(16, bank)
  b->num_frames_ = pl_lpc_bank_num_frames[bank];
  b->num_words_ = pl_lpc_bank_num_words[bank];
  b->loaded_bank_ = bank;
  return 1;
}

static void get_word_boundaries(const LPCSpeechSynthWordBank *b, float address, int *start, int *end) {
  if (b->num_words_ == 0) {
    *start = -1;
    *end = -1;
  } else {
    const int *bounds = pl_lpc_word_boundaries + pl_lpc_bank_boundary0[b->loaded_bank_];
    int word = (int)(address * itof_nonneg(b->num_words_));
    if (word >= b->num_words_) {
      word = b->num_words_ - 1;
    }
    PL_IDX(15, word)
    PL_IDX(17, b->num_words_ - (word + 1))
    *start = bounds[word];
    *end = bounds[word + 1] - 1;
  }
}

/* ---- LPCSpeechSynthController */

void lpc_speech_synth_controller_init(LPCSpeechSynthController *c) {
  lpc_speech_synth_word_bank_reset(&c->word_bank_);
  c->clock_phase_ = 0.0f;
  c->playback_frame_ = -1;
  c->last_playback_frame_ = -1;
  c->remaining_frame_samples_ = 0;
  c->sample_[0] = 0.0f;
  c->sample_[1] = 0.0f;
  c->next_sample_[0] = 0.0f;
  c->next_sample_[1] = 0.0f;
  c->gain_ = 0.0f;
  synth_init(&c->synth_);
}

void lpc_speech_synth_controller_render(LPCSpeechSynthController *c, int free_running, int trigger, int bank,
                                        float frequency, float prosody_amount, float speed, float address,
                                        float formant_shift, float gain, float *excitation, float *output, int size) {
  const float rate_ratio = SemitonesToRatio((formant_shift - 0.5f) * 36.0f);
  const float rate = rate_ratio / 6.0f;
  /* All utterances have been normalized for an average f0 of 100 Hz. */
  const float pitch_shift = frequency / (rate_ratio * 100.0f / 48000.0f);
  const float time_stretch = SemitonesToRatio(-speed * 24.0f +
      (formant_shift < 0.4f ? (formant_shift - 0.4f) * -45.0f
          : (formant_shift > 0.6f ? (formant_shift - 0.6f) * -45.0f : 0.0f)));
  LPCFrame f1, f2;
  Interp gain_modulation;
  int num_frames, n;

  if (bank != -1) {
    if (word_bank_load(&c->word_bank_, bank)) {
      c->playback_frame_ = -1;
      c->last_playback_frame_ = -1;
    }
  }

  num_frames = bank == -1 ? kLPCSpeechSynthNumVowels : c->word_bank_.num_frames_;

  if (trigger) {
    if (bank == -1) {
      /* a pseudo-random consonant, played for the duration of a frame */
      int r = (int)((address + 3.0f * formant_shift + 7.0f * frequency) * 8.0f);
      while (r >= kLPCSpeechSynthNumConsonants) {
        r -= kLPCSpeechSynthNumConsonants;
      }
      c->playback_frame_ = r + kLPCSpeechSynthNumVowels;
      c->last_playback_frame_ = c->playback_frame_ + 1;
    } else {
      get_word_boundaries(&c->word_bank_, address, &c->playback_frame_, &c->last_playback_frame_);
    }
    c->remaining_frame_samples_ = 0;
  }

  /* port: a word's frame index outside the vowel and consonant table ends the playback (see the top) */
  if (bank == -1 && c->playback_frame_ >= kLPCSpeechSynthNumPhonemes) {
    c->playback_frame_ = -1;
  }

  if (c->playback_frame_ == -1 && c->remaining_frame_samples_ == 0) {
    float frame = address * (itof_nonneg(num_frames) - 1.0001f);
    MAKE_INTEGRAL_FRACTIONAL(frame)
    get_frame(c, bank, frame_integral, &f1);
    get_frame(c, bank, frame_integral + 1, &f2);
    play_frame(&c->synth_, &f1, &f2, frame_fractional);
  } else {
    if (c->remaining_frame_samples_ == 0) {
      get_frame(c, bank, c->playback_frame_, &f1);
      play_one_frame(&c->synth_, &f1);
      c->remaining_frame_samples_ = (int)(1200.0f * time_stretch);  /* kSampleRate / kLPCSpeechSynthFPS */
      ++c->playback_frame_;
      if (c->playback_frame_ >= c->last_playback_frame_) {
        int back_to_scan_mode = bank == -1 || free_running;
        c->playback_frame_ = back_to_scan_mode ? -1 : c->last_playback_frame_;
      }
    }
    c->remaining_frame_samples_ -= size < c->remaining_frame_samples_ ? size : c->remaining_frame_samples_;
  }

  gain_modulation = interp(&c->gain_, gain, size);
  for (n = 0; n < size; ++n) {
    float this_sample_0 = c->next_sample_[0];
    float this_sample_1 = c->next_sample_[1];
    float g;
    c->next_sample_[0] = 0.0f;
    c->next_sample_[1] = 0.0f;

    c->clock_phase_ += rate;
    if (c->clock_phase_ >= 1.0f) {
      float reset_time, new_sample_0, new_sample_1, discontinuity_0, discontinuity_1;
      c->clock_phase_ -= 1.0f;
      reset_time = c->clock_phase_ / rate;
      synth_render_one(&c->synth_, prosody_amount, pitch_shift, &new_sample_0, &new_sample_1);
      discontinuity_0 = new_sample_0 - c->sample_[0];
      discontinuity_1 = new_sample_1 - c->sample_[1];
      this_sample_0 += discontinuity_0 * ThisBlepSample(reset_time);
      c->next_sample_[0] += discontinuity_0 * NextBlepSample(reset_time);
      this_sample_1 += discontinuity_1 * ThisBlepSample(reset_time);
      c->next_sample_[1] += discontinuity_1 * NextBlepSample(reset_time);
      c->sample_[0] = new_sample_0;
      c->sample_[1] = new_sample_1;
    }
    c->next_sample_[0] += c->sample_[0];
    c->next_sample_[1] += c->sample_[1];
    g = next(&gain_modulation);
    excitation[n] = this_sample_0 * g;
    output[n] = this_sample_1 * g;
  }
  commit(&gain_modulation);
}
