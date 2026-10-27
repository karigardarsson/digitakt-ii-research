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
// PLAITS for the Digitakt II: Plaits' WaveshapingEngine (ASYM-TRI) in C99. Slope -> Waveshaper -> Wavefolder.
// From plaits/dsp/engine/waveshaping_engine.cc, plaits/dsp/oscillator/oscillator.h (OSCILLATOR_SHAPE_SLOPE, no
// external FM), stmlib's ParameterInterpolator, InterpolateHermite, InterpolateWrap and polyblep; the slope
// oscillator and the waveshaper follow the dt2-machine-lab translation (MIT, the notice above). Differences from
// upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - the tables (ws_tables.h, generated from upstream resources.cc) hold IEEE bit patterns read through a float
//     pointer, and the int16 waveshaper tables are stored as v / 32768 (exact), so `static_cast<float>(t[i]) /
//     32768.0f` is `WS_F(t)[i]`;
//   - int-to-float conversions go through dsp_util.h `itof_nonneg` (exact; see there why), `itof_small` for the
//     one index that may be negative;
//   - three table indices are clamped so that no read leaves its table (upstream reads outside its tables for some
//     of these values, which is undefined behaviour; on the SHARC+ a wild read could fault): the waveshaper index
//     beyond +-2^31 or NaN, the wavefolder index outside [0, 1] or NaN, the sine phase outside [0, 2^23) or NaN. The
//     clamps are a little wider than the windows where upstream actually reads out of bounds: they may also change
//     a few values upstream handles in bounds (e.g. a wavefolder index just outside [0, 1]). Inside the clamped
//     ranges every result equals upstream's (for a sine phase >= 2^23 upstream's fractional part is 0 as here); the
//     host test's grid of corners and sweeps never reaches a clamp. Such values arise only from the slope
//     oscillator's polyblep when the pitch jumps by several octaves within a block without a note-on.
// The host test tests/plaits/test_ws_host.py compares it bit for bit with upstream Plaits built at 48,000 Hz.
#include "waveshaping.h"
#include "../dsp_util.h"
#include "pitch_tables.h"
#include "ws_tables.h"

typedef struct { float *state; float value; float increment; } Interp;
/* itof_nonneg(size) written out (selcc has no inlining: the same operations without its call and frame). */
static Interp interp(float *s, float target, int size) {
  Interp p;
  union { int i; float f; } u;
  float d = target - *s;
  u.i = 0x4B000000 | size;
  p.state = s; p.value = *s; p.increment = d / (*(float *)(void *)&u - 8388608.0f); return p;
}
static float next(Interp *p) { p->value += p->increment; return p->value; }
static void commit(Interp *p) { *p->state = p->value; }
static float ratio(float semitones) {
  float pitch=semitones+128.0f;
  MAKE_INTEGRAL_FRACTIONAL(pitch)
  return lut_pitch_ratio_high[pitch_integral] * lut_pitch_ratio_low[(int)(pitch_fractional*256.0f)];
}
static float NoteToFrequency(float note) {
  note -= 9.0f; CONSTRAIN(note,-128.0f,127.0f);
  return ((440.0f/8.0f)/48000.0f)*0.25f*ratio(note);
}

/* (float)i for |i| < 2^23 (exact). */
#if defined(__ADSP21569__) && !defined(PL_WS_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: slope_render's loop and ws_render's waveshaper loop run in lib/ws_sm.s, bit-identical to the C loops
   below, which stay for the host and as the -DPL_WS_C reference (tests/plaits/test_ws_kernel.py). */
#define PL_WS_KERNEL 1
void slope_kernel(Slope *s, const float *k, float *out, int size);
void ws_kernel(WS *s, const float *k, float *out, float *aux, int size);
#endif

static float itof_small(int i) { return i < 0 ? -itof_nonneg(-i) : itof_nonneg(i); }

static const float kMaxFrequency=0.25f;
static const float kMinFrequency=0.000001f;

static float NextIntegratedBlepSample(float t) {
  const float t1 = 0.5f * t;
  const float t2 = t1 * t1;
  const float t4 = t2 * t2;
  return 0.1875f - t1 + 1.5f * t2 - t4;
}

static float ThisIntegratedBlepSample(float t) {
  return NextIntegratedBlepSample(1.0f - t);
}

