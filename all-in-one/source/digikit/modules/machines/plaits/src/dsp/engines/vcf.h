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
// PLAITS for the Digitakt II: VCF-VA (model 16), see vcf.c. State: two oscillators (VariableShapeOscillator without the
// master phase and the phase modulation), two filters (stage 1 low and high pass, stage 2 low pass) and five
// interpolator states. Runs on between notes; reads neither the trigger nor the accent.
#ifndef PLAITS_VCF_H
#define PLAITS_VCF_H
#include "engine.h"

typedef struct {
  float slave_phase_, next_sample_, previous_pw_;
  int high_;
  float slave_frequency_, pw_, waveshape_;
} VcfOsc;

/* The two filters as a struct of arrays, a pair per field (stage 1 in lane 0, stage 2 in lane 1), first in the struct
 * so the pair is contiguous and 8-byte aligned for a later kernel. Same arithmetic as Svf (lib/svf.h). */
typedef struct {
  float g_[2], r_[2], h_[2], state_1_[2], state_2_[2];
} VcfSvf2;

typedef struct {
  VcfSvf2 svf_;
  VcfOsc oscillator_, sub_oscillator_;
  float previous_cutoff_, previous_stage2_gain_, previous_q_, previous_gain_, previous_sub_gain_;
} Vcf;

#ifdef __cplusplus
extern "C" {
#endif
void vcf_init(Vcf *s);
void vcf_render(Vcf *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_VCF_H
