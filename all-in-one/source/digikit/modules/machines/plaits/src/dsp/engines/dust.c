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
//
// PLAITS for the Digitakt II: Plaits' ParticleEngine in C99 (dust.h).
#include "dust.h"
#include "noise.h"
#include "../lib/drums.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#define DUST_MASK 8191

#if defined(__ADSP21569__) && !defined(PL_DUST_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: the particle loop, the post filter and the diffuser run in lib/dust_sm.s, bit-identical in output to
   the C below, which stays for the host and as the -DPL_DUST_C reference (tests/plaits/test_dust_kernel.py). The
   kernel never reads delay memory the C would still read as cleared (it counts fresh_), so dust_init does not clear
   buffer_: the model-change block's 16 KB clear is gone. */
#define PL_DUST_KERNEL 1
void dust_particles_kernel(Dust *s, float *k, float *out, float *aux, int size);
void dust_tail_kernel(Dust *s, const float *k, float *out, int size);
void dust_clear_kernel(unsigned int *b, int words);
#endif

/* Decompress(buffer[ix & MASK]): the 16-bit sample as a signed value / 4096 (the two's complement halfword is mapped to
 * value + 32768 by flipping its top bit, then converted without a signed FLOAT). */
#define DUST_READ(ix, dst)                                                         \
  {                                                                                \
    int i_ = (ix) & DUST_MASK;                                                     \
    unsigned int w_ = buf[i_ >> 1];                                                \
    unsigned int r_ = (i_ & 1) ? (w_ >> 16) : (w_ & 0xFFFFu);                      \
    dst = (itof_nonneg((int)(r_ ^ 0x8000u)) - 32768.0f) * 0.000244140625f;         \
  }

/* buffer[ix & MASK] = Compress(value): Clip16(int(value * 4096)) (truncation toward zero; NaN stores 0). */
#define DUST_WRITE(ix, value)                                                      \
  {                                                                                \
    float x_ = (value) * 4096.0f;                                                  \
    int n_;                                                                        \
    int i_ = (ix) & DUST_MASK;                                                     \
    unsigned int r_, w_;                                                           \
    if (x_ > 32767.0f) {                                                           \
      n_ = 32767;                                                                  \
      PL_IDX(37, 1)                                                                \
    } else if (x_ >= 0.0f) {                                                       \
      n_ = (int)x_;                                                                \
    } else if (x_ < -32768.0f) {                                                   \
      n_ = -32768;                                                                 \
      PL_IDX(37, -1)                                                               \
    } else if (x_ < 0.0f) {                                                        \
      n_ = -(int)(-x_);                                                            \
    } else {                                                                       \
      n_ = 0;                                                                      \
    }                                                                              \
    r_ = (unsigned int)n_ & 0xFFFFu;                                               \
    w_ = buf[i_ >> 1];                                                             \
    if (i_ & 1) {                                                                  \
      buf[i_ >> 1] = (w_ & 0xFFFFu) | (r_ << 16);                                  \
    } else {                                                                       \
      buf[i_ >> 1] = (w_ & 0xFFFF0000u) | r_;                                      \
    }                                                                              \
  }

/* Read(d TAIL, scale): acc += r * scale, previous_read = r. */
#define DUST_READ_TAIL(tail, scale)                                                \
  {                                                                                \
    float r;                                                                       \
    DUST_READ(wp + (tail), r)                                                      \
    prev = r;                                                                      \
    acc += r * (scale);                                                            \
  }

/* WriteAllPass(d, scale): Write(d, 0, scale) then acc += previous_read. */
#define DUST_ALLPASS(base, scale)                                                  \
  {                                                                                \
    DUST_WRITE(wp + (base), acc)                                                   \
    acc *= (scale);                                                                \
    acc += prev;                                                                   \
  }

/* Interpolate(d, offset + amplitude * lfo, scale): the line at `base`, two taps with the fraction between. */
#define DUST_INTERPOLATE(tbl, base, offset, amplitude, scale)                      \
  {                                                                                \
    float o_ = (offset) + (amplitude) * lfo;                                       \
    int oi_ = (int)o_;                                                             \
    PL_IDX(tbl, oi_)                                                               \
    float of_ = o_ - itof_nonneg(oi_);                                             \
    float a_, b_, x_;                                                              \
    DUST_READ(wp + oi_ + (base), a_)                                               \
    DUST_READ(wp + oi_ + (base) + 1, b_)                                           \
    x_ = a_ + (b_ - a_) * of_;                                                     \
    prev = x_;                                                                     \
    acc += x_ * (scale);                                                           \
  }

