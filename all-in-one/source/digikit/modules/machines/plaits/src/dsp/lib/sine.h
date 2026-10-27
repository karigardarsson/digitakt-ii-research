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
// PLAITS for the Digitakt II: the sine lookups of plaits/dsp/oscillator/sine_oscillator.h (Sine, SineNoWrap, SinePM)
// and stmlib's InterpolateWrap, in C99, from the dt2-machine-lab translation (MIT, the notice above).
// Unsigned conversions are explicit (Selache's C converts through signed FIX/FLOAT only, and the encoding gate refuses
// its register-to-register FLOAT): `ftou` equals static_cast<uint32_t>(x) for 0 <= x < 2^32, and SinePM writes its
// own conversions out (see there).

#ifndef PLAITS_LIB_SINE_H
#define PLAITS_LIB_SINE_H
#include "units.h"

/* static_cast<uint32_t>(x), truncating, for 0 <= x < 2^32 (x - 2^31 is exact in float for x >= 2^31). */
static unsigned int ftou(float x) {
  if (x >= 2147483648.0f) return (unsigned int)(int)(x - 2147483648.0f) + 2147483648u;
  return (unsigned int)(int)x;
}

/* For index >= 0 (upstream reads outside the table for a negative one; the engines here never pass one). */
static float InterpolateWrap(const float *table, float index, float size) {
  index -= itof_nonneg((int)index);
  index *= size;
  MAKE_INTEGRAL_FRACTIONAL(index)
  float a = table[index_integral];
  float b = table[index_integral + 1];
  return a + (b - a) * index_fractional;
}

static float Sine(float phase) { return InterpolateWrap(PL_F(pl_lut_sine), phase, 512.0f); }
static float SineNoWrap(float phase) { return Interpolate(PL_F(pl_lut_sine), phase, 512.0f); }

/* Upstream's SinePM with its conversions written out, each equal to upstream's bit for bit (no calls: Selache's C
 * has no inlining):
 *   - static_cast<uint32_t>((pm + 32) * 2^26) * 64: x = (pm + 32) * 2^26 is in [0, 2^32); for x >= 2^31 the cast is
 *     (int)(x - 2^31) + 2^31, and the 2^31 vanishes in the * 64 (mod 2^32), so (int)(x - 2^31) * 64 is the same;
 *   - static_cast<float>(phase << 9) / 2^32: phase << 9 has at most 23 significant bits, so the cast is exact, and
 *     the quotient is (phase & 0x7fffff) / 2^23, which is the float with exponent 0 and that mantissa, minus 1. */
static float SinePM(unsigned int phase, float pm) {
  union { unsigned int u; float f; } bits;
  unsigned int integral;
  float x, fractional, a, b;

  x = (pm + 32.0f) * 67108864.0f;              /* (pm + max_index) * max_uint32 / (max_index * 2) */
  if (x >= 2147483648.0f) x -= 2147483648.0f;
  phase += (unsigned int)(int)x << 6;          /* * max_index * 2 */
  integral = phase >> 23;                      /* 32 - kSineLUTBits */
  bits.u = 0x3F800000u | (phase & 0x7FFFFFu);
  fractional = *(float *)(void *)&bits - 1.0f;
  a = PL_F(pl_lut_sine)[integral];
  b = PL_F(pl_lut_sine)[integral + 1];
  return a + (b - a) * fractional;
}

#endif  // PLAITS_LIB_SINE_H
