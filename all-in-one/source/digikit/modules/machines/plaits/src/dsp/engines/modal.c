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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' ModalEngine (model RINGS) and ModalVoice in C99, from plaits/dsp/engine/
// modal_engine.cc and plaits/dsp/physical_modelling/modal_voice.cc.
// Differences from upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - modal_init is Init and Reset;
//   - ModalVoice's sustain branch (the trigger UNPATCHED: Dust noise instead of the click, range 36, 4 f0, q 0.7) is
//     not ported: the wrapper always passes a patched trigger (plaits.c TRIG_*; the design's strike engines), so
//     `sustain` is always false; the temporary buffer (kMaxBlockSize floats of the arena upstream) is on the stack.
// The click is a single sample in the sub-block of a rising edge (the note-on's first sub-block).
#include "modal.h"
#include "../lib/units.h"

#define TRIGGER_RISING_EDGE 1
#define kModalMaxSize 24

static void modal_voice_init(ModalVoice *s) {
  resonator_svf1_init(&s->excitation_filter_);
  resonator_init(&s->resonator_, 0.015f, kMaxNumModes);
}

void modal_init(Modal *s) {
  s->harmonics_lp_ = 0.0f;
  modal_voice_init(&s->voice_);
}

/* ModalVoice::Render with sustain false. */
static void modal_voice_render(ModalVoice *s, int trigger, float accent, float f0, float structure, float brightness,
                               float damping, float *temp, float *out, float *aux, int size) {
  const float range = 60.0f;
  const float f = 2.0f * f0;
  float cutoff;
  const float q = 1.5f;
  int i;

  brightness += 0.25f * accent * (1.0f - brightness);
  damping += 0.25f * accent * (1.0f - damping);

  cutoff = minf(f * SemitonesToRatio((brightness * (2.0f - brightness) - 0.5f) * range), 0.499f);

  for (i = 0; i < size; ++i) {
    temp[i] = 0.0f;
  }
  if (trigger) {
    const float attenuation = 1.0f - damping * 0.5f;
    const float amplitude = (0.12f + 0.08f * accent) * attenuation;
    temp[0] = amplitude * SemitonesToRatio(cutoff * cutoff * 24.0f) / cutoff;
  }
  resonator_svf1_low_pass(&s->excitation_filter_, cutoff, q, 1.0f, temp, temp, size);
  for (i = 0; i < size; ++i) {
    aux[i] += temp[i];
  }

  resonator_process(&s->resonator_, f0, structure, brightness, damping, temp, out, size);
}

void modal_render(Modal *s, const Params *p, float *out, float *aux, int size) {
  float temp[kModalMaxSize];
  int i;
  for (i = 0; i < size; ++i) {
    out[i] = 0.0f;
    aux[i] = 0.0f;
  }

  ONE_POLE(s->harmonics_lp_, p->harmonics, 0.01f);

  modal_voice_render(&s->voice_, p->trigger & TRIGGER_RISING_EDGE, p->accent, NoteToFrequency(p->note),
                     s->harmonics_lp_, p->timbre, p->morph, temp, out, aux, size);
}
