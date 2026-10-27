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
// PLAITS for the Digitakt II: Plaits' WavetableEngine (model WAVETABLE) in C99, from
// plaits/dsp/engine/wavetable_engine.cc and InterpolateWaveHermite and Differentiator of
// plaits/dsp/oscillator/wavetable_oscillator.h. Differences from upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - wave_map_ is the constant table of LoadUserData(NULL) (lib/waves.h, offsets into the float wave table): the
//     DT2 has no user wavetable;
//   - Reset does nothing upstream, so wavetable_init is Init;
//   - the interpolators, the eight InterpolateWaveHermite reads (a macro) and the Differentiator are written out in
//     the loop (Selache's C has no inlining), with the same float operations in the same order;
//   - int-to-float conversions go through dsp_util.h `itof_nonneg` (non-negative values) and `itof_small` below
//     (AUX's quantisation, which may be negative).
// Index ranges: x_pre_lp_ and y_pre_lp_ (one-pole averages of values in [0, 6.9999]) stay in [0, 6.9999], so does
// every x/y target (integral + a fraction in [0, 1)) and x_lp_ and y_lp_: x0, y0 <= 6 and x1, y1 <= 7. z is the
// same; z0, z1 in 4..7 mirror to 3..0. So x + 8 y + 64 z <= 255 (pl_wavetable_map has 256 entries). The phase is in
// [0, 1), so the Hermite read touches entries p_integral .. p_integral + 3 <= 130 of a 132-entry wave.
// tests/plaits/test_wavetable_chord_host.py compares it bit for bit with upstream at 48,000 Hz and checks these
// ranges at every bank, row and column corner.
#include "wavetable.h"
#include "../lib/units.h"
#include "../lib/waves.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

/* (float)i for -2^22 < i < 2^22, without a FLOAT instruction (dsp_util.h `itof_nonneg` for the reason): 1.5 * 2^23 + i
 * is exact in the mantissa, and subtracting 1.5 * 2^23 leaves i exactly. AUX's i = (int)(OUT * 32) stays far inside
 * (|OUT| < 4 at every extreme the host test plays). Read through a pointer to the union converted to float *, as
 * itof_nonneg. */
#if defined(__ADSP21569__) && !defined(PL_WAVETABLE_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: wavetable_render's sample loop runs in lib/wavetable_engine_sm.s, bit-identical to the C loop below,
   which stays for the host and as the -DPL_WAVETABLE_C reference (tests/plaits/test_wavetable_engine_kernel.py). */
#define PL_WAVETABLE_KERNEL 1
void wavetable_engine_kernel(Wavetable *s, const float *k, float *out, float *aux, int size);
#endif

static float itof_small(int i) {
  union { int i; float f; } u;
  u.i = 0x4B400000 + i;
  return *(float *)(void *)&u - 12582912.0f;
}

static float clamp_frac(float x, float amount) {  /* upstream's Clamp */
  x = x - 0.5f;
  x *= amount;
  CONSTRAIN(x, -0.5f, 0.5f);
  x += 0.5f;
  return x;
}

void wavetable_init(Wavetable *s) {
  s->phase_ = 0.0f;

  s->x_lp_ = 0.0f;
  s->y_lp_ = 0.0f;
  s->z_lp_ = 0.0f;

  s->x_pre_lp_ = 0.0f;
  s->y_pre_lp_ = 0.0f;
  s->z_pre_lp_ = 0.0f;

  s->previous_x_ = 0.0f;
  s->previous_y_ = 0.0f;
  s->previous_z_ = 0.0f;
  s->previous_f0_ = (440.0f / 8.0f) / 48000.0f;  /* a0 */

  differentiator_init(&s->diff_out_);
}

/* InterpolateWaveHermite(wave_map_[index], p_integral, p_fractional) into dst. */
#define READ_WAVE(dst, index)                                         \
  {                                                                   \
    const float *t_ = waves + map[index] + p_integral;                \
    PL_IDX(0, index)                                                  \
    PL_IDX(1, p_integral + 3)                                         \
    const float xm1_ = t_[0];                                         \
    const float x0_ = t_[1];                                          \
    const float x1_ = t_[2];                                          \
    const float x2_ = t_[3];                                          \
    const float c_ = (x1_ - xm1_) * 0.5f;                             \
    const float v_ = x0_ - x1_;                                       \
    const float w_ = c_ + v_;                                         \
    const float a_ = w_ + v_ + (x2_ - x0_) * 0.5f;                    \
    const float b_neg_ = w_ + a_;                                     \
    dst = (((a_ * p_fractional) - b_neg_) * p_fractional + c_) * p_fractional + x0_; \
  }

