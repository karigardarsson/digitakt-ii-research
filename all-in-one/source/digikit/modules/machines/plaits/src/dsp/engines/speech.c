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
// PLAITS for the Digitakt II: Plaits' SpeechEngine (model SPEECH) in C99, from plaits/dsp/engine/speech_engine.cc.
//
// Controls as Plaits' Voice sets them (plaits/dsp/voice.cc, engine_index 15, Plaits' own numbering), with the patch
// values Plaits would have with nothing but TRIG patched and its attenuverters at noon:
//   - prosody_amount = patch.frequency_modulation_amount and speed = patch.morph_modulation_amount when TRIG is
//     patched and FM / MORPH are not; both are 0 here (the attenuverters' centre, as Plaits' own test harness sets
//     them): the words follow the note's pitch, not their recorded intonation, at their recorded speed (TIMBRE still
//     stretches them outside 0.4..0.6, as upstream);
//   - the trigger: the wrapper passes Params.trigger as Plaits' Voice builds it with TRIG patched (plaits.c);
//   - accent: Params.accent, 0.8 from the wrapper (Plaits' value with LEVEL unpatched); it is the word banks' gain
//     when the trigger replays a word;
//   - already_enveloped is not returned: the wrapper applies no low-pass gate for any model (the Digitakt's amp
//     envelope shapes the voice).
// Differences from upstream, none of which changes a result on the host: the temporary buffers are on the stack
// (size <= 16) rather than in the arena; Init and Reset are one function.
#include "speech.h"
#include "../lib/units.h"

#define kLPCSpeechSynthNumWordBanks 5
#define TRIGGER_RISING_EDGE 1
#define TRIGGER_UNPATCHED 2

void speech_init(Speech *s) {
  sam_speech_synth_init(&s->sam_speech_synth_);
  naive_speech_synth_init(&s->naive_speech_synth_);
  lpc_speech_synth_controller_init(&s->lpc_speech_synth_controller_);  /* resets the word bank */
  hysteresis_quantizer2_init(&s->word_bank_quantizer_, kLPCSpeechSynthNumWordBanks + 1, 0.1f, 0);
  s->prosody_amount_ = 0.0f;
  s->speed_ = 0.0f;
}

void speech_render(Speech *s, const Params *p, float *out, float *aux, int size) {
  const float f0 = NoteToFrequency(p->note);
  const float group = p->harmonics * 6.0f;
  int i;

  /* Interpolates between the 3 models: naive, SAM, LPC. */
  if (group <= 2.0f) {
    float temp_0[16], temp_1[16];
    float blend = group;
    if (group <= 1.0f) {
      naive_speech_synth_render(&s->naive_speech_synth_, p->trigger == TRIGGER_RISING_EDGE, f0, p->morph, p->timbre,
                                aux, out, size);
    } else {
      lpc_speech_synth_controller_render(&s->lpc_speech_synth_controller_, p->trigger & TRIGGER_UNPATCHED,
                                         p->trigger & TRIGGER_RISING_EDGE, -1, f0, 0.0f, 0.0f, p->morph, p->timbre,
                                         1.0f, aux, out, size);
      blend = 2.0f - blend;
    }

    sam_speech_synth_render(&s->sam_speech_synth_, p->trigger == TRIGGER_RISING_EDGE, f0, p->morph, p->timbre,
                            temp_0, temp_1, size);

    blend *= blend * (3.0f - 2.0f * blend);
    blend *= blend * (3.0f - 2.0f * blend);
    for (i = 0; i < size; ++i) {
      aux[i] += (temp_0[i] - aux[i]) * blend;
      out[i] += (temp_1[i] - out[i]) * blend;
    }
  } else {
    /* Change phonemes/words for LPC. */
    const int word_bank = hysteresis_quantizer2_process(&s->word_bank_quantizer_, 0, (group - 2.0f) * 0.275f) - 1;
    const int replay_prosody = word_bank >= 0 && !(p->trigger & TRIGGER_UNPATCHED);
    lpc_speech_synth_controller_render(&s->lpc_speech_synth_controller_, p->trigger & TRIGGER_UNPATCHED,
                                       p->trigger & TRIGGER_RISING_EDGE, word_bank, f0, s->prosody_amount_,
                                       s->speed_, p->morph, p->timbre, replay_prosody ? p->accent : 1.0f, aux, out,
                                       size);
  }
}
