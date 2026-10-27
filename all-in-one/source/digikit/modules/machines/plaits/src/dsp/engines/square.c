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
// PLAITS for the Digitakt II: SQUARE (model 23), see square.h. The always-clocked branch of ChiptuneEngine::Render with
// Arpeggiator, SuperSquareOscillator and NESTriangleOscillator<5>.
// Differences from upstream, none of them in the audio on the host:
//   - the pitch reference is exactly 48,000 Hz (lib/units.h NoteToFrequency);
//   - clocked only (see square.h): the unclocked chord branch, the envelope and `already_enveloped` are not ported;
//   - bool is int; int-to-float conversions go through itof_nonneg; the arpeggiator's integer / and % are shifts, masks
//     and conditional subtractions (patchkit links no division routine): pattern / 3 and pattern % 3 for 0..11, the
//     random octave % range (1, 2 or 4) is a mask, the random note % num_notes (1..4) a mask, or for 3 a sum of base-4
//     digits (4 = 1 mod 3) reduced by subtraction;
//   - the oscillators are block loops with their state in locals (loaded once, stored once) and no calls on the
//     per-sample path (only the rare band-limiting steps call the blep helpers); NESTriangleOscillator<5>'s constants
//     are folded (num_steps 32, half 16, top 31, scale 4 / 30 = 0.13333334f), and the division by `scale` is skipped
//     while fade_to_tri is 0, where it is exactly 0;
//   - a float tested for non-zero is written `x != 0.0f`: a bare `if (x)` compiles to a compare shape the encoding gate
//     refuses (PORTING drift 22).
// Index ranges: the NES triangle's step is in [0, 31] (table 32); the arpeggiator's note is in [0, num_notes) and its octave in [0, range) (recorded, tables 30 and 31;
// range <= 4, so 1 << octave <= 8); chord_bank reads its ratio rows by the chord index in [0, 10]. The arpeggiator state
// is bounded after every Clock whatever the previous chord (tests/plaits/test_square_host.py steps it through chord and
// pattern changes against upstream).
#include "square.h"
#include "../lib/units.h"
#include "../lib/drums.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#define ARP_UP 0
#define ARP_DOWN 1
#define ARP_UP_DOWN 2
#define ARP_RANDOM 3

static float NextIntegratedBlepSample(float t) {
  const float t1 = 0.5f * t;
  const float t2 = t1 * t1;
  const float t4 = t2 * t2;
  return 0.1875f - t1 + 1.5f * t2 - t4;
}

static float ThisIntegratedBlepSample(float t) { return NextIntegratedBlepSample(1.0f - t); }

static void ss_init(SquareOsc *s) {
  s->master_phase_ = 0.0f;
  s->slave_phase_ = 0.0f;
  s->next_sample_ = 0.0f;
  s->high_ = 0;
  s->master_frequency_ = 0.0f;
  s->slave_frequency_ = 0.01f;
}

/* SuperSquareOscillator::Render */
static void ss_render(SquareOsc *s, float frequency, float shape, float *out, int size) {
  float master_frequency = frequency;
  Interp master_fm, fm;
  float mv, mi, sv, si, master_phase, slave_phase, next_sample;
  int high, i;
  frequency *= shape < 0.5f
      ? (0.51f + 0.98f * shape)
      : 1.0f + 16.0f * (shape - 0.5f) * (shape - 0.5f);
  if (master_frequency >= kMaxFrequency) {
    master_frequency = kMaxFrequency;
  }
  if (frequency >= kMaxFrequency) {
    frequency = kMaxFrequency;
  }
  master_fm = interp(&s->master_frequency_, master_frequency, size);
  fm = interp(&s->slave_frequency_, frequency, size);
  mv = master_fm.value; mi = master_fm.increment;
  sv = fm.value; si = fm.increment;
  master_phase = s->master_phase_;
  slave_phase = s->slave_phase_;
  next_sample = s->next_sample_;
  high = s->high_;
  for (i = 0; i < size; ++i) {
    int reset = 0;
    int transition_during_reset = 0;
    float reset_time = 0.0f;
    float this_sample = next_sample;
    float mf, sf;
    next_sample = 0.0f;
    mv += mi;
    sv += si;
    mf = mv;
    sf = sv;
    master_phase += mf;
    if (master_phase >= 1.0f) {
      float slave_phase_at_reset, value;
      master_phase -= 1.0f;
      reset_time = master_phase / mf;
      slave_phase_at_reset = slave_phase + (1.0f - reset_time) * sf;
      reset = 1;
      if (slave_phase_at_reset >= 1.0f) {
        slave_phase_at_reset -= 1.0f;
        transition_during_reset = 1;
      }
      if (!high && slave_phase_at_reset >= 0.5f) {
        transition_during_reset = 1;
      }
      value = slave_phase_at_reset < 0.5f ? 0.0f : 1.0f;
      this_sample -= value * ThisBlepSample(reset_time);
      next_sample -= value * NextBlepSample(reset_time);
    }
    slave_phase += sf;
    while (transition_during_reset || !reset) {
      float t;
      if (!high) {
        if (slave_phase < 0.5f) {
          break;
        }
        t = (slave_phase - 0.5f) / sf;
        this_sample += ThisBlepSample(t);
        next_sample += NextBlepSample(t);
        high = 1;
      }
      if (high) {
        if (slave_phase < 1.0f) {
          break;
        }
        slave_phase -= 1.0f;
        t = slave_phase / sf;
        this_sample -= ThisBlepSample(t);
        next_sample -= NextBlepSample(t);
        high = 0;
      }
    }
    if (reset) {
      slave_phase = reset_time * sf;
      high = 0;
    }
    next_sample += slave_phase < 0.5f ? 0.0f : 1.0f;
    out[i] = 2.0f * this_sample - 1.0f;
  }
  s->master_phase_ = master_phase;
  s->slave_phase_ = slave_phase;
  s->next_sample_ = next_sample;
  s->high_ = high;
  s->master_frequency_ = mv;
  s->slave_frequency_ = sv;
}

