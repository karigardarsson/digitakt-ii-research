// Copyright 2014-2017 Emilie Gillet.
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
// PLAITS for the Digitakt II: the virtual-analog engine (Plaits VirtualAnalogEngine, VA_VARIANT 2) in C99.
// Translated from plaits/dsp/engine/virtual_analog_engine.{h,cc} and the oscillators it uses; the C translation
// comes from the dt2-machine-lab (MIT, the notice above), adapted here to a 48,000 Hz pitch reference.

#ifndef PLAITS_VA_H
#define PLAITS_VA_H
#include "engine.h"
typedef struct {
 float master_phase_,slave_phase_,next_sample_,previous_pw_; int high_;
 float master_frequency_,slave_frequency_,pw_,waveshape_,phase_modulation_;
} Shape;
typedef struct {
 float phase_,next_sample_,previous_pw_; int high_;
 float frequency_,pw_,waveshape_;
} Saw;
typedef struct {
 Shape primary_,auxiliary_,sync_; Saw variable_saw_;
 float auxiliary_amount_,xmod_amount_,temp_buffer_[24];  // 24 = kMaxBlockSize
} VA;
#ifdef __cplusplus
extern "C" {
#endif
void va_init(VA *s);
// size <= 24 samples at 48 kHz; out and aux receive size samples each.
void va_render(VA *s,const Params *p,float *out,float *aux,int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_VA_H
