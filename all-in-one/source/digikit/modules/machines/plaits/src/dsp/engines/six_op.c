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
// PLAITS for the Digitakt II: Plaits' six-operator FM engine in C99 (six_op.h).
// Differences from upstream, none of which changes a result on the host (tests/plaits/test_six_op_host.py compares it
// bit for bit with upstream at 48,000 Hz, for the four banks):
//   - the sample rate is exactly 48,000 Hz (upstream: kCorrectedSampleRate = 47,872.34 Hz): one_hz_, a0_ and the
//     envelope scale are written as the floats the C++ computes at 48,000 Hz (DX_ONE_HZ, DX_A0, DX_ENV_SCALE, checked
//     against numpy float32 by the test);
//   - dx_units.h's conversions are generated tables (lib/gen_dx_tables.py), as are the compiled algorithms;
//   - ints become floats through itof_nonneg / itof_signed (exact for |i| < 2^23), float to unsigned through ftou;
//   - Pow2Fast reads and writes the float's bits through a pointer to a union (the encoding of selcc's unions);
//   - lut_cube_root has one more entry (1.0f): Interpolate(velocity = 1.0) reads index 17, multiplied by 0;
//   - the sustain mode (TRIGGER_UNPATCHED) and the LFO's Scrub are not ported (six_op.h).
#include "six_op.h"
#include "../lib/drums.h"
#include "../lib/sine.h"
#include "../lib/dx_tables.h"
#include "../lib/dx7_banks.h"

/* Index recorder of a PL_INDEX_CHECK build (tests/plaits/oracle/port_sizes.c, tables 41..44): 41 the lut_pitch_ratio_high index
 * of semitones_to_ratio_safe (0..255), 42 the lut_cube_root index (0..17), 43 a modulation input to SinePM, * 1024 (it
 * must stay inside (-32, 32): +-32768), 44 the low table's index of the same call (0..255). */
#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_DX_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: voice_render's operator loop and its calls of render_ops run in lib/dx_sm.s (dx_voice_kernel),
   bit-identical to the C below, which stays for the host and as the -DPL_DX_C reference (tests/plaits/test_dx_kernel.py).
   DxVoiceK carries the two divisions the C computes and the tables; the kernel reads them by word offset, in this
   order, and writes render_ops' per-operator frequency and amplitude increment back into it. */
#define PL_DX_KERNEL 1
typedef struct {
  float scale, fb_scale;
  int size;
  const unsigned int *calls, *modmask, *amp_mod_lut, *ratio_high, *ratio_low, *cube_root;
  unsigned int frequency[6];
  float amplitude_increment[6];
} DxVoiceK;
void dx_voice_kernel(SixOpVoice *v, float *temp, DxVoiceK *k);
void dx_pre_kernel(float *temp, const float *acc, int size);
void dx_post_kernel(float *temp, float *out, float *aux, float *acc, int size);
void dx_unpack_kernel(SixOpPatch *p, const unsigned int *bank, int index);
void dx_env_set_kernel(SixOpEnv *e, const SixOpPatchOp *op, int global_level);
#endif

#define TRIGGER_HIGH 4
#define DX_ONE_HZ 2.08333331e-05f
#define DX_A0 0.00114583329f
#define DX_ENV_SCALE 0.918749988f
#define DX_PREVIOUS_LEVEL -100.0f

#ifndef PL_DX_KERNEL
static float itof_signed(int i) { return i < 0 ? -itof_nonneg(-i) : itof_nonneg(i); }
#endif

static float as_float(int w) {
  union { int i; float f; } u;
  u.i = w;
  return *(float *)(void *)&u;
}

static int float_bits(float x) {
  union { int i; float f; } u;
  *(float *)(void *)&u = x;
  return u.i;
}

/* fm::Pow2Fast<1> and <2> */
static float pow2_fast_1(float x) { return as_float((int)(8388608.0f * (127.0f + x))); }

