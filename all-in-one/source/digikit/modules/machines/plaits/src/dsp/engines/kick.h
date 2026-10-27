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
// PLAITS for the Digitakt II: Plaits' BassDrumEngine (model KICK: an analog 808-style bass drum on OUT and a synthetic one
// on AUX; HARMONICS the amount of attack FM, self FM and drive, TIMBRE the tone, MORPH the decay) in C99, from
// plaits/dsp/engine/bass_drum_engine.cc and plaits/dsp/drums/{analog,synthetic}_bass_drum.h and fx/overdrive.h.
// The trigger is Plaits' RISING_EDGE (the wrapper's note-on); the sustain branches (the trigger UNPATCHED) are not
// ported: the wrapper never sets it. The 0.4 - 0.25 morph^2 dirtiness is therefore the only one.
#ifndef PLAITS_KICK_H
#define PLAITS_KICK_H
#include "engine.h"
#include "../lib/svf.h"

typedef struct {
  int pulse_remaining_samples_, fm_pulse_remaining_samples_;
  float pulse_, pulse_height_, pulse_lp_, fm_pulse_lp_, retrig_pulse_, lp_out_, tone_lp_;
  Svf resonator_;
} AnalogBassDrum;

typedef struct {
  float lp_, hp_;
  Svf filter_;
} SyntheticBassDrumClick;

typedef struct {
  float f0_, phase_, phase_noise_, fm_, fm_lp_, body_env_, body_env_lp_, transient_env_, transient_env_lp_, tone_lp_;
  SyntheticBassDrumClick click_;
  float noise_lp_, noise_hp_;  /* SyntheticBassDrumAttackNoise */
  int body_env_pulse_width_, fm_pulse_width_;
} SyntheticBassDrum;

typedef struct {
  AnalogBassDrum analog_;
  SyntheticBassDrum synthetic_;
  float pre_gain_, post_gain_;  /* Overdrive */
  unsigned int rng_;
} Kick;

#ifdef __cplusplus
extern "C" {
#endif
/* Init (Reset is empty). */
void kick_init(Kick *s);
void kick_render(Kick *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_KICK_H
