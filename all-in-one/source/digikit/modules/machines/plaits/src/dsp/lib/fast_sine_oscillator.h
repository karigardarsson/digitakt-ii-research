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
// PLAITS for the Digitakt II: Plaits' FastSineOscillator (plaits/dsp/oscillator/sine_oscillator.h, the "magic circle"
// sine and cosine) in C99: Init and RenderQuadrature, the mode TERRAIN uses.

#ifndef PLAITS_LIB_FAST_SINE_OSCILLATOR_H
#define PLAITS_LIB_FAST_SINE_OSCILLATOR_H
#include "units.h"
typedef struct {
  float x_, y_;
  float epsilon_, amplitude_;  /* the interpolators' states */
} FastSineOscillator;
/* The two helpers (static: shared with engines/swarm.c, which keeps its oscillators as lanes of arrays). */
static float fast_2_sin(float f) {
  const float f_pi = f * 3.14159274f;
  return f_pi * (2.0f - 0.319999993f * f_pi * f_pi);
}

/* stmlib::fast_rsqrt_carmack. */
static float fast_rsqrt_carmack(float x) {
  union { unsigned int u; float f; } bits;
  unsigned int i;
  float x2, y;
  const float threehalfs = 1.5f;
  y = x;
  *(float *)(void *)&bits = y;
  i = bits.u;
  i = 0x5f3759dfu - (i >> 1);
  bits.u = i;
  y = *(float *)(void *)&bits;
  x2 = x * 0.5f;
  y = y * (threehalfs - (x2 * y * y));
  return y;
}

void fast_sine_oscillator_init(FastSineOscillator *s);
/* RenderQuadrature(frequency, amplitude, x, y, size). */
void fast_sine_oscillator_render_quadrature(FastSineOscillator *s, float frequency, float amplitude, float *x,
                                            float *y, int size);
#endif  // PLAITS_LIB_FAST_SINE_OSCILLATOR_H
