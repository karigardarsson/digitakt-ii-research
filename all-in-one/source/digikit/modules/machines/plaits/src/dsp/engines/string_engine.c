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
// PLAITS for the Digitakt II: Plaits' StringEngine in C99 (string_engine.h).
#include "string_engine.h"
#include "../lib/drums.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_STRING_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: string_process's sample loop and string_voice_render's excitation loops run in lib/string_sm.s,
   bit-identical in output to the C below, which stays for the host and as the -DPL_STRING_C reference
   (tests/plaits/test_string_kernel.py). The delay lines are not cleared at init (fresh_string_, fresh_stretch_). */
#define PL_STRING_KERNEL 1
void string_loop_kernel(StringVoice *v, float *k, const float *in, float *out, int size, unsigned int *rng,
                        int dispersion);
void string_excite_kernel(Svf *f, float *temp, float *aux, int size, int noise_samples, unsigned int *rng, float *k);
#endif

#define STRING_MASK (kStringDelayLineSize - 1)
#define STRETCH_MASK (kStringStretchSize - 1)
#define kStringMaxSize 16

/* DelayLine::Write: line[wp] = sample, then wp = (wp - 1 + max) % max (a power of two: a mask). */
#define DL_WRITE(line, wp, mask, sample) { (line)[wp] = (sample); wp = (wp - 1) & (mask); }

/* DelayLine::ReadHermite(delay): the four taps around write_ptr + integral, as upstream (indices wrap through the mask,
 * which equals upstream's size_t modulo for a power of two). delay >= 0. */
static float dl_read_hermite(const float *line, int wp, int mask, float delay) {
  int integral = (int)delay;
  float f = delay - itof_nonneg(integral);
  int t = wp + integral + (mask + 1);
  float xm1 = line[(t - 1) & mask];
  float x0 = line[t & mask];
  float x1 = line[(t + 1) & mask];
  float x2 = line[(t + 2) & mask];
  float c = (x1 - xm1) * 0.5f;
  float v = x0 - x1;
  float w = c + v;
  float a = w + v + (x2 - x0) * 0.5f;
  float b_neg = w + a;
  return (((a * f) - b_neg) * f + c) * f + x0;
}

/* DelayLine::Read(delay): linear interpolation. */
static float dl_read(const float *line, int wp, int mask, float delay) {
  int integral = (int)delay;
  float fractional = delay - itof_nonneg(integral);
  float a = line[(wp + integral) & mask];
  float b = line[(wp + integral + 1) & mask];
  return a + (b - a) * fractional;
}

static void string_voice_init(StringVoice *v) {
  float *b;
  int i;
  svf_init(&v->excitation_filter_);
  v->remaining_noise_samples_ = 0;
  v->delay_ = 100.0f;
#ifdef PL_STRING_KERNEL
  /* The kernel never reads a word the C would still read as cleared (it counts the writes), so the 5 KB clear of
     each string is skipped. */
  (void)b;
  (void)i;
#else
  /* Clear through a pointer (`*b++ = 0`): an indexed store compiles to about 14 instructions per word (drift 24). */
  b = v->string_;
  for (i = 0; i < kStringDelayLineSize; ++i) *b++ = 0.0f;
  b = v->stretch_;
  for (i = 0; i < kStringStretchSize; ++i) *b++ = 0.0f;
#endif
  v->fresh_string_ = 0;
  v->fresh_stretch_ = 0;
  v->string_write_ptr_ = 0;
  v->stretch_write_ptr_ = 0;
  svf_init(&v->iir_damping_filter_);
  v->dc_x_ = 0.0f;
  v->dc_y_ = 0.0f;
  v->dispersion_noise_ = 0.0f;
  v->curved_bridge_ = 0.0f;
  v->out_sample_[0] = 0.0f;
  v->out_sample_[1] = 0.0f;
  v->src_phase_ = 0.0f;
}

void string_engine_init(StringEngine *s) {
  int i;
  for (i = 0; i < kNumStrings; ++i) {
    string_voice_init(&s->voice_[i]);
    s->f0_[i] = 0.01f;
  }
  s->active_string_ = kNumStrings - 1;
  for (i = 0; i < 16; ++i) s->f0_delay_[i] = 0.0f;
  s->f0_write_ptr_ = 0;
  s->rng_ = 0x21u;
}

