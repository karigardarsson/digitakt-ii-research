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
// PLAITS for the Digitakt II: Plaits' ChordEngine (model CHORD) in C99, from plaits/dsp/engine/chord_engine.cc.
// Differences from upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - chord_init is Init and Reset (upstream calls Reset when the engine is selected);
//   - the registrations, fade points and wave list are the generated bit-pattern tables of engines/chord_tables.h;
//     the wave list holds offsets into lib/waves.h's float wave table instead of pointers;
//   - (kRegistrationTableSize - 1.001f) is written as its float value, 6.999f (8.0f - 1.001f rounds to the same
//     float, 0x40dff7cf).
// Index ranges: registration is in [0, 1], so registration_integral <= 6 and the second row read <= 7 (8 rows);
// chord_bank_compute_chord_inversion writes voices 0..4 only; the wavetable voices read waves 0..14 (lib/
// wavetable_oscillator.c). tests/plaits/test_wavetable_chord_host.py compares the engine bit for bit with upstream at
// 48,000 Hz and checks these ranges at the corners.
#include "chord.h"
#include "../lib/units.h"
#include "../lib/waves.h"
#include "chord_tables.h"

#if defined(__ADSP21569__) && !defined(PL_CHORD_C)
/* The DSP build: the two buffer loops of chord_render run in lib/chord_loops_sm.s, bit-identical to the C loops below,
   which stay for the host and as the -DPL_CHORD_C reference (tests/plaits/test_chord_kernels.py). */
#define PL_CHORD_KERNELS 1
void chord_clear_kernel(float *out, float *aux, int size);
void chord_mix_kernel(float *out, float *aux, int size);
#endif

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#define kChordNumHarmonics 3

void chord_init(Chord *s) {
  int i;
  for (i = 0; i < kChordNumVoices; ++i) {
    string_synth_oscillator_init(&s->divide_down_voice_[i]);
    wavetable_oscillator_init(&s->wavetable_voice_[i]);
  }
  chord_bank_init(&s->chords_);

  s->morph_lp_ = 0.0f;
  s->timbre_lp_ = 0.0f;
  chord_bank_reset(&s->chords_);
}

static void compute_registration(float registration, float *amplitudes) {
  const float *registrations = PL_F(chord_registrations);
  int i;
  registration *= 6.999f;  /* (kRegistrationTableSize - 1.001f) */
  {
    MAKE_INTEGRAL_FRACTIONAL(registration)
    PL_IDX(4, registration_integral)
    PL_IDX(4, registration_integral + 1)
    /* row * 6 as shifts: Selache's C compiles an integer multiply by 6 to a multiplier form the encoding gate
     * refuses (in neither stock code nor encoding_proven.toml) */
    const float *row = registrations + (registration_integral << 2) + (registration_integral << 1);
    for (i = 0; i < kChordNumHarmonics * 2; ++i) {
      float a = row[i];
      float b = row[6 + i];
      amplitudes[i] = a + (b - a) * registration_fractional;
    }
  }
}

void chord_render(Chord *s, const Params *p, float *out, float *aux, int size) {
  const float *fade_point = PL_F(chord_fade_point);
  float harmonics[kChordNumHarmonics * 2 + 2];
  float note_amplitudes[kChordNumVoices];
  float ratios[kChordNumVoices];
  float registration, f0, waveform;
  int aux_note_mask, note, i;

  ONE_POLE(s->morph_lp_, p->morph, 0.1f);
  ONE_POLE(s->timbre_lp_, p->timbre, 0.1f);

  chord_bank_set_chord(&s->chords_, p->harmonics);

  registration = maxf(1.0f - s->morph_lp_ * 2.15f, 0.0f);

  compute_registration(registration, harmonics);
  harmonics[kChordNumHarmonics * 2] = 0.0f;
  harmonics[kChordNumHarmonics * 2 + 1] = 0.0f;  /* never read (upstream leaves it unset) */

  aux_note_mask = chord_bank_compute_chord_inversion(
      &s->chords_,
      s->timbre_lp_,
      ratios,
      note_amplitudes);

#ifdef PL_CHORD_KERNELS
  chord_clear_kernel(out, aux, size);
#else
  for (i = 0; i < size; ++i) {
    out[i] = 0.0f;
    aux[i] = 0.0f;
  }
#endif

  f0 = NoteToFrequency(p->note) * 0.998f;
  waveform = maxf((s->morph_lp_ - 0.535f) * 2.15f, 0.0f);

  for (note = 0; note < kChordNumVoices; ++note) {
    float wavetable_amount = 50.0f * (s->morph_lp_ - fade_point[note]);
    float divide_down_amount, note_f0, divide_down_gain;
    float *destination;
    CONSTRAIN(wavetable_amount, 0.0f, 1.0f);

    divide_down_amount = 1.0f - wavetable_amount;
    destination = (1 << note) & aux_note_mask ? aux : out;

    note_f0 = f0 * ratios[note];
    divide_down_gain = 4.0f - note_f0 * 32.0f;
    CONSTRAIN(divide_down_gain, 0.0f, 1.0f);
    divide_down_amount *= divide_down_gain;

    if (wavetable_amount != 0.0f) {
      wavetable_oscillator_render(
          &s->wavetable_voice_[note],
          note_f0 * 1.004f,
          note_amplitudes[note] * wavetable_amount,
          waveform,
          PL_F(pl_wav_integrated_waves),
          chord_waves,
          15,
          destination,
          size);
    }

    if (divide_down_amount != 0.0f) {
      string_synth_oscillator_render(
          &s->divide_down_voice_[note],
          note_f0,
          harmonics,
          note_amplitudes[note] * divide_down_amount,
          destination,
          size);
    }
  }

#ifdef PL_CHORD_KERNELS
  chord_mix_kernel(out, aux, size);
#else
  for (i = 0; i < size; ++i) {
    out[i] += aux[i];
    aux[i] *= 3.0f;
  }
#endif
}
