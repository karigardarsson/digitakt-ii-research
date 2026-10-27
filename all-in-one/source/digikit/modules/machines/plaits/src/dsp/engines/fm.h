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
// PLAITS for the Digitakt II: Plaits' FMEngine (model 2FM: two sine operators with feedback, a sub-oscillator on AUX)
// in C99. Translated from plaits/dsp/engine/fm_engine.{h,cc} with plaits/dsp/oscillator/sine_oscillator.h (SinePM)
// and plaits/dsp/downsampler/4x_downsampler.h; OUT follows the dt2-machine-lab translation (MIT, the notice above),
// the AUX sub-oscillator is from upstream directly.

#ifndef PLAITS_FM_H
#define PLAITS_FM_H
#include "engine.h"
typedef struct {
 unsigned int carrier_phase_,modulator_phase_,sub_phase_;
 float previous_carrier_frequency_,previous_modulator_frequency_,previous_amount_,previous_feedback_,previous_sample_;
 float sub_fir_,carrier_fir_;
} FM;
#ifdef __cplusplus
extern "C" {
#endif
void fm_init(FM *s);
// out and aux receive size samples each.
void fm_render(FM *s,const Params *p,float *out,float *aux,int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_FM_H
