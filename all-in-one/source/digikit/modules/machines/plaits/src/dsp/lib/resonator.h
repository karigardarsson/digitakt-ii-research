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
// PLAITS for the Digitakt II: Plaits' ResonatorSvf and Resonator (plaits/dsp/physical_modelling/resonator.{h,cc},
// "taken from Rings' code but with fixed position") in C99, for the RINGS model. The template ResonatorSvf<batch_size>
// becomes two state types: batch 1 (ModalVoice's excitation filter, low-pass, written) and batch 4 (the mode filters,
// band-pass, added), each with the one Process instance Plaits uses.

#ifndef PLAITS_LIB_RESONATOR_H
#define PLAITS_LIB_RESONATOR_H

#define kMaxNumModes 24
#define kModeBatchSize 4

typedef struct { float state_1_, state_2_; } ResonatorSvf1;
typedef struct { float state_1_[kModeBatchSize], state_2_[kModeBatchSize]; } ResonatorSvf4;

typedef struct {
  int resolution_;
  float mode_amplitude_[kMaxNumModes];
  ResonatorSvf4 mode_filters_[kMaxNumModes / kModeBatchSize];
} Resonator;

void resonator_svf1_init(ResonatorSvf1 *s);
/* ResonatorSvf<1>::Process<FILTER_MODE_LOW_PASS, false> (in and out may be the same buffer). */
void resonator_svf1_low_pass(ResonatorSvf1 *s, float f, float q, float gain, const float *in, float *out, int size);
void resonator_svf4_init(ResonatorSvf4 *s);
/* ResonatorSvf<4>::Process<FILTER_MODE_BAND_PASS, true>. */
void resonator_svf4_band_pass_add(ResonatorSvf4 *s, const float *f, const float *q, const float *gain, const float *in,
                                  float *out, int size);
void resonator_init(Resonator *s, float position, int resolution);
/* Adds the modes' response to `in` into out. */
void resonator_process(Resonator *s, float f0, float structure, float brightness, float damping, const float *in,
                       float *out, int size);
#endif  // PLAITS_LIB_RESONATOR_H
