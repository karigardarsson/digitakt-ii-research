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
// PLAITS for the Digitakt II: Plaits' WaveshapingEngine (model ASYM-TRI: slope -> waveshaper -> wavefolder) in C99.
// Translated from plaits/dsp/engine/waveshaping_engine.{h,cc} and the slope oscillator it uses
// (plaits/dsp/oscillator/oscillator.h, OSCILLATOR_SHAPE_SLOPE), at a 48,000 Hz pitch reference. The slope
// oscillator and the first half of the render follow the dt2-machine-lab translation (MIT, the notice above); the
// AUX path (triangle -> sine / second wavefolder) is from upstream directly.

#ifndef PLAITS_WAVESHAPING_H
#define PLAITS_WAVESHAPING_H
#include "engine.h"
typedef struct { float phase_,next_sample_,lp_state_,hp_state_; int high_; float frequency_,pw_; } Slope;  // Oscillator
typedef struct {
 Slope slope_,triangle_;
 float previous_shape_,previous_wavefolder_gain_,previous_overtone_gain_;
} WS;
#ifdef __cplusplus
extern "C" {
#endif
void ws_init(WS *s);
// size <= 24 samples at 48 kHz; out and aux receive size samples each.
void ws_render(WS *s,const Params *p,float *out,float *aux,int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_WAVESHAPING_H