void string_engine_seed(StringEngine *s, int track) {
  int k;
  s->rng_ = 0x21u;
  for (k = 0; k < track; ++k) {
    s->rng_ += 0x9e3779b1u;  /* track * 0x9e3779b1 without an integer multiply (the encoding gate refuses one) */
  }
}

/* String::Process: ProcessInternal<CURVED_BRIDGE> for non_linearity_amount <= 0 (called with its negation), else <DISPERSION>.
 * out += the string's output. */
static void string_process(StringVoice *v, unsigned int *rng, float f0, float non_linearity_amount, float brightness,
                           float damping, const float *in, float *out, int size) {
  float delay, src_ratio, damping_cutoff, damping_f, damping_compensation, stretch_point, stretch_correction;
  float noise_amount_sqrt, noise_amount, noise_filter, bridge_curving_sqrt, bridge_curving, ap_gain;
  Interp delay_modulation;
  float src_phase, curved_bridge, dispersion_noise, out0, out1;
  int swp, awp;
  int dispersion = 0;

  if (non_linearity_amount > 0.0f) {
    dispersion = 1;
  }
  if (!dispersion) non_linearity_amount = -non_linearity_amount;
  if (f0 > 0.0f) {
    delay = 1.0f / f0;
  } else {
    delay = 1020.0f;
  }
  CONSTRAIN(delay, 4.0f, 1020.0f); /* kDelayLineSize - 4 */

  /* Not enough delay time in the delay line (f0 < 1 / 1020): play the lowest note and upsample with a linear interpolator. */
  src_ratio = delay * f0;
  if (src_ratio >= 0.9999f) {
    v->src_phase_ = 1.0f;
    src_ratio = 1.0f;
  }

  damping_cutoff = minf(12.0f + damping * damping * 60.0f + brightness * 24.0f, 84.0f);
  damping_f = minf(f0 * SemitonesToRatio(damping_cutoff), 0.499f);

  /* Crossfade to infinite decay. */
  if (damping >= 0.95f) {
    float to_infinite = 20.0f * (damping - 0.95f);
    brightness += to_infinite * (1.0f - brightness);
    damping_f += to_infinite * (0.4999f - damping_f);
    damping_cutoff += to_infinite * (128.0f - damping_cutoff);
  }

  /* set_f_q<FREQUENCY_FAST>(damping_f, 0.5f) */
  svf_set_g_q(&v->iir_damping_filter_, tan_fast(damping_f), 0.5f);

  PL_IDX(39, (int)damping_cutoff)
  damping_compensation = Interpolate(PL_F(pl_lut_svf_shift), damping_cutoff, 1.0f);

  /* Linearly interpolate the delay time. */
  delay_modulation = interp(&v->delay_, delay * damping_compensation, size);

  stretch_point = non_linearity_amount * (2.0f - non_linearity_amount) * 0.225f;
  stretch_correction = 0.00333333341f * delay;  /* (160.0f / kSampleRate) * delay */
  CONSTRAIN(stretch_correction, 1.0f, 2.1f);

  noise_amount_sqrt = non_linearity_amount > 0.75f ? 4.0f * (non_linearity_amount - 0.75f) : 0.0f;
  noise_amount = noise_amount_sqrt * noise_amount_sqrt * 0.1f;
  noise_filter = 0.06f + 0.94f * brightness * brightness;

  bridge_curving_sqrt = non_linearity_amount;
  bridge_curving = bridge_curving_sqrt * bridge_curving_sqrt * 0.01f;

  ap_gain = -0.618f * non_linearity_amount / (0.15f + absf(non_linearity_amount));

#ifdef PL_STRING_KERNEL
  {
    float k[24];
    k[0] = src_ratio;
    k[1] = delay_modulation.value;
    k[2] = delay_modulation.increment;
    k[3] = noise_filter;
    k[4] = noise_amount;
    k[5] = bridge_curving;
    k[6] = stretch_point;
    k[7] = 0.408f - stretch_point * 0.308f;
    k[8] = stretch_correction;
    k[9] = ap_gain;
    k[10] = 1.0f;
    k[11] = 0.5f;
    k[12] = 4.0f;
    k[13] = 0.025f;
    k[14] = -1.5f;
    k[15] = -20.0f;
    k[16] = 20.0f;
    k[17] = 0.999583304f;
    k[18] = 8388608.0f;
    k[19] = 65536.0f;
    k[20] = 0.0f;
    k[21] = 4294967296.0f;
    k[22] = 2.0f;
    k[23] = 0.0f;
    string_loop_kernel(v, k, in, out, size, rng, dispersion);
    delay_modulation.value = k[1];
    commit(&delay_modulation);
    (void)src_phase;
    (void)curved_bridge;
    (void)dispersion_noise;
    (void)out0;
    (void)out1;
    (void)swp;
    (void)awp;
  }
#else
  src_phase = v->src_phase_;
  curved_bridge = v->curved_bridge_;
  dispersion_noise = v->dispersion_noise_;
  out0 = v->out_sample_[0];
  out1 = v->out_sample_[1];
  swp = v->string_write_ptr_;
  awp = v->stretch_write_ptr_;

  while (size--) {
    src_phase += src_ratio;
    if (src_phase > 1.0f) {
      float d, s;
      src_phase -= 1.0f;

      d = next(&delay_modulation);
      s = 0.0f;

      if (dispersion) {
        float noise = rng_float(rng) - 0.5f;
        ONE_POLE(dispersion_noise, noise, noise_filter)
        d *= 1.0f + dispersion_noise * noise_amount;
      } else {
        d *= 1.0f - curved_bridge * bridge_curving;
      }

      if (dispersion) {
        float ap_delay = d * stretch_point;
        float main_delay = d - ap_delay * (0.408f - stretch_point * 0.308f) * stretch_correction;
        if (ap_delay >= 4.0f && main_delay >= 4.0f) {
          int ap_int = (int)ap_delay;
          float rd, wr;
          PL_IDX(40, ap_int)
          s = dl_read(v->string_, swp, STRING_MASK, main_delay);
          /* stretch_.Allpass(s, ap_delay, ap_gain) */
          rd = v->stretch_[(awp + ap_int) & STRETCH_MASK];
          wr = s + ap_gain * rd;
          DL_WRITE(v->stretch_, awp, STRETCH_MASK, wr)
          s = -wr * ap_gain + rd;
        } else {
          s = dl_read_hermite(v->string_, swp, STRING_MASK, d);
        }
      } else {
        s = dl_read_hermite(v->string_, swp, STRING_MASK, d);
      }

      if (!dispersion) {
        float value = absf(s) - 0.025f;
        float sign = s > 0.0f ? 1.0f : -1.5f;
        curved_bridge = (absf(value) + value) * sign;
      }

      s += *in;
      CONSTRAIN(s, -20.0f, 20.0f);

      /* dc_blocker_.Process(&s, 1), pole 1.0f - 20.0f / kSampleRate */
      {
        float old_x = v->dc_x_;
        v->dc_x_ = s;
        v->dc_y_ = v->dc_y_ * 0.999583304f + s - old_x;
        s = v->dc_y_;
      }
      /* iir_damping_filter_.Process<FILTER_MODE_LOW_PASS>(s) */
      {
        Svf *f = &v->iir_damping_filter_;
        float hp = (s - f->r_ * f->state_1_ - f->g_ * f->state_1_ - f->state_2_) * f->h_;
        float bp = f->g_ * hp + f->state_1_;
        float lp;
        f->state_1_ = f->g_ * hp + bp;
        lp = f->g_ * bp + f->state_2_;
        f->state_2_ = f->g_ * bp + lp;
        s = lp;
      }
      DL_WRITE(v->string_, swp, STRING_MASK, s)

      out1 = out0;
      out0 = s;
    }
    *out++ += out1 + (out0 - out1) * src_phase;
    in++;
  }

  commit(&delay_modulation);
  v->src_phase_ = src_phase;
  v->curved_bridge_ = curved_bridge;
  v->dispersion_noise_ = dispersion_noise;
  v->out_sample_[0] = out0;
  v->out_sample_[1] = out1;
  v->string_write_ptr_ = swp;
  v->stretch_write_ptr_ = awp;
#endif
}

