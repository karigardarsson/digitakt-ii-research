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
// PLAITS for the Digitakt II: Plaits' SpeechEngine (model SPEECH: HARMONICS crossfades from the naive formant synth
// to SAM to the LPC10 vowels, then selects one of five LPC10 word banks; TIMBRE the formant shift, MORPH the vowel or
// the word; a trigger starts a consonant or the word) in C99, translated from plaits/dsp/engine/speech_engine.{h,cc}.

#ifndef PLAITS_SPEECH_H
#define PLAITS_SPEECH_H
#include "engine.h"
#include "../lib/chord_bank.h"
#include "../lib/naive_speech_synth.h"
#include "../lib/sam_speech_synth.h"
#include "../lib/lpc_speech_synth.h"
typedef struct {
  HysteresisQuantizer2 word_bank_quantizer_;
  NaiveSpeechSynth naive_speech_synth_;
  SAMSpeechSynth sam_speech_synth_;
  LPCSpeechSynthController lpc_speech_synth_controller_;
  float prosody_amount_;
  float speed_;
} Speech;
#ifdef __cplusplus
extern "C" {
#endif
/* Init and Reset (Plaits' voice calls Reset when the engine is selected). prosody_amount and speed are 0 (see
 * speech.c). */
void speech_init(Speech *s);
// out and aux receive size samples each (size <= 16: the SAM crossfade's temporary buffers are on the stack).
void speech_render(Speech *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_SPEECH_H