/* The 5-bit triangle's levels min(step, 31 - step) for step 0..31, as floats (positive values: a plain initialiser is
 * right; drift 2 is about negative entries). Replaces a call of itof_nonneg per sample. */
static const float nes_level[32] = {
  0.0f, 1.0f, 2.0f, 3.0f, 4.0f, 5.0f, 6.0f, 7.0f, 8.0f, 9.0f, 10.0f, 11.0f, 12.0f, 13.0f, 14.0f, 15.0f,
  15.0f, 14.0f, 13.0f, 12.0f, 11.0f, 10.0f, 9.0f, 8.0f, 7.0f, 6.0f, 5.0f, 4.0f, 3.0f, 2.0f, 1.0f, 0.0f,
};

static void nes_init(NesTriOsc *s) {
  s->phase_ = 0.0f;
  s->step_ = 0;
  s->ascending_ = 1;
  s->next_sample_ = 0.0f;
  s->frequency_ = 0.001f;
}

/* NESTriangleOscillator<5>::Render: num_steps 32, half 16, top 31, scale 4 / (top - 1). */
static void nes_render(NesTriOsc *s, float frequency, float *out, int size) {
  const float scale = 0.13333334f;
  Interp fm;
  float fv, fi, phase, next_sample;
  int step, ascending, i;
  if (frequency > 0.25f) {
    frequency = 0.25f;
  }
  fm = interp(&s->frequency_, frequency, size);
  fv = fm.value; fi = fm.increment;
  phase = s->phase_;
  next_sample = s->next_sample_;
  step = s->step_;
  ascending = s->ascending_;
  for (i = 0; i < size; ++i) {
    float f, fade_to_tri, nes_gain, tri_gain, this_sample;
    int next_step;
    fv += fi;
    f = fv;
    phase += f;
    fade_to_tri = (f - 0.5f / 32.0f) * 2.0f * 32.0f;
    CONSTRAIN(fade_to_tri, 0.0f, 1.0f)
    nes_gain = 1.0f - fade_to_tri;
    tri_gain = fade_to_tri == 0.0f ? 0.0f : fade_to_tri * 2.0f / scale;
    this_sample = next_sample;
    next_sample = 0.0f;
    if (ascending && phase >= 0.5f) {
      float discontinuity = 4.0f * f * tri_gain;
      if (discontinuity != 0.0f) {
        float t = (phase - 0.5f) / f;
        this_sample -= ThisIntegratedBlepSample(t) * discontinuity;
        next_sample -= NextIntegratedBlepSample(t) * discontinuity;
      }
      ascending = 0;
    }
    next_step = (int)(phase * 32.0f);
    if (next_step != step) {
      int wrap = 0;
      float discontinuity;
      if (next_step >= 32) {
        phase -= 1.0f;
        next_step -= 32;
        wrap = 1;
      }
      discontinuity = next_step < 16 ? 1.0f : -1.0f;
      if (next_step == 0 || next_step == 16) {
        discontinuity = 0.0f;
      }
      discontinuity *= nes_gain;
      if (discontinuity != 0.0f) {
        float frac = (phase * 32.0f - itof_nonneg(next_step));
        float t = frac / (f * 32.0f);
        this_sample += ThisBlepSample(t) * discontinuity;
        next_sample += NextBlepSample(t) * discontinuity;
      }
      if (wrap) {
        float d = 4.0f * f * tri_gain;
        if (d != 0.0f) {
          float t = phase / f;
          this_sample += ThisIntegratedBlepSample(t) * d;
          next_sample += NextIntegratedBlepSample(t) * d;
        }
        ascending = 1;
      }
    }
    step = next_step;
    PL_IDX(32, step)
    next_sample += nes_gain * nes_level[step];
    next_sample += tri_gain * (phase < 0.5f ? 2.0f * phase : 2.0f - 2.0f * phase);
    out[i] = this_sample * scale - 1.0f;
  }
  s->phase_ = phase;
  s->next_sample_ = next_sample;
  s->step_ = step;
  s->ascending_ = ascending;
  s->frequency_ = fv;
}

