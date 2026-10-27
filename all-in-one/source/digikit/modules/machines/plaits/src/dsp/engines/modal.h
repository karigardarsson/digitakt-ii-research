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
// PLAITS for the Digitakt II: Plaits' ModalEngine (model RINGS: a mallet click through a low-pass into a 24-mode
// resonator, "taken from Rings"; HARMONICS the structure (inharmonicity), TIMBRE the brightness, MORPH the damping;
// AUX the exciter) with its ModalVoice, in C99, translated from plaits/dsp/engine/modal_engine.{h,cc} and
// plaits/dsp/physical_modelling/modal_voice.{h,cc}.

#ifndef PLAITS_MODAL_H
#define PLAITS_MODAL_H
#include "engine.h"
#include "../lib/resonator.h"
typedef struct {
  ResonatorSvf1 excitation_filter_;  /* ModalVoice */
  Resonator resonator_;
} ModalVoice;
typedef struct {
  ModalVoice voice_;
  float harmonics_lp_;
} Modal;
#ifdef __cplusplus
extern "C" {
#endif
/* Init and Reset (Plaits' Voice calls Reset when the engine is selected; Reset is voice_.Init()). */
void modal_init(Modal *s);
void modal_render(Modal *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_MODAL_H
