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
// PLAITS for the Digitakt II: Plaits' ChordEngine (model CHORD: a four-note chord from the chord bank on five voices,
// HARMONICS the chord, TIMBRE the inversion, MORPH from divide-down organ/string registrations to wavetable voices;
// AUX the chord's root) in C99, translated from plaits/dsp/engine/chord_engine.{h,cc}.

#ifndef PLAITS_CHORD_H
#define PLAITS_CHORD_H
#include "engine.h"
#include "../lib/chord_bank.h"
#include "../lib/string_synth_oscillator.h"
#include "../lib/wavetable_oscillator.h"
typedef struct {
  StringSynthOscillator divide_down_voice_[kChordNumVoices];
  WavetableOscillator wavetable_voice_[kChordNumVoices];
  ChordBank chords_;
  float morph_lp_;
  float timbre_lp_;
} Chord;
#ifdef __cplusplus
extern "C" {
#endif
void chord_init(Chord *s);
// out and aux receive size samples each.
void chord_render(Chord *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_CHORD_H