#ifndef PL_DX_KERNEL
static float pow2_fast_2(float x) {
  int x_integral = (int)x;
  float r;
  if (x < 0.0f) {
    --x_integral;
  }
  x -= itof_signed(x_integral);
  r = 1.0f + x * (0.6565f + x * 0.3435f);
  return as_float(float_bits(r) + (x_integral << 23));
}
#endif

/* stmlib::SemitonesToRatioSafe */
static float semitones_to_ratio_safe(float semitones) {
  float scale = 1.0f;
  while (semitones > 120.0f) {
    semitones -= 120.0f;
    scale *= 1024.0f;
  }
  while (semitones < -120.0f) {
    semitones += 120.0f;
    scale *= 0.0009765625f;
  }
#ifdef PL_INDEX_CHECK
  {
    float pitch = semitones + 128.0f;
    MAKE_INTEGRAL_FRACTIONAL(pitch)
    PL_IDX(41, pitch_integral)
    PL_IDX(44, (int)(pitch_fractional * 256.0f))
  }
#endif
  return scale * SemitonesToRatio(semitones);
}

/* ---- the bank and fm::Patch::Unpack */

static int bank_byte(const unsigned int *bank, int off) {
  unsigned int w = bank[off >> 2];
  return (int)((w >> ((off & 3) << 3)) & 0xffu);
}

static int min99(int v) { return v < 99 ? v : 99; }

static void patch_unpack(SixOpPatch *p, int bank, int index) {
  const unsigned int *b = &pl_dx7_banks[bank * PL_DX7_BANK_WORDS];
#ifdef PL_DX_KERNEL
  dx_unpack_kernel(p, b, index);
#else
  int base = index << 7;
  int i, j;
  for (i = 0; i < 6; ++i) {
    SixOpPatchOp *o = &p->op[i];
    for (j = 0; j < 4; ++j) {
      o->env_rate[j] = min99(bank_byte(b, base + j) & 0x7f);
      o->env_level[j] = min99(bank_byte(b, base + 4 + j) & 0x7f);
    }
    o->kb_break_point = min99(bank_byte(b, base + 8) & 0x7f);
    o->kb_left_depth = min99(bank_byte(b, base + 9) & 0x7f);
    o->kb_right_depth = min99(bank_byte(b, base + 10) & 0x7f);
    o->kb_left_curve = bank_byte(b, base + 11) & 0x3;
    o->kb_right_curve = (bank_byte(b, base + 11) >> 2) & 0x3;
    o->rate_scaling = bank_byte(b, base + 12) & 0x7;
    o->amp_mod_sensitivity = bank_byte(b, base + 13) & 0x3;
    o->velocity_sensitivity = (bank_byte(b, base + 13) >> 2) & 0x7;
    o->level = min99(bank_byte(b, base + 14) & 0x7f);
    o->mode = bank_byte(b, base + 15) & 0x1;
    o->coarse = (bank_byte(b, base + 15) >> 1) & 0x1f;
    o->fine = min99(bank_byte(b, base + 16) & 0x7f);
    {
      int d = (bank_byte(b, base + 12) >> 3) & 0xf;
      o->detune = d < 14 ? d : 14;
    }
    base += 17; /* i * 17 without a multiply */
  }
  base = (index << 7) + 102;
  for (j = 0; j < 4; ++j) {
    p->pitch_rate[j] = min99(bank_byte(b, base + j) & 0x7f);
    p->pitch_level[j] = min99(bank_byte(b, base + 4 + j) & 0x7f);
  }
  base = (index << 7) + 110;
  p->algorithm = bank_byte(b, base) & 0x1f;
  p->feedback = bank_byte(b, base + 1) & 0x7;
  p->reset_phase = (bank_byte(b, base + 1) >> 3) & 0x1;
  p->lfo_rate = min99(bank_byte(b, base + 2) & 0x7f);
  p->lfo_delay = min99(bank_byte(b, base + 3) & 0x7f);
  p->lfo_pitch_mod_depth = min99(bank_byte(b, base + 4) & 0x7f);
  p->lfo_amp_mod_depth = min99(bank_byte(b, base + 5) & 0x7f);
  p->lfo_reset_phase = bank_byte(b, base + 6) & 0x1;
  {
    int w = (bank_byte(b, base + 6) >> 1) & 0x7;
    p->lfo_waveform = w < 5 ? w : 5;
  }
  p->lfo_pitch_mod_sens = bank_byte(b, base + 6) >> 4;
#endif
}

