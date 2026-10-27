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
//
// -----------------------------------------------------------------------------
//
// PLAITS for the Digitakt II: Plaits' six-operator FM engine (the DX7 models: SixOpEngine, plaits/dsp/engine2/
// six_op_engine.{h,cc}) with plaits/dsp/fm/{voice,operator,envelope,lfo,algorithms,dx_units,patch}.h, in C99.
// Four models share it, a bank each (PLAITS models 18, 19, 20 = upstream's three built-in banks, 24 = the owner's bank:
// lib/dx7_banks.h); the wrapper picks the bank at the engine's initialisation (six_op_load_bank).
// What is ported and how (the details are in six_op.c):
//   - one voice's render is upstream's Voice<6>::Render with its RenderOperators variants written as one generic
//     function; the algorithms are the const table pl_dx_calls compiled by the generator from upstream's opcodes;
//   - every per-patch conversion of dx_units.h (envelope rates and levels, LFO rate and delay) is a 100-entry table
//     computed in float32 by the generator in upstream's operation order (no integer multiply or divide on the DSP);
//   - the sustain ("unpatched") mode of Voice<6> and the LFO's Scrub are not ported: the wrapper never gives this engine
//     TRIGGER_UNPATCHED (plaits.c), as for the strike engines (PORTING.md drift 7);
//   - stmlib's global Random (the LFO's sample-and-hold) is one state per engine, 0x21 at init as at Plaits' power-up.
// Layout (PORTING.md section 5): the six operators are arrays of six (3 pairs) in one voice, 8-byte aligned.
#ifndef PLAITS_SIX_OP_H
#define PLAITS_SIX_OP_H
#include "engine.h"

/* One unpacked DX7 voice (fm::Patch), every field an int. */
typedef struct {
  int env_rate[4], env_level[4];
  int kb_break_point, kb_left_depth, kb_right_depth, kb_left_curve, kb_right_curve;
  int rate_scaling, amp_mod_sensitivity, velocity_sensitivity, level;
  int mode, coarse, fine, detune;
} SixOpPatchOp;

typedef struct {
  SixOpPatchOp op[6];
  int pitch_rate[4], pitch_level[4];
  int algorithm, feedback, reset_phase;
  int lfo_rate, lfo_delay, lfo_pitch_mod_depth, lfo_amp_mod_depth, lfo_reset_phase, lfo_waveform, lfo_pitch_mod_sens;
} SixOpPatch;

/* fm::Envelope<4> (operator: reshaped ascending segments; pitch: not). */
typedef struct {
  int stage;
  float phase, start;
  float increment[4], level[4];
} SixOpEnv;

/* FMVoice: fm::Lfo, fm::Voice<6>, its Parameters, and the unpacked patch it plays. */
typedef struct {
  int patch_index; /* bank index of the loaded patch, -1: none (upstream's patch_ == NULL) */
  int dirty, gate_;
  /* fm::Lfo */
  float lfo_phase, lfo_frequency, lfo_delay_phase, lfo_delay_increment[2], lfo_value, lfo_random_value;
  float lfo_amp_mod_depth, lfo_pitch_mod_depth;
  int lfo_waveform, lfo_reset_phase;
  /* fm::Voice<6>::Parameters */
  int p_gate;
  float p_note, p_velocity, p_brightness, p_envelope_control, p_pitch_mod, p_amp_mod;
  /* fm::Voice<6> */
  float normalized_velocity, note;
  unsigned int phase[6];
  float amplitude[6];
  float ratios[6], level_headroom[6], level[6];
  float feedback_state[2];
  SixOpEnv env[6];
  SixOpEnv pitch_env;
  SixOpPatch patch;
} SixOpVoice;

typedef struct {
  int quantized_value; /* unused (upstream's hysteresis state; the patch quantiser is memoryless, six_op_patch_index); kept for the layout */
  int bank;            /* 0..3: the bank of lib/dx7_banks.h */
  int active_voice, rendered_voice;
  unsigned int rng_;
  float temp[96]; /* temp_buffer_: three buffers of 2 * size floats (size <= 16) */
  float acc[32];  /* acc_buffer_ (the staggered rendering's carry), 2 * size floats */
  SixOpVoice voice[2];
} SixOp;

#ifdef __cplusplus
extern "C" {
#endif
/* Init (bank 0 loaded, as LoadUserData of upstream's first bank), then Reset (empty). */
void six_op_init(SixOp *s);
/* LoadUserData of a bank (0..3): both voices unload their patch. Used by the wrapper right after six_op_init. */
void six_op_load_bank(SixOp *s, int bank);
/* The patch (0..31) that HARMONICS (unit(word), 0..1) selects: memoryless, boundaries from lib/dx7_banks.c (pl_dx7_patch_thr). */
int six_op_patch_index(float harmonics);
/* Reseed the engine's generator for a track: 0x21 + track * 0x9e3779b1 (track 0: 0x21, as the oracle), after init. */
void six_op_seed(SixOp *s, int track);
/* trigger: RISING_EDGE (1) starts a voice, HIGH (4) is the gate; p->accent is the velocity. size <= 16. */
void six_op_render(SixOp *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_SIX_OP_H
