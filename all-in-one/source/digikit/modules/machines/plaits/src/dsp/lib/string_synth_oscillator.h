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
// PLAITS for the Digitakt II: Plaits' StringSynthOscillator (plaits/dsp/oscillator/string_synth_oscillator.h) in C99,
// the divide-down organ/string voice of the CHORD model (and of STRING MACHINE later): four band-limited saws an
// octave apart, mixed by a registration of 7 harmonics.

#ifndef PLAITS_LIB_STRING_SYNTH_OSCILLATOR_H
#define PLAITS_LIB_STRING_SYNTH_OSCILLATOR_H

typedef struct {
  float phase_;
  float next_sample_;
  int segment_;
  float frequency_;  /* the interpolators' states */
  float saw_8_gain_;
  float saw_4_gain_;
  float saw_2_gain_;
  float saw_1_gain_;
} StringSynthOscillator;

#ifdef __cplusplus
extern "C" {
#endif
void string_synth_oscillator_init(StringSynthOscillator *s);
/* Adds `size` samples to out; unshifted_registration holds 7 amplitudes. */
void string_synth_oscillator_render(StringSynthOscillator *s, float frequency, const float *unshifted_registration,
                                    float gain, float *out, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_LIB_STRING_SYNTH_OSCILLATOR_H