/* ---- fm::Envelope<4, reshape> */

static float env_value_at(const SixOpEnv *e, int stage, float phase, float start_level, int reshape) {
  float from = start_level == DX_PREVIOUS_LEVEL ? e->level[(stage + 3) & 3] : start_level;
  float to = e->level[stage];
  if (reshape != 0 && from < to) {
    from = maxf(6.7f, from);
    to = maxf(6.7f, to);
    phase *= (2.5f - phase) * 0.666667f;
  }
  return phase * (to - from) + from;
}

static void env_init(SixOpEnv *e) {
  int i;
  e->stage = 3;
  e->phase = 1.0f;
  e->start = 0.0f;
  for (i = 0; i < 4; ++i) {
    e->increment[i] = 0.001f;
  }
  e->level[0] = 1.0f;
  e->level[1] = 0.5f;
  e->level[2] = 0.25f;
  e->level[3] = 0.0f;
}

static float env_render(SixOpEnv *e, int gate, float rate, float ad_scale, float release_scale, int reshape) {
  if (gate != 0) {
    if (e->stage == 3) {
      e->start = env_value_at(e, e->stage, e->phase, e->start, reshape);
      e->stage = 0;
      e->phase = 0.0f;
    }
  } else {
    if (e->stage != 3) {
      e->start = env_value_at(e, e->stage, e->phase, e->start, reshape);
      e->stage = 3;
      e->phase = 0.0f;
    }
  }
  e->phase += e->increment[e->stage] * rate * (e->stage == 3 ? release_scale : ad_scale);
  if (e->phase >= 1.0f) {
    if (e->stage >= 2) {
      e->phase = 1.0f;
    } else {
      e->phase = 0.0f;
      ++e->stage;
    }
    e->start = DX_PREVIOUS_LEVEL;
  }
  return env_value_at(e, e->stage, e->phase, e->start, reshape);
}

/* OperatorEnvelope::Set */
static void op_env_set(SixOpEnv *e, const SixOpPatchOp *op, int global_level) {
#ifdef PL_DX_KERNEL
  dx_env_set_kernel(e, op, global_level);
#else
  int i;
  for (i = 0; i < 4; ++i) {
    int level_scaled = (int)pl_dx_op_level[op->env_level[i]];
    level_scaled = (level_scaled & ~1) + global_level - 133;
    e->level[i] = 0.125f * (level_scaled < 1 ? 0.5f : itof_nonneg(level_scaled));
  }
  for (i = 0; i < 4; ++i) {
    float increment = PL_F(pl_dx_op_increment)[op->env_rate[i]];
    float from = e->level[(i + 3) & 3];
    float to = e->level[i];
    if (from == to) {
      increment *= 0.6f;
      if (i == 0 && op->env_level[i] == 0) {
        increment *= 20.0f;
      }
    } else if (from < to) {
      from = maxf(6.7f, from);
      to = maxf(6.7f, to);
      if (from == to) {
        increment = 1.0f;
      } else {
        increment *= 7.2f / (to - from);
      }
    } else {
      increment *= 1.0f / (from - to);
    }
    e->increment[i] = increment * DX_ENV_SCALE;
  }
#endif
}

