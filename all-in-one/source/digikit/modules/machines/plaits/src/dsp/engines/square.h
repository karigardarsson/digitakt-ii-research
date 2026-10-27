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
// PLAITS for the Digitakt II: SQUARE (model 23, Plaits' ChiptuneEngine, plaits/dsp/engine2/chiptune_engine.{h,cc}) in
// C99. Always clocked (the owner's decision): every DT2 note-on is Plaits' TRIGGER_RISING_EDGE and advances the
// arpeggiator one note; TRIGGER_UNPATCHED (the chord-and-bass mode of five voices) is not ported, and neither is the
// Plaits envelope (envelope_shape_ stays NO_ENVELOPE) or `already_enveloped`. HARMONICS the chord, TIMBRE the pattern
// and range, MORPH the waveform. OUT is the arpeggiated SuperSquareOscillator, AUX the NES triangle bass one octave
// down. State: one square voice (upstream's other four are only rendered unclocked), the bass, the chord bank, the
// arpeggiator, the pattern selector and the random state of the arpeggiator's random mode (stmlib's Random is one
// global upstream; here one per voice, seeded 0x21 like a power-up).
#ifndef PLAITS_SQUARE_H
#define PLAITS_SQUARE_H
#include "engine.h"
#include "../lib/chord_bank.h"

typedef struct {
  float master_phase_, slave_phase_, next_sample_;
  int high_;
  float master_frequency_, slave_frequency_;
} SquareOsc;

typedef struct {
  float phase_, next_sample_;
  int step_, ascending_;
  float frequency_;
} NesTriOsc;

typedef struct {
  int mode_, range_, note_, octave_, direction_;
} SquareArp;

typedef struct {
  SquareOsc voice_;
  NesTriOsc bass_;
  ChordBank chords_;
  SquareArp arp_;
  HysteresisQuantizer2 pattern_selector_;
  unsigned int rng_;
} Square;

#ifdef __cplusplus
extern "C" {
#endif
void square_init(Square *s);
void square_render(Square *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_SQUARE_H
