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
// PLAITS for the Digitakt II: Plaits' AdditiveEngine (model ADDITIVE: 24 harmonics on OUT, 8 organ harmonics on AUX)
// in C99. Translated from plaits/dsp/engine/additive_engine.{h,cc} and plaits/dsp/oscillator/harmonic_oscillator.h;
// OUT follows the dt2-machine-lab translation (MIT, the notice above), the AUX organ path is from upstream directly.

#ifndef PLAITS_ADDITIVE_H
#define PLAITS_ADDITIVE_H
#include "engine.h"
typedef struct { float phase_,frequency_,amplitude_[12]; } Harmonic;  // HarmonicOscillator<kHarmonicBatchSize = 12>
typedef struct {
 Harmonic harmonic_oscillator_[3];  // kNumHarmonicOscillators
 float amplitudes_[36];             // kNumHarmonics (upstream allocates it from the engine's arena)
} Additive;
#ifdef __cplusplus
extern "C" {
#endif
void additive_init(Additive *s);
// out and aux receive size samples each.
void additive_render(Additive *s,const Params *p,float *out,float *aux,int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_ADDITIVE_H