/* PitchEnvelope::Set */
static void pitch_env_set(SixOpEnv *e, const SixOpPatch *p) {
  int i;
  for (i = 0; i < 4; ++i) {
    e->level[i] = PL_F(pl_dx_pitch_level)[p->pitch_level[i]];
  }
  for (i = 0; i < 4; ++i) {
    float from = e->level[(i + 3) & 3];
    float to = e->level[i];
    float increment = PL_F(pl_dx_pitch_increment)[p->pitch_rate[i]];
    if (from != to) {
      increment *= 1.0f / absf(from - to);
    } else if (i != 3) {
      increment = 0.2f;
    }
    e->increment[i] = increment * DX_ENV_SCALE;
  }
}

/* ---- fm::Lfo */

static void lfo_init(SixOpVoice *v) {
  v->lfo_phase = 0.0f;
  v->lfo_frequency = 0.1f;
  v->lfo_delay_phase = 0.0f;
  v->lfo_delay_increment[0] = 0.1f;
  v->lfo_delay_increment[1] = 0.1f;
  v->lfo_random_value = 0.0f;
  v->lfo_value = 0.0f;
  v->lfo_amp_mod_depth = 0.0f;
  v->lfo_pitch_mod_depth = 0.0f;
  v->lfo_waveform = 0;
  v->lfo_reset_phase = 0;
}

static void lfo_set(SixOpVoice *v) {
  const SixOpPatch *p = &v->patch;
  v->lfo_frequency = PL_F(pl_dx_lfo_frequency)[p->lfo_rate] * DX_ONE_HZ;
  v->lfo_delay_increment[0] = PL_F(pl_dx_lfo_delay0)[p->lfo_delay] * DX_ONE_HZ;
  v->lfo_delay_increment[1] = PL_F(pl_dx_lfo_delay1)[p->lfo_delay] * DX_ONE_HZ;
  v->lfo_waveform = p->lfo_waveform;
  v->lfo_reset_phase = p->lfo_reset_phase != 0;
  v->lfo_amp_mod_depth = itof_nonneg(p->lfo_amp_mod_depth) * 0.01f;
  v->lfo_pitch_mod_depth = itof_nonneg(p->lfo_pitch_mod_depth) * 0.01f * PL_F(pl_dx_lut_pitch_mod)[p->lfo_pitch_mod_sens];
}

static void lfo_reset(SixOpVoice *v) {
  if (v->lfo_reset_phase != 0) {
    v->lfo_phase = 0.0f;
  }
  v->lfo_delay_phase = 0.0f;
}

static float lfo_waveform_value(const SixOpVoice *v) {
  float phase = v->lfo_phase;
  int w = v->lfo_waveform;
  if (w == 0) {
    return 2.0f * (phase < 0.5f ? 0.5f - phase : phase - 0.5f);
  } else if (w == 1) {
    return 1.0f - phase;
  } else if (w == 2) {
    return phase;
  } else if (w == 3) {
    return phase < 0.5f ? 0.0f : 1.0f;
  } else if (w == 4) {
    return 0.5f + 0.5f * Sine(phase + 0.5f);
  }
  return v->lfo_random_value;
}

static void lfo_step(SixOpVoice *v, float scale, unsigned int *rng) {
  v->lfo_phase += scale * v->lfo_frequency;
  if (v->lfo_phase >= 1.0f) {
    v->lfo_phase -= 1.0f;
    v->lfo_random_value = rng_float(rng);
  }
  v->lfo_value = lfo_waveform_value(v);
  v->lfo_delay_phase += scale * v->lfo_delay_increment[(v->lfo_delay_phase < 0.5f) ? 0 : 1];
  if (v->lfo_delay_phase >= 1.0f) {
    v->lfo_delay_phase = 1.0f;
  }
}

static float lfo_delay_ramp(const SixOpVoice *v) {
  return v->lfo_delay_phase < 0.5f ? 0.0f : (v->lfo_delay_phase - 0.5f) * 2.0f;
}

static float lfo_pitch_mod(const SixOpVoice *v) {
  return (v->lfo_value - 0.5f) * lfo_delay_ramp(v) * v->lfo_pitch_mod_depth;
}

static float lfo_amp_mod(const SixOpVoice *v) {
  return (1.0f - v->lfo_value) * lfo_delay_ramp(v) * v->lfo_amp_mod_depth;
}

