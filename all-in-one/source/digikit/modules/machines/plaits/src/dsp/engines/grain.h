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
// PLAITS for the Digitakt II: Plaits' GrainEngine (model FORMANT: two grainlet oscillators on OUT, the Z oscillator on
// AUX) in C99. Translated from plaits/dsp/engine/grain_engine.{h,cc}, plaits/dsp/oscillator/grainlet_oscillator.h and
// z_oscillator.h, and stmlib's OnePole; the grainlets follow the dt2-machine-lab translation (MIT, the notice above),
// the Z oscillator and the AUX path are from upstream directly. (Upstream's VOSIM oscillator is commented out of the
// engine and not ported.)

#ifndef PLAITS_GRAIN_H
#define PLAITS_GRAIN_H
#include "engine.h"
#include "../lib/one_pole.h"
typedef struct {
 float carrier_phase_,formant_phase_,next_sample_;
 float carrier_frequency_,formant_frequency_,carrier_shape_,carrier_bleed_;
} Grainlet;
typedef struct {
 float carrier_phase_,discontinuity_phase_,formant_phase_,next_sample_;
 float carrier_frequency_,formant_frequency_,carrier_shape_,mode_;
} ZOsc;
typedef struct {
 Grainlet grainlet_[2];
 ZOsc z_oscillator_;
 OnePole dc_blocker_[2];
} Grain;
#ifdef __cplusplus
extern "C" {
#endif
void grain_init(Grain *s);
// out and aux receive size samples each.
void grain_render(Grain *s,const Params *p,float *out,float *aux,int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_GRAIN_H
