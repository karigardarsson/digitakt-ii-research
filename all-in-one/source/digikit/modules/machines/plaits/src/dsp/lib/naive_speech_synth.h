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
// PLAITS for the Digitakt II: Plaits' NaiveSpeechSynth (plaits/dsp/speech/naive_speech_synth.{h,cc}) in C99: a pulse
// oscillator (Plaits' Oscillator, OSCILLATOR_SHAPE_IMPULSE_TRAIN) through five zero-delay band-pass filters (stmlib's
// Svf) tuned to the formants of five vowels in five registers (lib/speech_tables.c, pl_naive_phonemes).

#ifndef PLAITS_LIB_NAIVE_SPEECH_SYNTH_H
#define PLAITS_LIB_NAIVE_SPEECH_SYNTH_H
#include "svf.h"

#define kNaiveSpeechNumFormants 5

/* Plaits' Oscillator, the state OSCILLATOR_SHAPE_IMPULSE_TRAIN uses (its high_ and pw_ do not change the output of
 * that shape: pw is constrained to 0.5 and never read). */
typedef struct {
  float phase_;
  float next_sample_;
  float lp_state_;
  float hp_state_;
  float frequency_;
} ImpulseOscillator;

typedef struct {
  ImpulseOscillator pulse_;
  float frequency_;
  int click_duration_;
  Svf filter_[kNaiveSpeechNumFormants];
  Svf pulse_coloration_;
} NaiveSpeechSynth;

#ifdef __cplusplus
extern "C" {
#endif
void naive_speech_synth_init(NaiveSpeechSynth *s);
/* Render(click, frequency, phoneme, vocal_register, temp, excitation, output, size); upstream's temp is unused. */
void naive_speech_synth_render(NaiveSpeechSynth *s, int click, float frequency, float phoneme, float vocal_register,
                               float *excitation, float *output, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_LIB_NAIVE_SPEECH_SYNTH_H