/* FMVoice::set_modulations: the voice takes the LFO of `lfo` */
static void set_modulations(SixOpVoice *v, const SixOpVoice *lfo) {
  v->p_pitch_mod = lfo_pitch_mod(lfo);
  v->p_amp_mod = lfo_amp_mod(lfo);
}

/* ---- fm::Voice<6> and FMVoice */

static void voice_init(SixOpVoice *v) {
  int i;
  for (i = 0; i < 6; ++i) {
    v->phase[i] = 0;
    v->amplitude[i] = 0.0f;
    env_init(&v->env[i]);
  }
  env_init(&v->pitch_env);
  v->feedback_state[0] = 0.0f;
  v->feedback_state[1] = 0.0f;
  v->gate_ = 0;
  v->note = 48.0f;
  v->normalized_velocity = 10.0f;
  v->dirty = 1;
  lfo_init(v);
  v->p_gate = 0;
  v->p_note = 48.0f;
  v->p_velocity = 0.5f;
  v->p_brightness = 0.5f;
  v->p_envelope_control = 0.5f;
  v->p_pitch_mod = 0.0f;
  v->p_amp_mod = 0.0f;
  v->patch_index = -1;
  for (i = 0; i < 6; ++i) {
    v->ratios[i] = 0.0f;
    v->level_headroom[i] = 0.0f;
    v->level[i] = 0.0f;
  }
}

/* FMVoice::LoadPatch: the voice plays patch `index` of `bank` (a patch that is already loaded changes nothing). */
static void voice_load_patch(SixOpVoice *v, int bank, int index) {
  if (index == v->patch_index) {
    return;
  }
  v->patch_index = index;
  patch_unpack(&v->patch, bank, index);
  v->dirty = 1;
  lfo_set(v);
}

/* fm::FrequencyRatio */
static float frequency_ratio(const SixOpPatchOp *op) {
  float detune = (op->mode == 0 && op->fine != 0) ? 1.0f + 0.01f * itof_nonneg(op->fine) : 1.0f;
  float base = op->mode == 0 ? PL_F(pl_dx_lut_coarse)[op->coarse]
                             : (itof_nonneg(op->coarse & 3) * 100.0f + itof_nonneg(op->fine)) * 0.39864f;
  base += (itof_nonneg(op->detune) - 7.0f) * 0.015f;
  return semitones_to_ratio_safe(base) * detune;
}

/* Voice<6>::Setup: true when it did the setup (the render then returns without a block). */
static int voice_setup(SixOpVoice *v) {
  int i;
  if (v->dirty == 0) {
    return 0;
  }
  pitch_env_set(&v->pitch_env, &v->patch);
  for (i = 0; i < 6; ++i) {
    const SixOpPatchOp *op = &v->patch.op[i];
    int level = (int)pl_dx_op_level[op->level];
    float sign;
    op_env_set(&v->env[i], op, level);
    v->level_headroom[i] = itof_nonneg(127 - level);
    sign = op->mode == 0 ? 1.0f : -1.0f;
    v->ratios[i] = sign * frequency_ratio(op);
  }
  v->dirty = 0;
  return 1;
}

#ifndef PL_DX_KERNEL
/* fm::KeyboardScaling */
static float keyboard_scaling(float note, const SixOpPatchOp *op) {
  float x = note - itof_nonneg(op->kb_break_point) - 15.0f;
  int curve = x > 0.0f ? op->kb_right_curve : op->kb_left_curve;
  float t = absf(x);
  float depth;
  if (curve == 1 || curve == 2) {
    t = minf(t * 0.010467f, 1.0f);
    t = t * t * t;
    t *= 96.0f;
  }
  if (curve < 2) {
    t = -t;
  }
  depth = itof_nonneg(x > 0.0f ? op->kb_right_depth : op->kb_left_depth);
  return t * depth * 0.02677f;
}
#endif