/* StringVoice::Render with sustain false. */
static void string_voice_render(StringVoice *v, unsigned int *rng, int trigger, float accent, float f0, float structure,
                                float brightness, float damping, float *temp, float *out, float *aux, int size) {
  float non_linearity;
  int i;

  brightness += 0.25f * accent * (1.0f - brightness);
  damping += 0.25f * accent * (1.0f - damping);

  /* Synthesize the excitation signal. */
  if (trigger) {
    const float range = 72.0f;
    const float f = 4.0f * f0;
    const float cutoff = minf(f * SemitonesToRatio((brightness * (2.0f - brightness) - 0.5f) * range), 0.499f);
    v->remaining_noise_samples_ = (int)(1.0f / f0);
    /* set_f_q<FREQUENCY_DIRTY>(cutoff, 0.5f) */
    svf_set_g_q(&v->excitation_filter_, tan_dirty(cutoff), 0.5f);
  }

#ifdef PL_STRING_KERNEL
  {
    int noise_samples = v->remaining_noise_samples_ < size ? v->remaining_noise_samples_ : size;
    float k[8];
    v->remaining_noise_samples_ -= noise_samples;
    k[0] = 8388608.0f;
    k[1] = 65536.0f;
    k[2] = 0.0f;
    k[3] = 4294967296.0f;
    k[4] = 2.0f;
    k[5] = 1.0f;
    k[6] = 0.0f;
    k[7] = 0.0f;
    string_excite_kernel(&v->excitation_filter_, temp, aux, size, noise_samples, rng, k);
    (void)i;
  }
#else
  if (v->remaining_noise_samples_ != 0) {
    int noise_samples = v->remaining_noise_samples_ < size ? v->remaining_noise_samples_ : size;
    int tail;
    float *start = temp;
    v->remaining_noise_samples_ -= noise_samples;
    tail = size - noise_samples;
    while (noise_samples--) {
      *start++ = 2.0f * rng_float(rng) - 1.0f;
    }
    while (tail--) {
      *start++ = 0.0f;
    }
  } else {
    for (i = 0; i < size; ++i) temp[i] = 0.0f;
  }

  /* excitation_filter_.Process<FILTER_MODE_LOW_PASS>(temp, temp, size) */
  {
    Svf *f = &v->excitation_filter_;
    float g = f->g_, r = f->r_, h = f->h_;
    float state_1 = f->state_1_, state_2 = f->state_2_;
    for (i = 0; i < size; ++i) {
      float hp = (temp[i] - r * state_1 - g * state_1 - state_2) * h;
      float bp = g * hp + state_1;
      float lp;
      state_1 = g * hp + bp;
      lp = g * bp + state_2;
      state_2 = g * bp + lp;
      temp[i] = lp;
    }
    f->state_1_ = state_1;
    f->state_2_ = state_2;
  }
  for (i = 0; i < size; ++i) {
    aux[i] += temp[i];
  }
#endif

  non_linearity = structure < 0.24f ? (structure - 0.24f) * 4.166f
                                    : (structure > 0.26f ? (structure - 0.26f) * 1.35135f : 0.0f);
  string_process(v, rng, f0, non_linearity, brightness, damping, temp, out, size);
}

void string_engine_render(StringEngine *s, const Params *p, float *out, float *aux, int size) {
  float temp[kStringMaxSize];
  float f0;
  int i;

  if (p->trigger & TRIGGER_RISING_EDGE) {
    /* 8 in the original firmware version; 14 since 05.01.18 (problem with the Microbrute). Read(14): an exact tap. */
    s->f0_[s->active_string_] = dl_read(s->f0_delay_, s->f0_write_ptr_, 15, 14.0f);
    s->active_string_ = s->active_string_ == kNumStrings - 1 ? 0 : s->active_string_ + 1;
  }

  f0 = NoteToFrequency(p->note);
  s->f0_[s->active_string_] = f0;
  DL_WRITE(s->f0_delay_, s->f0_write_ptr_, 15, f0)

  for (i = 0; i < size; ++i) {
    out[i] = 0.0f;
    aux[i] = 0.0f;
  }

  for (i = 0; i < kNumStrings; ++i) {
    string_voice_render(&s->voice_[i], &s->rng_, (p->trigger & TRIGGER_RISING_EDGE) != 0 && i == s->active_string_,
                        p->accent, s->f0_[i], p->harmonics, p->timbre * p->timbre, p->morph, temp, out, aux, size);
  }
}
