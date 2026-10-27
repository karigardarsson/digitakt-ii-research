// Copyright 2021 Emilie Gillet.
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
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' Ensemble in C99 (lib/ensemble.h). Differences from upstream, none of which
// changes a result on the host:
//   - the FxEngine context is written out: the accumulator is a local, and the sequence Read, Write(line, 0.0f), Read
//     (the first sum is the dry sample, the second the wet taps) reduces to the same sums because the accumulator
//     is multiplied by the scale 0.0f in between (only a non-finite input would differ: it would then be NaN in
//     upstream; the input is the output of a stable filter);
//   - the upstream LFO of FxEngine (a cosine oscillator that Ensemble does not use) is not ported;
//   - the loop-invariant amplitudes (dry amount, the two LFO depths) are hoisted out of the sample loop;
//   - int-to-float conversions go through dsp_util.h `itof_nonneg` (the delay offsets are 16..368 samples).
// Index ranges: the reads are masked into the 1024-float buffer, and the delay offset (192 + the LFO sum, whose depth is
// at most 0.35 + 0.65 = 1: at most +-176) is between 16 and 368, inside the 511-sample lines (the test records it).
#include "ensemble.h"
#include "units.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#define ENS_MASK (kEnsembleBufferSize - 1)

/* acc += Interpolate(line at `base`, offset) * 0.33f, written out (no calls: Selache's C has no inlining). */
#define ENS_TAP(acc, base, offset)                                                \
  {                                                                               \
    float o_ = (offset);                                                          \
    int oi_ = (int)o_;                                                            \
    float of_ = o_ - itof_nonneg(oi_);                                            \
    int ix_ = (wp + oi_ + (base)) & ENS_MASK;                                     \
    float a_ = buf[ix_];                                                          \
    float b_ = buf[(ix_ + 1) & ENS_MASK];                                         \
    PL_IDX(34, oi_)                                                               \
    acc += (a_ + (b_ - a_) * of_) * 0.33f;                                        \
  }

void ensemble_init(Ensemble *e) {
  int i;
#if defined(__ADSP21569__) && !defined(PL_SM_C) && !defined(PL_INDEX_CHECK)
  /* The DSP build's chorus (lib/string_machine_sm.s) never reads a word the C would still read as cleared (it counts
     fresh_), so the 4 KB clear of the model-change block is skipped. */
  (void)i;
#else
  for (i = 0; i < kEnsembleBufferSize; ++i) {
    e->buffer_[i] = 0.0f;
  }
#endif
  e->fresh_ = 0;
  e->write_ptr_ = 0;
  e->phase_1_ = 0;
  e->phase_2_ = 0;
}

void ensemble_process(Ensemble *e, float amount, float depth, float *left, float *right, int size) {
  const float *sine = PL_F(pl_lut_sine);
  float *buf = e->buffer_;
  const unsigned int one_third = 0x547ae147u;  /* 1417339207 */
  const unsigned int two_third = 0xa8f5c28fu;  /* 2834678415 */
  const float dry_amount = 1.0f - amount * 0.5f;
  const float a = depth * 160.0f;  /* max deviation: 176 */
  const float b = depth * 16.0f;
  int wp = e->write_ptr_;
  unsigned int phase_1 = e->phase_1_;
  unsigned int phase_2 = e->phase_2_;

  while (size--) {
    float l = *left, r = *right;
    float slow_0, slow_120, slow_240, fast_0, fast_120, fast_240;
    float mod_1, mod_2, mod_3, wet;

    --wp;
    if (wp < 0) {
      wp += kEnsembleBufferSize;
    }

    phase_1 += 67289u;   /* 0.75 Hz */
    phase_2 += 589980u;  /* 6.57 Hz */
    slow_0 = sine[phase_1 >> 23];
    slow_120 = sine[(phase_1 + one_third) >> 23];
    slow_240 = sine[(phase_1 + two_third) >> 23];
    fast_0 = sine[phase_2 >> 23];
    fast_120 = sine[(phase_2 + one_third) >> 23];
    fast_240 = sine[(phase_2 + two_third) >> 23];

    mod_1 = slow_0 * a + fast_0 * b;
    mod_2 = slow_120 * a + fast_120 * b;
    mod_3 = slow_240 * a + fast_240 * b;

    /* Sum L & R channel to send to chorus line. */
    buf[wp] = l;
    buf[(wp + 512) & ENS_MASK] = r;

    wet = 0.0f;
    ENS_TAP(wet, 0, mod_1 + 192.0f)
    ENS_TAP(wet, 0, mod_2 + 192.0f)
    ENS_TAP(wet, 512, mod_3 + 192.0f)
    *left = wet * amount + l * dry_amount;

    wet = 0.0f;
    ENS_TAP(wet, 512, mod_1 + 192.0f)
    ENS_TAP(wet, 512, mod_2 + 192.0f)
    ENS_TAP(wet, 0, mod_3 + 192.0f)
    *right = wet * amount + r * dry_amount;
    left++;
    right++;
  }
  e->write_ptr_ = wp;
  e->phase_1_ = phase_1;
  e->phase_2_ = phase_2;
}