/* fm::NormalizeVelocity: Interpolate(lut_cube_root, velocity, 16) */
static float normalize_velocity(float velocity) {
  float cube_root = Interpolate(PL_F(pl_dx_lut_cube_root), velocity, 16.0f);
  PL_IDX(42, (int)(velocity * 16.0f))
  return 16.0f * (cube_root - 0.918f);
}

#ifndef PL_DX_KERNEL
/* fm::RenderOperators<n, modulation_source, additive> (n <= 3), one function for the eight variants. */
static void render_ops(int n, int ms, int additive, unsigned int *ops_phase, float *ops_amplitude, const float *f,
                       const float *a, float *fb_state, int fb_amount, const float *modulation, float *out,
                       int size) {
  float previous_0 = 0.0f, previous_1 = 0.0f;
  unsigned int frequency[3], phase[3];
  float amplitude[3], amplitude_increment[3];
  float scale = 1.0f / itof_nonneg(size);
  float fb_scale;
  int i, k;

  if (ms >= 0) {
    previous_0 = fb_state[0];
    previous_1 = fb_state[1];
  }
  for (i = 0; i < n; ++i) {
    frequency[i] = ftou(minf(f[i], 0.5f) * 4294967296.0f);
    phase[i] = ops_phase[i];
    amplitude[i] = ops_amplitude[i];
    amplitude_increment[i] = (minf(a[i], 4.0f) - amplitude[i]) * scale;
  }
  fb_scale = fb_amount != 0 ? itof_nonneg(1 << fb_amount) * 0.001953125f : 0.0f;

  for (k = 0; k < size; ++k) {
    float pm = 0.0f;
    if (ms >= 0) {
      pm = (previous_0 + previous_1) * fb_scale;
    } else if (ms == -2) {
      pm = *modulation++;
    }
    for (i = 0; i < n; ++i) {
      phase[i] += frequency[i];
      PL_IDX(43, (int)(pm * 1024.0f))
      pm = SinePM(phase[i], pm) * amplitude[i];
      amplitude[i] += amplitude_increment[i];
      if (i == ms) {
        previous_1 = previous_0;
        previous_0 = pm;
      }
    }
    if (additive != 0) {
      *out++ += pm;
    } else {
      *out++ = pm;
    }
  }

  for (i = 0; i < n; ++i) {
    ops_phase[i] = phase[i];
    ops_amplitude[i] = amplitude[i];
  }
  if (ms >= 0) {
    fb_state[0] = previous_0;
    fb_state[1] = previous_1;
  }
}

/* the renderers of Algorithms<6> in upstream's order: n, modulation source, additive */
static const int dx_render_n[8] = {1, 1, 1, 1, 1, 1, 3, 2};
static const int dx_render_ms[8] = {-2, -2, -1, -1, 0, 0, 2, 1};
static const int dx_render_add[8] = {0, 1, 0, 1, 0, 1, 1, 1};
#endif