void dust_init(Dust *s) {
  int i;
  for (i = 0; i < kDustParticles; ++i) {
    s->particle_[i].pre_gain_ = 0.0f;
    svf_init(&s->particle_[i].filter_);
  }
  svf_init(&s->post_filter_);
  s->lp_decay_ = 0.0f;
  /* SetLFOFrequency(LFO_1, 0.3f / 48000.0f): InitApproximate(0.000200000009f) gives iir_coefficient_ 1.99999869f and
   * initial_amplitude_ 0.499999672f; Start: y1_ = initial_amplitude_, y0_ = 0.5f. */
  s->lfo_y1_ = 0.499999672f;
  s->lfo_y0_ = 0.5f;
  s->rng_ = 0x21u;
  s->write_ptr_ = 0;
  s->fresh_ = 0;
#ifdef PL_DUST_KERNEL
  dust_clear_kernel(s->buffer_, kDustBufferWords);  /* one store per word (the C loop below: 7) */
#else
  {
    unsigned int *b = s->buffer_;  /* walked by pointer: the indexed store cost 14 instructions per word */
    for (i = 0; i < kDustBufferWords; ++i) {
      *b++ = 0u;
    }
  }
#endif
}

void dust_seed(Dust *s, int track) {
  int k;
  s->rng_ = 0x21u;
  for (k = 0; k < track; ++k) {
    s->rng_ += 0x9e3779b1u;  /* track * 0x9e3779b1 without an integer multiply (the encoding gate refuses one) */
  }
}

