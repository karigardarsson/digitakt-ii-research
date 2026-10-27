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
// PLAITS for the Digitakt II: Plaits' FastSineOscillator in C99, from plaits/dsp/oscillator/sine_oscillator.h, with
// stmlib's fast_rsqrt_carmack (stmlib/dsp/rsqrt.h) and ParameterInterpolator (lib/units.h).
// Differences from upstream, none of which changes a result on the host:
//   - Fast2Sin's (2.0f * 0.96f / 6.0f) is written as the float it evaluates to, 0.319999993f (0x3ea3d70a), and
//     float(M_PI) as 3.14159274f;
//   - fast_rsqrt_carmack's bit casts go through a union written as its unsigned member and read as float through a
//     pointer to the union (Selache's C places union members like struct members: docs/findings/20-patch-toolchains.md).
#include "fast_sine_oscillator.h"
#include "units.h"

void fast_sine_oscillator_init(FastSineOscillator *s) {
  s->x_ = 1.0f;
  s->y_ = 0.0f;
  s->epsilon_ = 0.0f;
  s->amplitude_ = 0.0f;
}

void fast_sine_oscillator_render_quadrature(FastSineOscillator *s, float frequency, float amplitude, float *x_out,
                                            float *y_out, int size) {
  Interp epsilon, am;
  float x, y, norm;
  int n;
  if (frequency >= 0.25f) {
    frequency = 0.25f;
    amplitude = 0.0f;
  } else {
    amplitude *= 1.0f - frequency * 4.0f;
  }

  epsilon = interp(&s->epsilon_, fast_2_sin(frequency), size);
  am = interp(&s->amplitude_, amplitude, size);
  x = s->x_;
  y = s->y_;

  norm = x * x + y * y;
  if (norm <= 0.5f || norm >= 2.0f) {
    const float scale = fast_rsqrt_carmack(norm);
    x *= scale;
    y *= scale;
  }

  for (n = 0; n < size; ++n) {
    const float e = next(&epsilon);
    float a;
    x += e * y;
    y -= e * x;
    a = next(&am);
    x_out[n] = x * a;
    y_out[n] = y * a;
  }
  s->x_ = x;
  s->y_ = y;
  commit(&epsilon);
  commit(&am);
}
