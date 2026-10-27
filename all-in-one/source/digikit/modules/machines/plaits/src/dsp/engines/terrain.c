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
// PLAITS for the Digitakt II: Plaits' WaveTerrainEngine (model TERRAIN) in C99, from plaits/dsp/engine2/
// wave_terrain_engine.cc. Sine is lib/sine.h's (Plaits' sine_oscillator.h), the wavetable terrains read lib/waves.h's
// float copy of wav_integrated_waves (the table of the WAVETABLE model).
// Differences from upstream, none of which changes a result on the host:
//   - the pitch reference is 48,000 Hz: a0 = (440 / 8) / 48000 (upstream: kCorrectedSampleRate = 47872.34 Hz);
//   - no user terrain: Plaits' Voice passes UserData::ptr(), NULL without user data in its flash, so num_terrains is
//     8 and terrain 8 (TerrainLookup of the user data) is never selected: it is not ported (Terrain returns 0 there,
//     as upstream does for an index past its cases); float(num_terrains - 1.0001f) is written as the float it
//     evaluates to, 6.99989986f (0x40dfff2e);
//   - the temporary buffer (kMaxBlockSize * 4 floats of the arena upstream) is on the stack.
// Undefined behaviour in upstream, guarded here (the only deviations from upstream's output). The path's
// FastSineOscillator is renormalised only when x^2 + y^2 leaves (0.5, 2), so after parameter jumps the path runs
// outside the unit square (|x|, |y| up to about 1.5, measured) and upstream's unguarded indices point outside its
// tables: TerrainLookupWT before and past wav_integrated_waves (about 1,300 entries), and Sine of a negative phase
// before lut_sine. What upstream returns there depends on its build's memory. The port wraps a negative Sine phase into
// [0, 1) and guards the wave coordinates the way the wave table is laid out (terrain_sine, terrain_lookup_wt); inputs
// inside the tables take upstream's arithmetic unchanged (bit exact against upstream, tests/plaits/test_terrain_host.py,
// whose oracle applies the same guard only where upstream would read outside its tables).
// Index ranges (PL_INDEX_CHECK): the terrain index z_integral is 0..6 (z < 6.9999), so Terrain reads cases 0..7; every
// wave read is inside the 25,344 entries.
#include "terrain.h"
#include "../lib/units.h"
#include "../lib/sine.h"
#include "../lib/waves.h"

#ifdef PL_INDEX_CHECK
void pl_index_check(int table, int index);
#define PL_IDX(t, i) pl_index_check(t, i);
#else
#define PL_IDX(t, i)
#endif

#if defined(__ADSP21569__) && !defined(PL_TERRAIN_C) && !defined(PL_INDEX_CHECK)
/* The DSP build: terrain_render's sample loop runs in lib/terrain_sm.s, bit-identical to the C below, which stays for the
   host and as the -DPL_TERRAIN_C reference (tests/plaits/test_terrain_kernel.py). */
#define PL_TERRAIN_KERNEL 1
void terrain_kernel(Terrain *s, const float *k, float *out, float *aux, const float *path_x, const float *path_y,
                    int size);
#endif

#define kTerrainOversampling 2
#define kTerrainMaxSize 24

void terrain_init(Terrain *s) {
  fast_sine_oscillator_init(&s->path_);
  s->offset_ = 0.0f;
  s->terrain_ = 0.0f;
}

/* static_cast<float>(i) for |i| < 2^23 (itof_nonneg for both signs). */
static float itof(int i) {
  return i < 0 ? -itof_nonneg(-i) : itof_nonneg(i);
}

/* Sine at any phase. A non-negative phase is upstream's Sine (InterpolateWrap) unchanged. A negative phase, where
 * upstream's InterpolateWrap indexes below lut_sine (undefined behaviour), is first wrapped into [0, 1) (sine is
 * periodic: the value an unbounded table would give). Table 19 records whether the phase was negative. */
static float terrain_sine(float phase) {
  PL_IDX(19, phase < 0.0f ? -1 : (int)(phase * 65536.0f))
  if (phase < 0.0f) {
    phase = phase - itof((int)phase) + 1.0f;
    if (phase >= 1.0f) phase -= 1.0f;
  }
  return Sine(phase);
}

/* InterpolateIntegratedWave at the index i of wav_integrated_waves (reads i, i + 1, i + 2). Table 26 records i. */
static float interpolate_integrated_wave(int i, float t) {
  const float *table = PL_F(pl_wav_integrated_waves);
  float a, b, c;
  PL_IDX(26, i)
  a = table[i];
  b = table[i + 1];
  c = table[i + 2];
  return (b - a) + (c - b - b + a) * t;
}

/* TerrainLookupWT: the wavetable data re-interpreted as a terrain (DIFFERENTIATE_WAVE_DATA). Upstream reads outside
 * wav_integrated_waves (undefined behaviour) when the path leaves the unit square; only there the coordinates are
 * guarded the way the table is laid out: a wave is periodic (128 samples plus 4 wrapped), so a sample coordinate below
 * 0 or at 130 and above moves by 128; the wave coordinate is held in [0, 63], and at 63 only wave 63 is read (upstream
 * multiplies the next wave by a zero fraction there). Inside the table this is upstream's arithmetic unchanged. */
