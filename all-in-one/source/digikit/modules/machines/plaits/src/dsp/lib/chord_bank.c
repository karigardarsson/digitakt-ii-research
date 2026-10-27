// Copyright 2021 Emilie Gillet.
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
// PLAITS for the Digitakt II: Plaits' ChordBank and stmlib's HysteresisQuantizer2 in C99 (lib/chord_bank.h).
// Differences from upstream, none of which changes a result on the host:
//   - the ratios and note counts are the generated constants of lib/chord_bank_tables.h (upstream: Reset computes
//     them with SemitonesToRatio); Reset sorts, as upstream's;
//   - integer division and modulo are written as shifts, masks and a conditional subtraction (Selache's C calls a
//     library routine for them that patchkit does not link): every operand is non-negative and the divisors are 4
//     and 5, so the results are equal;
//   - std::sort of the four sorted ratios is an insertion sort (equal results for four floats);
//   - int-to-float conversions go through dsp_util.h `itof_nonneg` (every value is non-negative).
#include "chord_bank.h"
#include "units.h"
#include "chord_bank_tables.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

void hysteresis_quantizer2_init(HysteresisQuantizer2 *q, int num_steps, float hysteresis, int symmetric) {
  q->num_steps_ = num_steps;
  q->hysteresis_ = hysteresis;

  q->scale_ = itof_nonneg(symmetric ? num_steps - 1 : num_steps);
  q->offset_ = symmetric ? 0.0f : -0.5f;

  q->quantized_value_ = 0;
}

int hysteresis_quantizer2_process(HysteresisQuantizer2 *q, int base, float value) {
  float hysteresis_sign;
  int r;
  value *= q->scale_;
  value += q->offset_;
  value += itof_nonneg(base);

  hysteresis_sign = value > itof_nonneg(q->quantized_value_) ? -1.0f : +1.0f;
  r = (int)(value + hysteresis_sign * q->hysteresis_ + 0.5f);
  CONSTRAIN(r, 0, q->num_steps_ - 1)
  q->quantized_value_ = r;
  return r;
}

void chord_bank_init(ChordBank *b) {
  hysteresis_quantizer2_init(&b->chord_index_quantizer_, kChordNumChords, 0.075f, 0);
}

void chord_bank_reset(ChordBank *b) {
  chord_bank_sort(b);
}

int chord_bank_chord_index(const ChordBank *b) {
  return b->chord_index_quantizer_.quantized_value_;
}

const float *chord_bank_ratios(const ChordBank *b) {
  PL_IDX(5, chord_bank_chord_index(b) * kChordNumNotes)
  PL_IDX(5, chord_bank_chord_index(b) * kChordNumNotes + kChordNumNotes - 1)
  return PL_F(chord_ratios) + chord_bank_chord_index(b) * kChordNumNotes;
}

float chord_bank_ratio(const ChordBank *b, int note) {
  return chord_bank_ratios(b)[note];
}

float chord_bank_sorted_ratio(const ChordBank *b, int note) {
  return b->sorted_ratios_[note];
}

int chord_bank_num_notes(const ChordBank *b) {
  return chord_note_count[chord_bank_chord_index(b)];
}

void chord_bank_sort(ChordBank *b) {
  int i, j;
  for (i = 0; i < kChordNumNotes; ++i) {
    float r = chord_bank_ratio(b, i);
    while (r > 2.0f) {
      r *= 0.5f;
    }
    b->sorted_ratios_[i] = r;
  }
  for (i = 1; i < kChordNumNotes; ++i) {
    float r = b->sorted_ratios_[i];
    for (j = i; j > 0 && r < b->sorted_ratios_[j - 1]; --j) {
      b->sorted_ratios_[j] = b->sorted_ratios_[j - 1];
    }
    b->sorted_ratios_[j] = r;
  }
}

void chord_bank_set_chord(ChordBank *b, float parameter) {
  hysteresis_quantizer2_process(&b->chord_index_quantizer_, 0, parameter * 1.02f);
}

int chord_bank_compute_chord_inversion(const ChordBank *b, float inversion, float *ratios, float *amplitudes) {
  const float *base_ratio = chord_bank_ratios(b);
  const float kBaseGain = 0.25f;
  int num_rotations, rotated_note, mask, i;
  inversion = inversion * 20.0f;  /* float(kChordNumNotes * kChordNumVoices) */

  MAKE_INTEGRAL_FRACTIONAL(inversion)

  num_rotations = inversion_integral >> 2;  /* / kChordNumNotes */
  rotated_note = inversion_integral & 3;    /* % kChordNumNotes */

  mask = 0;

  for (i = 0; i < kChordNumNotes; ++i) {
    float transposition = 0.25f * itof_nonneg(
        1 << ((kChordNumNotes - 1 + inversion_integral - i) >> 2));
    int target_voice = i - num_rotations + kChordNumVoices;  /* % kChordNumVoices: 0 <= it < 2 * 5 */
    int previous_voice;
    if (target_voice >= kChordNumVoices) {
      target_voice -= kChordNumVoices;
    }
    previous_voice = target_voice == 0 ? kChordNumVoices - 1 : target_voice - 1;
    PL_IDX(6, target_voice)
    PL_IDX(6, previous_voice)

    if (i == rotated_note) {
      ratios[target_voice] = base_ratio[i] * transposition;
      ratios[previous_voice] = ratios[target_voice] * 2.0f;
      amplitudes[previous_voice] = kBaseGain * inversion_fractional;
      amplitudes[target_voice] = kBaseGain * (1.0f - inversion_fractional);
    } else if (i < rotated_note) {
      ratios[previous_voice] = base_ratio[i] * transposition;
      amplitudes[previous_voice] = kBaseGain;
    } else {
      ratios[target_voice] = base_ratio[i] * transposition;
      amplitudes[target_voice] = kBaseGain;
    }

    if (i == 0) {
      if (i >= rotated_note) {
        mask |= 1 << target_voice;
      }
      if (i <= rotated_note) {
        mask |= 1 << previous_voice;
      }
    }
  }
  return mask;
}