static float InterpolateHermite(const float *table, float index, float size) {
  if (!(index >= 0.0f)) { index = 0.0f; } else if (index > 1.0f) { index = 1.0f; }  // in range (see the top)
  index *= size;
  MAKE_INTEGRAL_FRACTIONAL(index)
  const float xm1 = table[index_integral - 1];
  const float x0 = table[index_integral + 0];
  const float x1 = table[index_integral + 1];
  const float x2 = table[index_integral + 2];
  const float c = (x1 - xm1) * 0.5f;
  const float v = x0 - x1;
  const float w = c + v;
  const float a = w + v + (x2 - x0) * 0.5f;
  const float b_neg = w + a;
  const float f = index_fractional;
  return (((a * f) - b_neg) * f + c) * f + x0;
}

static float Sine(float phase) {  // InterpolateWrap(lut_sine, phase, 512)
  if (!(phase >= 0.0f && phase < 8388608.0f)) { phase = 0.0f; }  // in range (see the top)
  phase -= itof_nonneg((int)phase);
  phase *= 512.0f;
  MAKE_INTEGRAL_FRACTIONAL(phase)
  const float *sine = WS_F(lut_sine);
  float a = sine[phase_integral];
  float b = sine[phase_integral + 1];
  return a + (b - a) * phase_fractional;
}

static void slope_init(Slope *s) {
  s->phase_ = 0.5f;
  s->next_sample_ = 0.0f;
  s->lp_state_ = 1.0f;
  s->hp_state_ = 0.0f;
  s->high_ = 1;
  s->frequency_ = 0.001f;
  s->pw_ = 0.5f;
}

/* Oscillator::Render<OSCILLATOR_SHAPE_SLOPE, false, false>. */
static void slope_render(Slope *s, float frequency, float pw, float *out, int size) {
  CONSTRAIN(frequency, kMinFrequency, kMaxFrequency);
  CONSTRAIN(pw, absf(frequency) * 2.0f, 1.0f - 2.0f * absf(frequency))

  Interp fm = interp(&s->frequency_, frequency, size);
  Interp pwm = interp(&s->pw_, pw, size);

#ifdef PL_WS_KERNEL
  {
    float k[2];
    k[0] = fm.increment;
    k[1] = pwm.increment;
    slope_kernel(s, k, out, size);
    return;
  }
#endif
  float next_sample = s->next_sample_;

  while (size--) {
    float this_sample = next_sample;
    next_sample = 0.0f;

    const float frequency = next(&fm);
    const float pw = next(&pwm);
    s->phase_ += frequency;

    const float slope_up = 1.0f / (pw);
    const float slope_down = 1.0f / (1.0f - pw);
    if (s->high_ ^ (s->phase_ < pw)) {
      float t = (s->phase_ - pw) / frequency;
      float discontinuity = (slope_up + slope_down) * frequency;
      this_sample -= ThisIntegratedBlepSample(t) * discontinuity;
      next_sample -= NextIntegratedBlepSample(t) * discontinuity;
      s->high_ = s->phase_ < pw;
    }
    if (s->phase_ >= 1.0f) {
      s->phase_ -= 1.0f;
      float t = s->phase_ / frequency;
      float discontinuity = (slope_up + slope_down) * frequency;
      this_sample += ThisIntegratedBlepSample(t) * discontinuity;
      next_sample += NextIntegratedBlepSample(t) * discontinuity;
      s->high_ = 1;
    }
    next_sample += s->high_
      ? s->phase_ * slope_up
      : 1.0f - (s->phase_ - pw) * slope_down;
    *out++ = 2.0f * this_sample - 1.0f;
  }
  s->next_sample_ = next_sample;
  commit(&fm);
  commit(&pwm);
}

static float Tame(float f0, float harmonics, float order) {
  f0 *= harmonics;
  float max_f = 0.5f / order;
  float max_amount = 1.0f - (f0 - max_f) / (0.5f - max_f);
  CONSTRAIN(max_amount, 0.0f, 1.0f);
  return max_amount * max_amount * max_amount;
}

/* lookup_table_i16_table[0 .. 4] (upstream resources.cc). */
static const float *ws_table(int index) {
  if (index == 0) return WS_F(lut_ws_inverse_tan);
  if (index == 1) return WS_F(lut_ws_inverse_sin);
  if (index == 2) return WS_F(lut_ws_linear);
  if (index == 3) return WS_F(lut_ws_bump);
  return WS_F(lut_ws_double_bump);
}

void ws_init(WS *s) {
  slope_init(&s->slope_);
  slope_init(&s->triangle_);
  s->previous_shape_ = 0.0f;
  s->previous_wavefolder_gain_ = 0.0f;
  s->previous_overtone_gain_ = 0.0f;
}