static void arp_init(SquareArp *a) {
  a->mode_ = ARP_UP;
  a->note_ = 0;
  a->octave_ = 0;
  a->direction_ = 1;
  a->range_ = 0;
}

/* Arpeggiator::Clock */
static void arp_clock(SquareArp *a, unsigned int *rng, int num_notes) {
  int done;
  if (num_notes == 1 && a->range_ == 1) {
    a->note_ = a->octave_ = 0;
    return;
  }
  if (a->mode_ == ARP_RANDOM) {
    for (;;) {
      unsigned int w = rng_word(rng);
      int octave = (int)((w >> 4) & (unsigned int)(a->range_ - 1));  /* range_ is 1, 2 or 4 */
      int x = (int)(w >> 20);
      int note;
      if (num_notes == 3) {
        note = (x & 3) + ((x >> 2) & 3) + ((x >> 4) & 3) + ((x >> 6) & 3) + ((x >> 8) & 3) + ((x >> 10) & 3);
        while (note >= 3) {
          note -= 3;
        }
      } else {
        note = x & (num_notes - 1);  /* num_notes is 1, 2 or 4 */
      }
      if (octave != a->octave_ || note != a->note_) {
        a->octave_ = octave;
        a->note_ = note;
        break;
      }
    }
    return;
  }
  if (a->mode_ == ARP_UP) {
    a->direction_ = 1;
  }
  if (a->mode_ == ARP_DOWN) {
    a->direction_ = -1;
  }
  a->note_ += a->direction_;
  done = 0;
  while (!done) {
    done = 1;
    if (a->note_ >= num_notes || a->note_ < 0) {
      a->octave_ += a->direction_;
      a->note_ = a->direction_ > 0 ? 0 : num_notes - 1;
    }
    if (a->octave_ >= a->range_ || a->octave_ < 0) {
      a->octave_ = a->direction_ > 0 ? 0 : a->range_ - 1;
      if (a->mode_ == ARP_UP_DOWN) {
        a->direction_ = -a->direction_;
        a->note_ = a->direction_ > 0 ? 1 : num_notes - 2;
        a->octave_ = a->direction_ > 0 ? 0 : a->range_ - 1;
        done = 0;
      }
    }
  }
}

void square_init(Square *s) {
  ss_init(&s->voice_);
  nes_init(&s->bass_);
  chord_bank_init(&s->chords_);
  arp_init(&s->arp_);
  hysteresis_quantizer2_init(&s->pattern_selector_, 12, 0.075f, 0);
  s->rng_ = 0x21u;
  chord_bank_reset(&s->chords_);  /* ChiptuneEngine::Reset */
}

void square_render(Square *s, const Params *p, float *out, float *aux, int size) {
  const float f0 = NoteToFrequency(p->note);
  const float shape = p->morph * 0.995f;
  float octave, note_f0, root_transposition;
  if (p->trigger & TRIGGER_RISING_EDGE) {
    int pattern, mode, rem;
    chord_bank_set_chord(&s->chords_, p->harmonics);
    chord_bank_sort(&s->chords_);
    pattern = hysteresis_quantizer2_process(&s->pattern_selector_, 0, p->timbre);
    mode = pattern >= 9 ? 3 : pattern >= 6 ? 2 : pattern >= 3 ? 1 : 0;  /* pattern / 3 */
    rem = pattern - ((mode << 1) + mode);                               /* pattern % 3 */
    s->arp_.mode_ = mode;
    s->arp_.range_ = 1 << rem;
    arp_clock(&s->arp_, &s->rng_, chord_bank_num_notes(&s->chords_));
  }
  PL_IDX(30, s->arp_.note_)
  PL_IDX(31, s->arp_.octave_)
  octave = itof_nonneg(1 << s->arp_.octave_);
  note_f0 = f0 * chord_bank_sorted_ratio(&s->chords_, s->arp_.note_) * octave;
  root_transposition = octave;
  ss_render(&s->voice_, note_f0, shape, out, size);
  nes_render(&s->bass_, f0 * 0.5f * root_transposition, aux, size);
}