/* Voice<6>::Render(parameters, temp, size): the three buffers are temp, temp + size, temp + 2 * size */
static void voice_render(SixOpVoice *v, float *temp, int size) {
  float *buffers[3];
  float envelope_rate, ad_scale, r_scale, pitch_envelope, pitch_mod, f0;
  float f[6], a[6];
  int note_on, i;
  const SixOpPatch *patch = &v->patch;
  int algorithm = patch->algorithm;
  unsigned int modmask = pl_dx_modmask[algorithm];

  if (v->patch_index < 0) {
    return; /* FMVoice::Render: no patch */
  }
  if (voice_setup(v) != 0) {
    /* a CPU-overrun guard upstream: a new patch's setup block is a clean blank */
    return;
  }
#ifdef PL_DX_KERNEL
  {
    DxVoiceK k;
    k.scale = 1.0f / itof_nonneg(size);
    k.fb_scale = patch->feedback != 0 ? itof_nonneg(1 << patch->feedback) * 0.001953125f : 0.0f;
    k.size = size;
    k.calls = pl_dx_calls;
    k.modmask = pl_dx_modmask;
    k.amp_mod_lut = pl_dx_lut_amp_mod;
    k.ratio_high = pl_lut_pitch_ratio_high;
    k.ratio_low = pl_lut_pitch_ratio_low;
    k.cube_root = pl_dx_lut_cube_root;
    dx_voice_kernel(v, temp, &k);
  }
#else
#ifndef PL_DX_KERNEL
  buffers[0] = temp;
  buffers[1] = temp + size;
  buffers[2] = temp + 2 * size;
#endif

  envelope_rate = itof_nonneg(size);
  ad_scale = pow2_fast_1((0.5f - v->p_envelope_control) * 8.0f);
  r_scale = pow2_fast_1(-absf(v->p_envelope_control - 0.3f) * 8.0f);

  pitch_envelope = env_render(&v->pitch_env, v->p_gate, envelope_rate, ad_scale, r_scale, 0);
  pitch_mod = pitch_envelope + v->p_pitch_mod;
  f0 = DX_A0 * 0.25f * semitones_to_ratio_safe(v->p_note - 9.0f + pitch_mod * 12.0f);

  note_on = v->p_gate != 0 && v->gate_ == 0;
  v->gate_ = v->p_gate;
  if (note_on != 0) {
    v->normalized_velocity = normalize_velocity(v->p_velocity);
    v->note = v->p_note;
  }

  if (note_on != 0 && patch->reset_phase != 0) {
    for (i = 0; i < 6; ++i) {
      v->phase[i] = 0;
    }
  }

  for (i = 0; i < 6; ++i) {
    const SixOpPatchOp *op = &patch->op[i];
    float rate_scaling, level, kb_scaling, velocity_scaling, brightness, sensitivity, log_level_mod, level_mod;
    float boost;

    f[i] = v->ratios[i] * (v->ratios[i] < 0.0f ? -DX_ONE_HZ : f0);

    rate_scaling = pow2_fast_1(itof_nonneg(op->rate_scaling) * (v->note * 0.33333f - 7.0f) * 0.03125f);
    level = env_render(&v->env[i], v->p_gate, envelope_rate * rate_scaling, ad_scale, r_scale, 1);
    kb_scaling = keyboard_scaling(v->note, op);
    velocity_scaling = v->normalized_velocity * itof_nonneg(op->velocity_sensitivity);
    brightness = ((modmask >> i) & 1u) != 0u ? (v->p_brightness - 0.5f) * 32.0f : 0.0f;

    boost = minf(kb_scaling + velocity_scaling + brightness, v->level_headroom[i]);
    level += 0.125f * boost;

    v->level[i] = level;

    sensitivity = PL_F(pl_dx_lut_amp_mod)[op->amp_mod_sensitivity];
    log_level_mod = sensitivity * v->p_amp_mod - 1.0f;
    level_mod = 1.0f - pow2_fast_2(6.4f * log_level_mod);
    a[i] = pow2_fast_2(-14.0f + level * level_mod);
  }

  for (i = 0; i < 6;) {
    unsigned int call = pl_dx_calls[algorithm * 6 + i];
    int renderer = (int)(call & 0xffu);
    int n = (int)((call >> 8) & 0xffu);
    int input_index = (int)((call >> 16) & 0xffu);
    int output_index = (int)((call >> 24) & 0xffu);
    render_ops(dx_render_n[renderer], dx_render_ms[renderer], dx_render_add[renderer], &v->phase[i], &v->amplitude[i],
               &f[i], &a[i], v->feedback_state, patch->feedback, buffers[input_index], buffers[output_index], size);
    i += n;
  }
#endif
}

/* ---- SixOpEngine */

void six_op_load_bank(SixOp *s, int bank) {
  s->bank = bank;
  s->voice[0].patch_index = -1; /* UnloadPatch */
  s->voice[1].patch_index = -1;
}