void ws_render(WS *s, const Params *p, float *out, float *aux, int size) {
  const float root = p->note;

  const float f0 = NoteToFrequency(root);
  const float pw = p->morph * 0.45f + 0.5f;

  // Start from bandlimited slope signal.
  slope_render(&s->slope_, f0, pw, out, size);
  slope_render(&s->triangle_, f0, 0.5f, aux, size);

  // Try to estimate how rich the spectrum is, and reduce the range of the
  // waveshaping control accordingly.
  const float slope = 3.0f + absf(p->morph - 0.5f) * 5.0f;
  const float shape_amount = absf(p->harmonics - 0.5f) * 2.0f;
  const float shape_amount_attenuation = Tame(f0, slope, 16.0f);
  const float wavefolder_gain = p->timbre;
  const float wavefolder_gain_attenuation = Tame(
      f0,
      slope * (3.0f + shape_amount * shape_amount_attenuation * 5.0f),
      12.0f);

  // Apply waveshaper / wavefolder.
  Interp shape_modulation = interp(
      &s->previous_shape_,
      0.5f + (p->harmonics - 0.5f) * shape_amount_attenuation,
      size);
  Interp wf_gain_modulation = interp(
      &s->previous_wavefolder_gain_,
      0.03f + 0.46f * wavefolder_gain * wavefolder_gain_attenuation,
      size);
  const float overtone_gain = p->timbre * (2.0f - p->timbre);
  Interp overtone_gain_modulation = interp(
      &s->previous_overtone_gain_,
      overtone_gain * (2.0f - overtone_gain),
      size);

#ifdef PL_WS_KERNEL
  {
    float k[11];
    k[0] = shape_modulation.increment;
    k[1] = wf_gain_modulation.increment;
    k[2] = overtone_gain_modulation.increment;
    ((const float **)(void *)k)[3] = ws_table(0);
    ((const float **)(void *)k)[4] = ws_table(1);
    ((const float **)(void *)k)[5] = ws_table(2);
    ((const float **)(void *)k)[6] = ws_table(3);
    ((const float **)(void *)k)[7] = ws_table(4);
    ((const float **)(void *)k)[8] = WS_F(lut_fold) + 1;
    ((const float **)(void *)k)[9] = WS_F(lut_fold_2) + 1;
    ((const float **)(void *)k)[10] = WS_F(lut_sine);
    ws_kernel(s, k, out, aux, size);
    return;
  }
#endif
  int i;
  for (i = 0; i < size; ++i) {
    float shape = next(&shape_modulation) * 3.9999f;
    MAKE_INTEGRAL_FRACTIONAL(shape)

    const float *shape_1 = ws_table(shape_integral);
    const float *shape_2 = ws_table(shape_integral + 1);

    float ws_index = 127.0f * out[i] + 128.0f;
    int ws_index_integral = 0;
    float ws_index_fractional = 0.0f;
    if (ws_index > -8388608.0f && ws_index < 8388608.0f) {  // MAKE_INTEGRAL_FRACTIONAL
      ws_index_integral = (int)ws_index;
      ws_index_fractional = ws_index - itof_small(ws_index_integral);
    } else if (ws_index >= -2147483648.0f && ws_index < 2147483648.0f) {  // integral already: fractional 0
      ws_index_integral = (int)ws_index;
    }  // else (beyond +-2^31, NaN) 0, 0: in range (see the top)
    ws_index_integral &= 255;

    float x0 = shape_1[ws_index_integral];
    float x1 = shape_1[ws_index_integral + 1];
    float x = x0 + (x1 - x0) * ws_index_fractional;

    float y0 = shape_2[ws_index_integral];
    float y1 = shape_2[ws_index_integral + 1];
    float y = y0 + (y1 - y0) * ws_index_fractional;

    float mix = x + (y - x) * shape_fractional;
    float index = mix * next(&wf_gain_modulation) + 0.5f;
    float fold = InterpolateHermite(
        WS_F(lut_fold) + 1, index, 512.0f);
    float fold_2 = -InterpolateHermite(
        WS_F(lut_fold_2) + 1, index, 512.0f);

    float sine = Sine(aux[i] * 0.25f + 0.5f);
    out[i] = fold;
    aux[i] = sine + (fold_2 - sine) * next(&overtone_gain_modulation);
  }
  commit(&shape_modulation);
  commit(&wf_gain_modulation);
  commit(&overtone_gain_modulation);
}