void dust_render(Dust *s, const Params *p, float *out, float *aux, int size) {
  const float f0 = NoteToFrequency(p->note);
  const float density_sqrt = NoteToFrequency(60.0f + p->timbre * p->timbre * 72.0f);
  const float density = density_sqrt * density_sqrt * 0.166666672f;  /* 1.0f / kNumParticles */
  const float gain = 1.0f / density;
  const float q_sqrt = SemitonesToRatio(p->morph >= 0.5f ? (p->morph - 0.5f) * 120.0f : 0.0f);
  const float q = 0.5f + q_sqrt * q_sqrt;
  const float spread = 48.0f * p->harmonics * p->harmonics;
  const float raw_diffusion_sqrt = 2.0f * absf(p->morph - 0.5f);
  const float raw_diffusion = raw_diffusion_sqrt * raw_diffusion_sqrt;
  const float diffusion = p->morph < 0.5f ? raw_diffusion : 0.0f;
  const int sync = p->trigger & TRIGGER_RISING_EDGE;
  const float sqrt_density = density * pl_rsqrt(density);
  const float resonance_r = 1.0f / q;
  unsigned int rng = s->rng_;
  int i, n;

#ifdef PL_DUST_KERNEL
  {
    float k[13];
    k[0] = density;
    k[1] = gain;
    k[2] = f0;
    k[3] = spread;
    k[4] = q;
    k[5] = sqrt_density;
    k[6] = resonance_r;
    ((int *)(void *)k)[7] = sync;
    k[8] = 8388608.0f;  /* the bits 0x4B000000: itof_nonneg's bias */
    k[9] = 65536.0f;
    k[10] = 4294967296.0f;
    k[11] = 2.0f;
    (void)rng;
    (void)i;
    (void)n;
    dust_particles_kernel(s, k, out, aux, size);
    svf_set_f_r(&s->post_filter_, minf(f0, 0.49f), 2.0f);
    k[0] = 0.8f * diffusion * diffusion;
    k[1] = 0.5f * diffusion + 0.25f;
    dust_tail_kernel(s, k, out, size);
    return;
  }
#endif
  for (n = 0; n < size; ++n) {
    out[n] = 0.0f;
    aux[n] = 0.0f;
  }

  for (i = 0; i < kDustParticles; ++i) {
    DustParticle *pt = &s->particle_[i];
    float u = rng_float(&rng);
    float pre_gain = pt->pre_gain_;
    float g = pt->filter_.g_, r = pt->filter_.r_, h = pt->filter_.h_;
    float state_1 = pt->filter_.state_1_, state_2 = pt->filter_.state_2_;
    int can_randomize_frequency = 1;
    if (sync) {
      u = density;
    }
    for (n = 0; n < size; ++n) {
      float sample = 0.0f;
      float in, hp, bp, lp;
      if (u <= density) {
        sample = u * gain;
        if (can_randomize_frequency) {
          const float v = 2.0f * rng_float(&rng) - 1.0f;
          const float f = minf(SemitonesToRatio(spread * v) * f0, 0.25f);
          pre_gain = 0.5f * pl_rsqrt(q * f * sqrt_density);
          /* set_f_q<FREQUENCY_DIRTY>(f, q) */
          g = f * (3.14159265358979323846f + 11.583945274353027f * f * f);
          r = resonance_r;
          h = 1.0f / (1.0f + r * g + g * g);
          can_randomize_frequency = 0;
        }
      }
      aux[n] += sample;
      in = pre_gain * sample;
      hp = (in - r * state_1 - g * state_1 - state_2) * h;
      bp = g * hp + state_1;
      state_1 = g * hp + bp;
      lp = g * bp + state_2;
      state_2 = g * bp + lp;
      out[n] += bp;
      u = rng_float(&rng);
    }
    pt->pre_gain_ = pre_gain;
    pt->filter_.g_ = g;
    pt->filter_.r_ = r;
    pt->filter_.h_ = h;
    pt->filter_.state_1_ = state_1;
    pt->filter_.state_2_ = state_2;
  }
  s->rng_ = rng;

  /* post_filter_.set_f_q<FREQUENCY_DIRTY>(min(f0, 0.49f), 0.5f); Process<FILTER_MODE_LOW_PASS>(out, out, size) */
  svf_set_f_r(&s->post_filter_, minf(f0, 0.49f), 2.0f);
  {
    Svf *f = &s->post_filter_;
    const float g = f->g_, r = f->r_, h = f->h_;
    float state_1 = f->state_1_, state_2 = f->state_2_;
    for (n = 0; n < size; ++n) {
      float hp = (out[n] - r * state_1 - g * state_1 - state_2) * h;
      float bp = g * hp + state_1;
      float lp;
      state_1 = g * hp + bp;
      lp = g * bp + state_2;
      state_2 = g * bp + lp;
      out[n] = lp;
    }
    f->state_1_ = state_1;
    f->state_2_ = state_2;
  }

  /* diffuser_.Process(0.8f * diffusion * diffusion, 0.5f * diffusion + 0.25f, out, size) */
  {
    const float amount = 0.8f * diffusion * diffusion;
    const float rt = 0.5f * diffusion + 0.25f;
    unsigned int *buf = s->buffer_;
    float lp = s->lp_decay_;
    float y1 = s->lfo_y1_, y0 = s->lfo_y0_;
    int wp = s->write_ptr_;
    for (n = 0; n < size; ++n) {
      float acc, prev, lfo, wet, in = out[n];
      --wp;
      if (wp < 0) {
        wp += 8192;
      }
      if ((wp & 31) == 0) {
        float t = y0;
        y0 = 1.99999869f * y0 - y1;
        y1 = t;
        lfo = t + 0.5f;
      } else {
        lfo = y1 + 0.5f;
      }
      prev = 0.0f;
      acc = in;
      DUST_READ_TAIL(125, 0.625f)       /* ap1: base 0, length 126 */
      DUST_ALLPASS(0, -0.625f)
      DUST_READ_TAIL(306, 0.625f)       /* ap2: base 127, length 180 */
      DUST_ALLPASS(127, -0.625f)
      DUST_READ_TAIL(576, 0.625f)       /* ap3: base 308, length 269 */
      DUST_ALLPASS(308, -0.625f)
      DUST_INTERPOLATE(35, 578, 400.0f, 43.0f, 0.625f)  /* ap4: base 578, length 444 */
      DUST_ALLPASS(578, -0.625f)
      DUST_INTERPOLATE(36, 4688, 3070.0f, 340.0f, rt)   /* del: base 4688, length 3411 */
      lp += 0.75f * (acc - lp);
      acc = lp;
      DUST_READ_TAIL(2675, -0.625f)     /* dapa: base 1023, length 1653 */
      DUST_ALLPASS(1023, 0.625f)
      DUST_READ_TAIL(4686, 0.625f)      /* dapb: base 2677, length 2010 */
      DUST_ALLPASS(2677, -0.625f)
      DUST_WRITE(wp + 4688, acc)        /* Write(del, 2.0f) */
      acc *= 2.0f;
      wet = acc;                        /* Write(wet, 0.0f) */
      out[n] = in + amount * (wet - in);
    }
    s->lp_decay_ = lp;
    s->lfo_y1_ = y1;
    s->lfo_y0_ = y0;
    s->write_ptr_ = wp;
  }
}
