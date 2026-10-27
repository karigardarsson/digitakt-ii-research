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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' Ensemble (plaits/dsp/fx/ensemble.h) with its FxEngine<1024, FORMAT_32_BIT> in
// C99: a stereo chorus of two 511-sample delay lines read at three LFO-modulated taps each. The state is the 1024-float
// circular buffer (upstream allocates it from the arena: here it is part of the engine's state), the write pointer and
// the two LFO phases. Same circular-buffer semantics as upstream (the write pointer decrements, every read and write
// is masked into the buffer).

#ifndef PLAITS_LIB_ENSEMBLE_H
#define PLAITS_LIB_ENSEMBLE_H

#define kEnsembleBufferSize 1024

typedef struct {
  float buffer_[kEnsembleBufferSize];
  int write_ptr_;
  unsigned int phase_1_;
  unsigned int phase_2_;
  int fresh_;  /* samples processed since ensemble_init (lib/string_machine_sm.s: the DSP build does not clear buffer_) */
} Ensemble;

#ifdef __cplusplus
extern "C" {
#endif
/* Ensemble::Init and Reset (Clear: the buffer and the write pointer to 0). */
void ensemble_init(Ensemble *e);
/* Process(left, right, size) in place with set_amount(amount) and set_depth(depth). */
void ensemble_process(Ensemble *e, float amount, float depth, float *left, float *right, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_LIB_ENSEMBLE_H