void wavetable_render(Wavetable *s, const Params *p, float *out, float *aux, int size) {
  const float *waves = PL_F(pl_wav_integrated_waves);
  const int *map = pl_wavetable_map;
  const float f0 = NoteToFrequency(p->note);
  float quantization, lp_coefficient, x, y, z;
  Interp x_modulation, y_modulation, z_modulation, f0_modulation;
  float d_lp, d_previous;

  ONE_POLE(s->x_pre_lp_, p->timbre * 6.9999f, 0.2f);
  ONE_POLE(s->y_pre_lp_, p->morph * 6.9999f, 0.2f);
  ONE_POLE(s->z_pre_lp_, p->harmonics * 6.9999f, 0.05f);

  x = s->x_pre_lp_;
  y = s->y_pre_lp_;
  z = s->z_pre_lp_;

  quantization = minf(maxf(z - 3.0f, 0.0f), 1.0f);
  lp_coefficient = minf(
      maxf(2.0f * f0 * (4.0f - 3.0f * quantization), 0.01f), 0.1f);

  {
    MAKE_INTEGRAL_FRACTIONAL(x)
    MAKE_INTEGRAL_FRACTIONAL(y)
    MAKE_INTEGRAL_FRACTIONAL(z)

    x_fractional += quantization * (clamp_frac(x_fractional, 16.0f) - x_fractional);
    y_fractional += quantization * (clamp_frac(y_fractional, 16.0f) - y_fractional);
    z_fractional += quantization * (clamp_frac(z_fractional, 16.0f) - z_fractional);

    x_modulation = interp(&s->previous_x_, itof_nonneg(x_integral) + x_fractional, size);
    y_modulation = interp(&s->previous_y_, itof_nonneg(y_integral) + y_fractional, size);
    z_modulation = interp(&s->previous_z_, itof_nonneg(z_integral) + z_fractional, size);
  }

  f0_modulation = interp(&s->previous_f0_, f0, size);

#ifdef PL_WAVETABLE_KERNEL
  {
    float k[5];
    k[0] = x_modulation.increment;
    k[1] = y_modulation.increment;
    k[2] = z_modulation.increment;
    k[3] = f0_modulation.increment;
    k[4] = lp_coefficient;
    (void)waves;
    (void)map;
    (void)d_lp;
    (void)d_previous;
    wavetable_engine_kernel(s, k, out, aux, size);
    return;
  }
#endif
  d_lp = s->diff_out_.lp_;
  d_previous = s->diff_out_.previous_;
  while (size--) {
    float f, gain, cutoff, xs, ys, zs, ph, mix;
    float x0y0z0, x1y0z0, xy0z0, x0y1z0, x1y1z0, xy1z0, xyz0;
    float x0y0z1, x1y0z1, xy0z1, x0y1z1, x1y1z1, xy1z1, xyz1;
    int x_integral, y_integral, z_integral, p_integral;
    float x_fractional, y_fractional, z_fractional, p_fractional;
    int x0, x1, y0, y1, z0, z1;

    f0_modulation.value += f0_modulation.increment;  /* f0_modulation.Next() */
    f = f0_modulation.value;

    gain = (1.0f / (f * 131072.0f)) * (0.95f - f);
    cutoff = minf(128.0f * f, 1.0f);

    x_modulation.value += x_modulation.increment;
    ONE_POLE(s->x_lp_, x_modulation.value, lp_coefficient);
    y_modulation.value += y_modulation.increment;
    ONE_POLE(s->y_lp_, y_modulation.value, lp_coefficient);
    z_modulation.value += z_modulation.increment;
    ONE_POLE(s->z_lp_, z_modulation.value, lp_coefficient);

    xs = s->x_lp_;
    ys = s->y_lp_;
    zs = s->z_lp_;

    x_integral = (int)xs;
    x_fractional = xs - itof_nonneg(x_integral);
    y_integral = (int)ys;
    y_fractional = ys - itof_nonneg(y_integral);
    z_integral = (int)zs;
    z_fractional = zs - itof_nonneg(z_integral);

    s->phase_ += f;
    if (s->phase_ >= 1.0f) {
      s->phase_ -= 1.0f;
    }

    ph = s->phase_ * 128.0f;
    p_integral = (int)ph;
    p_fractional = ph - itof_nonneg(p_integral);

    x0 = x_integral;
    x1 = x_integral + 1;
    y0 = y_integral;
    y1 = y_integral + 1;
    z0 = z_integral;
    z1 = z_integral + 1;

    if (z0 >= 4) {
      z0 = 7 - z0;
    }
    if (z1 >= 4) {
      z1 = 7 - z1;
    }
    PL_IDX(1, p_integral)

    READ_WAVE(x0y0z0, x0 + y0 * 8 + z0 * 64)
    READ_WAVE(x1y0z0, x1 + y0 * 8 + z0 * 64)
    xy0z0 = x0y0z0 + (x1y0z0 - x0y0z0) * x_fractional;

    READ_WAVE(x0y1z0, x0 + y1 * 8 + z0 * 64)
    READ_WAVE(x1y1z0, x1 + y1 * 8 + z0 * 64)
    xy1z0 = x0y1z0 + (x1y1z0 - x0y1z0) * x_fractional;

    xyz0 = xy0z0 + (xy1z0 - xy0z0) * y_fractional;

    READ_WAVE(x0y0z1, x0 + y0 * 8 + z1 * 64)
    READ_WAVE(x1y0z1, x1 + y0 * 8 + z1 * 64)
    xy0z1 = x0y0z1 + (x1y0z1 - x0y0z1) * x_fractional;

    READ_WAVE(x0y1z1, x0 + y1 * 8 + z1 * 64)
    READ_WAVE(x1y1z1, x1 + y1 * 8 + z1 * 64)
    xy1z1 = x0y1z1 + (x1y1z1 - x0y1z1) * x_fractional;

    xyz1 = xy0z1 + (xy1z1 - xy0z1) * y_fractional;

    mix = xyz0 + (xyz1 - xyz0) * z_fractional;
    ONE_POLE(d_lp, mix - d_previous, cutoff)  /* diff_out_.Process(cutoff, mix) */
    d_previous = mix;
    mix = d_lp * gain;
    *out++ = mix;
    *aux++ = itof_small((int)(mix * 32.0f)) / 32.0f;
  }
  s->diff_out_.lp_ = d_lp;
  s->diff_out_.previous_ = d_previous;
  commit(&x_modulation);
  commit(&y_modulation);
  commit(&z_modulation);
  commit(&f0_modulation);
}