void six_op_init(SixOp *s) {
  int i;
  s->quantized_value = 0; /* unused: the patch quantiser has no state (six_op_patch_index) */
  for (i = 0; i < 2; ++i) {
    voice_init(&s->voice[i]);
  }
  for (i = 0; i < 96; ++i) {
    s->temp[i] = 0.0f;
  }
  for (i = 0; i < 32; ++i) {
    s->acc[i] = 0.0f;
  }
  s->active_voice = 1;
  s->rendered_voice = 0;
  s->rng_ = 0x21u;
  six_op_load_bank(s, 0);
}

void six_op_seed(SixOp *s, int track) {
  int t;
  s->rng_ = 0x21u;
  for (t = 0; t < track; ++t) {
    s->rng_ += 0x9e3779b1u;
  }
}

/* The patch of HARMONICS: upstream's HysteresisQuantizer2::Process(harmonics * 1.02f) (32 steps, not symmetric) with the
 * hysteresis 0, so the patch is a fixed function of the HARMONICS word (the owner's decision, 2026-10-23: the display shows the
 * patch name and must agree with the sound; docs/design/2026-10-22-plaits-dx7-design.md, PORTING drift 28). pl_dx7_patch_thr[k]
 * (generated by lib/gen_dx_tables.py, the same words as the ColdFire names) is unit(smallest word of patch k or more), so the
 * patch is the largest k whose threshold `harmonics` reaches: a binary search over 32 entries. */
int six_op_patch_index(float harmonics) {
  int lo = 0;
  int hi = 32;
  while (hi - lo > 1) {
    int mid = (lo + hi) >> 1;
    if (harmonics >= PL_F(pl_dx7_patch_thr)[mid]) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return lo;
}

#ifndef PL_DX_KERNEL
static float soft_clip(float x) { return SoftClip(x); }
#endif

void six_op_render(SixOp *s, const Params *p, float *out, float *aux, int size) {
  int patch_index = six_op_patch_index(p->harmonics);
  int i, k;
  float fsize = itof_nonneg(size);

  if ((p->trigger & TRIGGER_RISING_EDGE) != 0) {
    s->active_voice = (s->active_voice + 1) & 1;
    voice_load_patch(&s->voice[s->active_voice], s->bank, patch_index);
    lfo_reset(&s->voice[s->active_voice]);
  }
  {
    SixOpVoice *av = &s->voice[s->active_voice];
    av->p_note = p->note;
    av->p_velocity = p->accent;
    av->p_envelope_control = p->morph;
    lfo_step(av, fsize, &s->rng_);
  }
  for (i = 0; i < 2; ++i) {
    SixOpVoice *v = &s->voice[i];
    v->p_brightness = p->timbre;
    v->p_gate = ((p->trigger & TRIGGER_HIGH) != 0 && i == s->active_voice) ? 1 : 0;
    if (v->patch_index != s->voice[s->active_voice].patch_index) {
      lfo_step(v, fsize, &s->rng_);
      set_modulations(v, v);
    } else {
      set_modulations(v, &s->voice[s->active_voice]);
    }
  }

  /* Staggered rendering: the carry of the previous call, then one voice for 2 * size samples. */
#ifdef PL_DX_KERNEL
  dx_pre_kernel(s->temp, s->acc, size);
#else
  for (k = 0; k < size; ++k) {
    s->temp[k] = s->acc[k];
  }
  for (k = size; k < 2 * size; ++k) {
    s->temp[k] = 0.0f;
  }
#endif
  s->rendered_voice = (s->rendered_voice + 1) & 1;
  voice_render(&s->voice[s->rendered_voice], s->temp, size * 2);

#ifdef PL_DX_KERNEL
  dx_post_kernel(s->temp, out, aux, s->acc, size);
#else
  for (k = 0; k < size; ++k) {
    float y = soft_clip(s->temp[k] * 0.25f);
    out[k] = y;
    aux[k] = y;
  }
  for (k = 0; k < size; ++k) {
    s->acc[k] = s->temp[size + k];
  }
#endif
}