static float terrain_lookup_wt(float x, float y, int bank) {
  const int table_size_full = 132;
  const float value_scale = 0.0009765625f;  /* 1 / 1024 */
  float sample = (y + 1.0f) * 0.5f * 128.0f;
  float wt = (x + 1.0f) * 0.5f * 63.0f;
  int sample_integral, wt_integral, base;
  float sample_fractional, wt_fractional, xy0, xy1;
  PL_IDX(20, wt < 0.0f ? -1 : (int)wt)
  PL_IDX(21, sample < 0.0f ? -1 : (int)sample)
  while (sample < 0.0f) sample += 128.0f;
  while (sample >= 130.0f) sample -= 128.0f;
  if (wt < 0.0f) wt = 0.0f;
  if (wt > 63.0f) wt = 63.0f;
  sample_integral = (int)sample;
  sample_fractional = sample - itof_nonneg(sample_integral);
  wt_integral = (int)wt;
  wt_fractional = wt - itof_nonneg(wt_integral);
  base = bank * (64 * 132) + wt_integral * table_size_full + sample_integral;
  PL_IDX(22, base + (wt_integral >= 63 ? 0 : table_size_full) + 2)
  xy0 = interpolate_integrated_wave(base, sample_fractional);
  xy1 = wt_integral >= 63 ? xy0 : interpolate_integrated_wave(base + table_size_full, sample_fractional);
  return (xy0 + (xy1 - xy0) * wt_fractional) * value_scale;
}

static float squash(float x, float a) {
  x *= a;
  return x / (1.0f + absf(x));
}

static float terrain(float x, float y, int terrain_index) {
  const float k = 4.0f;
  PL_IDX(23, terrain_index)
  if (terrain_index == 0) {
    return (squash(terrain_sine(k + x * 1.273f), 2.0f) - terrain_sine(k + y * (x + 1.571f) * 0.637f)) * 0.57f;
  } else if (terrain_index == 1) {
    const float xy = x * y;
    return terrain_sine(k + terrain_sine(k + (x + y) * 0.637f) / (0.2f + xy * xy) * 0.159f);
  } else if (terrain_index == 2) {
    const float xy = x * y;
    return terrain_sine(k + terrain_sine(k + 2.387f * xy) / (0.350f + xy * xy) * 0.159f);
  } else if (terrain_index == 3) {
    const float xy = x * y;
    const float xys = (x - 0.25f) * (y + 0.25f);
    return terrain_sine(k + xy / (2.0f + absf(5.0f * xys)) * 6.366f);
  } else if (terrain_index == 4) {
    return terrain_sine(0.159f / (0.170f + absf(y - 0.25f)) + 0.477f / (0.350f + absf((x + 0.5f) * (y + 1.5f))) + k);
  } else if (terrain_index >= 5 && terrain_index <= 7) {
    return terrain_lookup_wt(x, y, 2 - (terrain_index - 5));
  }
  return 0.0f;
}

void terrain_render(Terrain *s, const Params *p, float *out, float *aux, int size) {
  const float kScale = 0.5f;
  float path_x[kTerrainOversampling * kTerrainMaxSize];
  float path_y[kTerrainOversampling * kTerrainMaxSize];
  const float f0 = NoteToFrequency(p->note);
  const float attenuation = maxf(1.0f - 8.0f * f0, 0.0f);
  const float radius = 0.1f + 0.9f * p->timbre * attenuation * (2.0f - attenuation);
  Interp offset, terrain_z;
  int i, j, ij;

  fast_sine_oscillator_render_quadrature(&s->path_, f0 * kScale, radius, path_x, path_y, size * kTerrainOversampling);

  offset = interp(&s->offset_, 1.9f * p->morph - 1.0f, size);
  terrain_z = interp(&s->terrain_, minf(p->harmonics * 1.05f, 1.0f) * 6.99989986f, size);

#ifdef PL_TERRAIN_KERNEL
  {
    float k[2];
    k[0] = offset.increment;
    k[1] = terrain_z.increment;
    (void)i;
    (void)j;
    (void)ij;
    terrain_kernel(s, k, out, aux, path_x, path_y, size);
    return;
  }
#endif
  ij = 0;
  for (i = 0; i < size; ++i) {
    const float x_offset = next(&offset);
    const float zz = next(&terrain_z);
    float out_s = 0.0f;
    float aux_s = 0.0f;
    MAKE_INTEGRAL_FRACTIONAL(zz)

    for (j = 0; j < kTerrainOversampling; ++j) {
      const float x = path_x[ij] * (1.0f - absf(x_offset)) + x_offset;
      const float y = path_y[ij];
      float z0, z1, z;
      ++ij;
      PL_IDX(24, (int)(x * 10000.0f))
      PL_IDX(25, (int)(y * 10000.0f))

      z0 = terrain(x, y, zz_integral);
      z1 = terrain(x, y, zz_integral + 1);
      z = (z0 + (z1 - z0) * zz_fractional);
      out_s += z;
      aux_s += y + z;
    }
    out[i] = kScale * out_s;
    aux[i] = terrain_sine(1.0f + 0.5f * kScale * aux_s);
  }
  commit(&offset);
  commit(&terrain_z);
}
