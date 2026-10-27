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
// PLAITS for the Digitakt II: Plaits' Differentiator and WavetableOscillator (plaits/dsp/oscillator/
// wavetable_oscillator.h) in C99, for the CHORD model and the later wavetable engines. The template
// WavetableOscillator<wavetable_size = 128, num_waves, approximate_scale = true, attenuate_high_frequencies = true>
// becomes one function with num_waves as an argument (CHORD uses 15). Upstream's array of wave pointers is a base
// (pl_wav_integrated_waves as floats, lib/waves.h) and an array of offsets into it: an initialised table of addresses
// would need data relocations.

#ifndef PLAITS_LIB_WAVETABLE_OSCILLATOR_H
#define PLAITS_LIB_WAVETABLE_OSCILLATOR_H

/* Differentiator: a one-pole low-pass of the first difference. */
typedef struct { float lp_, previous_; } Differentiator;

typedef struct {
  float phase_;
  float frequency_;  /* the interpolators' states */
  float amplitude_;
  float waveform_;
  float lp_;
  Differentiator differentiator_;
} WavetableOscillator;

#ifdef __cplusplus
extern "C" {
#endif
void differentiator_init(Differentiator *d);
void wavetable_oscillator_init(WavetableOscillator *s);
/* WavetableOscillator<128, num_waves>::Render: adds `size` samples to out. Wave k is the 128 (+4) floats at
 * waves + offsets[k], k < num_waves. */
void wavetable_oscillator_render(WavetableOscillator *s, float frequency, float amplitude, float waveform,
                                 const float *waves, const int *offsets, int num_waves, float *out, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_LIB_WAVETABLE_OSCILLATOR_H
