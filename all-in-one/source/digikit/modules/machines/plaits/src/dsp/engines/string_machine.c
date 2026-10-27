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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' StringMachineEngine (model STRING MACHINE) in C99, from
// plaits/dsp/engine2/string_machine_engine.cc. Differences from upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - string_machine_init is Init and Reset; the ensemble's 1024-float buffer is part of the state (upstream takes it
//     from the arena, then Reset clears it), with the same length and the same circular-buffer semantics;
//   - the registrations are the generated bit-pattern table of engines/string_machine_tables.h;
//     (kRegistrationTableSize - 1.001f) is written as its float value, 9.999f (11.0f - 1.001f rounds to the same float);
//   - the two NaiveSvf are one pair of arrays and their per-sample low-pass (resonance 1.0: damp = 1.0f, so bp * damp is bp, exactly) is one
//     loop over the block with the filter state in registers, the same operations in the same order per filter;
//   - the registration row is read at row * 6 as shifts (Selache's C compiles an integer multiply by 6 to a form the
//     encoding gate refuses).
// Index ranges: registration is in [0, 1] (MORPH is clamped by the wrapper, then low-passed), so
// registration_integral <= 9 and the second row read <= 10 (11 rows); SemitonesToRatio(120 * timbre) is within the
// pitch tables for timbre in [0, 1]. The oscillators and the ensemble keep their own ranges (lib/). The sustained
// string/organ voices ignore the trigger and the accent, as upstream's engine does.
#include "string_machine.h"
#include "../lib/units.h"
#include "string_machine_tables.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_SM_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: the mixdown filters and the ensemble run per sample in lib/string_machine_sm.s, bit-identical in output
   to the C below, which stays for the host and as the -DPL_SM_C reference (tests/plaits/test_string_machine_kernel.py). */
#define PL_SM_KERNEL 1
void sm_tail_kernel(float *svf, Ensemble *e, const float *k, float *out, float *aux, int size);
#endif

#define kChordNumHarmonics 3
#define kRegistrationTableSize 11

void string_machine_init(StringMachine *s) {
  int i;
  for (i = 0; i < kChordNumNotes; ++i) {
    string_synth_oscillator_init(&s->divide_down_voice_[i]);
  }
  chord_bank_init(&s->chords_);
  s->morph_lp_ = 0.0f;
  s->timbre_lp_ = 0.0f;
  for (i = 0; i < 2; ++i) {
    /* NaiveSvf::Init: set_f_q<FREQUENCY_DIRTY>(0.01f, 100.0f), Reset */
    s->svf_f_[i] = (2.0f * 3.14159265358979323846f) * 0.01f;
    s->svf_lp_[i] = 0.0f;
    s->svf_bp_[i] = 0.0f;
  }
  ensemble_init(&s->ensemble_);
  chord_bank_reset(&s->chords_);
}

static void compute_registration(float registration, float *amplitudes) {
  const float *registrations = PL_F(string_machine_registrations);
  int i;
  registration *= 9.999f;  /* (kRegistrationTableSize - 1.001f) */
  {
    MAKE_INTEGRAL_FRACTIONAL(registration)
    PL_IDX(33, registration_integral)
    PL_IDX(33, registration_integral + 1)
    const float *row = registrations + (registration_integral << 2) + (registration_integral << 1);
    for (i = 0; i < kChordNumHarmonics * 2; ++i) {
      float a = row[i];
      float b = row[6 + i];
      amplitudes[i] = a + (b - a) * registration_fractional;
    }
  }
}

