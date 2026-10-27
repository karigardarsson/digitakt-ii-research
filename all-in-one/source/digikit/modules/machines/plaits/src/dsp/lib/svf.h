// Copyright 2014 Emilie Gillet.
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
// PLAITS for the Digitakt II: the state of stmlib's Svf (stmlib/dsp/filter.h), the zero-delay state variable filter.
// Its functions (set_f_q<FREQUENCY_DIRTY>, Process<FILTER_MODE_BAND_PASS> and ProcessAdd<FILTER_MODE_BAND_PASS> over a
// block) are in lib/svf.c, with the same float operations in the same order as upstream.

#ifndef PLAITS_LIB_SVF_H
#define PLAITS_LIB_SVF_H
typedef struct { float g_, r_, h_, state_1_, state_2_; } Svf;
#ifdef __cplusplus
extern "C" {
#endif
/* Init: set_f_q<FREQUENCY_DIRTY>(0.01f, 100.0f) and Reset. */
void svf_init(Svf *s);
/* set_f_q<FREQUENCY_DIRTY>(f, resonance), with r = 1 / resonance given (upstream divides; the callers' resonances
 * are constants whose reciprocals are exact floats or rounded as upstream's division rounds them). */
void svf_set_f_r(Svf *s, float f, float r);
/* Process<FILTER_MODE_BAND_PASS>(in, out, size); in may be out. */
void svf_band_pass(Svf *s, const float *in, float *out, int size);
/* ProcessAdd<FILTER_MODE_BAND_PASS>(in, out, size, gain): out[i] += gain * band pass. */
void svf_band_pass_add(Svf *s, const float *in, float *out, int size, float gain);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_LIB_SVF_H
