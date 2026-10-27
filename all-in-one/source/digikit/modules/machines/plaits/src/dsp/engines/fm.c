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
// PLAITS for the Digitakt II: Plaits' FMEngine (model 2FM) in C99, from plaits/dsp/engine/fm_engine.cc; OUT follows
// the dt2-machine-lab translation (MIT, the notice above), the AUX sub-oscillator is from upstream directly.
// Differences from upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - the downsampler states (carrier_fir_, sub_fir_) are set to 0 by fm_init; upstream leaves them as the engine's
//     memory holds them (zero at power-up), and here the engine's state is shared with the other models;
//   - float <-> uint32 conversions go through lib/sine.h `ftou` / `utof` (equal to the casts), and the tables are the
//     shared bit-pattern tables of lib/tables.h.
// The host test tests/plaits/test_fm_grain_additive_host.py compares it bit for bit with upstream at 48,000 Hz.
#include "fm.h"
#include "../lib/sine.h"

#if defined(__ADSP21569__) && !defined(PL_FM_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: fm_render's sample loop runs in lib/fm_sm.s, bit-identical to the C loop below, which stays for the host
   and as the -DPL_FM_C reference (tests/plaits/test_fm_kernel.py). */
#define PL_FM_KERNEL 1
void fm_kernel(FM *s, const float *k, float *out, float *aux, int size);
#endif

/* plaits::Downsampler (4x): the state is written back by down_commit (its destructor); Accumulate is written out in
 * fm_render's loop. */
typedef struct { float head_, tail_; float *state_; } Down;
static Down down_init(float *p) { Down s; s.head_ = *p; s.tail_ = 0.0f; s.state_ = p; return s; }
static float down_read(Down *s) { float v = s->head_; s->head_ = s->tail_; s->tail_ = 0.0f; return v; }
static void down_commit(Down *s) { *s->state_ = s->head_; }

void fm_init(FM *s) {
  s->carrier_phase_ = 0;
  s->modulator_phase_ = 0;
  s->sub_phase_ = 0;

  s->previous_carrier_frequency_ = (440.0f / 8.0f) / 48000.0f;  /* a0 */
  s->previous_modulator_frequency_ = (440.0f / 8.0f) / 48000.0f;
  s->previous_amount_ = 0.0f;
  s->previous_feedback_ = 0.0f;
  s->previous_sample_ = 0.0f;
  s->sub_fir_ = 0.0f;
  s->carrier_fir_ = 0.0f;
}

void fm_render(FM *s, const Params *p, float *out, float *aux, int size) {
  // 4x oversampling
  const float note = p->note - 24.0f;

  const float ratio = Interpolate(
      PL_F(pl_lut_fm_frequency_quantizer),
      p->harmonics,
      128.0f);

  float modulator_note = note + ratio;
  float target_modulator_frequency = NoteToFrequency(modulator_note);
  float hf_taming;
  Interp carrier_frequency, modulator_frequency, amount_modulation, feedback_modulation;
  Down carrier_downsampler, sub_downsampler;
  CONSTRAIN(target_modulator_frequency, 0.0f, 0.5f);

  // Reduce the maximum FM index for high pitched notes, to prevent aliasing.
  hf_taming = 1.0f - (modulator_note - 72.0f) * 0.025f;
  CONSTRAIN(hf_taming, 0.0f, 1.0f);
  hf_taming *= hf_taming;

  carrier_frequency = interp(
      &s->previous_carrier_frequency_, NoteToFrequency(note), size);
  modulator_frequency = interp(
      &s->previous_modulator_frequency_, target_modulator_frequency, size);
  amount_modulation = interp(
      &s->previous_amount_,
      2.0f * p->timbre * p->timbre * hf_taming,
      size);
  feedback_modulation = interp(
      &s->previous_feedback_, 2.0f * p->morph - 1.0f, size);

#ifdef PL_FM_KERNEL
  {
    float k[8];
    k[0] = carrier_frequency.increment;
    k[1] = modulator_frequency.increment;
    k[2] = amount_modulation.increment;
    k[3] = feedback_modulation.increment;
    k[4] = PL_F(pl_lut_4x_downsampler_fir)[0];
    k[5] = PL_F(pl_lut_4x_downsampler_fir)[1];
    k[6] = PL_F(pl_lut_4x_downsampler_fir)[2];
    k[7] = PL_F(pl_lut_4x_downsampler_fir)[3];
    fm_kernel(s, k, out, aux, size);
    return;
  }
#endif
  carrier_downsampler = down_init(&s->carrier_fir_);
  sub_downsampler = down_init(&s->sub_fir_);

  while (size--) {
    const float max_uint32 = 4294967296.0f;
    float amount, feedback, phase_feedback, _modulator_frequency, modulator_fb;
    unsigned int carrier_increment;
    const float *fir = PL_F(pl_lut_4x_downsampler_fir);
    int j;
    /* the interpolators' Next(), written out (the same operations as `next`, without the calls) */
    amount_modulation.value += amount_modulation.increment;
    amount = amount_modulation.value;
    feedback_modulation.value += feedback_modulation.increment;
    feedback = feedback_modulation.value;
    phase_feedback = feedback < 0.0f ? 0.5f * feedback * feedback : 0.0f;
    carrier_frequency.value += carrier_frequency.increment;
    carrier_increment = ftou(max_uint32 * carrier_frequency.value);
    modulator_frequency.value += modulator_frequency.increment;
    _modulator_frequency = modulator_frequency.value;
    modulator_fb = feedback > 0.0f ? 0.25f * feedback * feedback : 0.0f;

    for (j = 0; j < 4; ++j) {  /* kOversampling */
      float modulator, carrier, sub;
      s->modulator_phase_ += ftou(max_uint32 *
           _modulator_frequency * (1.0f + s->previous_sample_ * phase_feedback));
      s->carrier_phase_ += carrier_increment;
      s->sub_phase_ += carrier_increment >> 1;
      modulator = SinePM(
          s->modulator_phase_, modulator_fb * s->previous_sample_);
      carrier = SinePM(s->carrier_phase_, amount * modulator);
      sub = SinePM(s->sub_phase_, amount * carrier * 0.25f);
      ONE_POLE(s->previous_sample_, carrier, 0.05f);
      /* Downsampler::Accumulate(j, ...) for both, written out */
      carrier_downsampler.head_ += carrier * fir[3 - j];
      carrier_downsampler.tail_ += carrier * fir[j];
      sub_downsampler.head_ += sub * fir[3 - j];
      sub_downsampler.tail_ += sub * fir[j];
    }

    *out++ = down_read(&carrier_downsampler);
    *aux++ = down_read(&sub_downsampler);
  }

  commit(&carrier_frequency);
  commit(&modulator_frequency);
  commit(&amount_modulation);
  commit(&feedback_modulation);
  down_commit(&carrier_downsampler);
  down_commit(&sub_downsampler);
}