void string_machine_render(StringMachine *s, const Params *p, float *out, float *aux, int size) {
  float harmonics[kChordNumHarmonics * 2 + 2];
  float registration, f0, cutoff, cutoff_1, cutoff_2, amount, depth;
  float f_0, f_1, lp_0, lp_1, bp_0, bp_1;
  int note, i;

  ONE_POLE(s->morph_lp_, p->morph, 0.1f);
  ONE_POLE(s->timbre_lp_, p->timbre, 0.1f);

  chord_bank_set_chord(&s->chords_, p->harmonics);

  registration = maxf(s->morph_lp_, 0.0f);
  compute_registration(registration, harmonics);
  harmonics[kChordNumHarmonics * 2] = 0.0f;
  harmonics[kChordNumHarmonics * 2 + 1] = 0.0f;  /* never read (upstream leaves it unset) */

  /* Render string/organ sound. */
  for (i = 0; i < size; ++i) {
    out[i] = 0.0f;
    aux[i] = 0.0f;
  }
  f0 = NoteToFrequency(p->note) * 0.998f;
  for (note = 0; note < kChordNumNotes; ++note) {
    float note_f0 = f0 * chord_bank_ratio(&s->chords_, note);
    float divide_down_gain = 4.0f - note_f0 * 32.0f;
    CONSTRAIN(divide_down_gain, 0.0f, 1.0f);
    string_synth_oscillator_render(
        &s->divide_down_voice_[note],
        note_f0,
        harmonics,
        0.25f * divide_down_gain,
        (note & 1) != 0 ? aux : out,
        size);
  }

  /* Pass through VCF: set_f_q<FREQUENCY_DIRTY>(cutoff, 1.0f), then the 1.5 times higher one. */
  cutoff = 2.2f * f0 * SemitonesToRatio(120.0f * p->timbre);
  cutoff_1 = cutoff;
  if (!(cutoff_1 < 0.158f)) cutoff_1 = 0.158f;
  cutoff_2 = cutoff * 1.5f;
  if (!(cutoff_2 < 0.158f)) cutoff_2 = 0.158f;
  s->svf_f_[0] = (2.0f * 3.14159265358979323846f) * cutoff_1;
  s->svf_f_[1] = (2.0f * 3.14159265358979323846f) * cutoff_2;

#ifdef PL_SM_KERNEL
  {
    float k[6];
    amount = absf(p->timbre - 0.5f) * 2.0f;
    depth = 0.35f + 0.65f * p->timbre;
    k[0] = s->svf_f_[0];
    k[1] = s->svf_f_[1];
    k[2] = amount;
    k[3] = 1.0f - amount * 0.5f;  /* ensemble_process's dry_amount */
    k[4] = depth * 160.0f;        /* a */
    k[5] = depth * 16.0f;         /* b */
    (void)f_0;
    (void)f_1;
    (void)lp_0;
    (void)lp_1;
    (void)bp_0;
    (void)bp_1;
    sm_tail_kernel(&s->svf_lp_[0], &s->ensemble_, k, out, aux, size);
    return;
  }
#endif
  /* Mixdown. */
  f_0 = s->svf_f_[0];
  f_1 = s->svf_f_[1];
  lp_0 = s->svf_lp_[0];
  lp_1 = s->svf_lp_[1];
  bp_0 = s->svf_bp_[0];
  bp_1 = s->svf_bp_[1];
  for (i = 0; i < size; ++i) {
    float l, r;
    float notch_0 = out[i] - bp_0;
    float notch_1 = aux[i] - bp_1;
    lp_0 += f_0 * bp_0;
    lp_1 += f_1 * bp_1;
    bp_0 += f_0 * (notch_0 - lp_0);
    bp_1 += f_1 * (notch_1 - lp_1);
    l = lp_0;
    r = lp_1;
    out[i] = 0.66f * l + 0.33f * r;
    aux[i] = 0.66f * r + 0.33f * l;
  }
  s->svf_lp_[0] = lp_0;
  s->svf_lp_[1] = lp_1;
  s->svf_bp_[0] = bp_0;
  s->svf_bp_[1] = bp_1;

  /* Ensemble FX. */
  amount = absf(p->timbre - 0.5f) * 2.0f;
  depth = 0.35f + 0.65f * p->timbre;
  ensemble_process(&s->ensemble_, amount, depth, out, aux, size);
}
