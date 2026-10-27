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
// PLAITS for the Digitakt II: Plaits' ParticleEngine (model DUST: six particles, each a random impulse train through a
// band-pass filter at a random frequency, a low-pass filter, and the diffuser, a reverb-like chain of all-pass filters
// on Plaits' FxEngine; HARMONICS the frequency spread, TIMBRE the density, MORPH the diffusion below 0.5 and the
// resonance above) in C99, from plaits/dsp/engine/particle_engine.{h,cc}, plaits/dsp/noise/particle.h and
// plaits/dsp/fx/diffuser.h with plaits/dsp/fx/fx_engine.h. Not a strike engine: it runs on between notes. The trigger's
// RISING_EDGE (the wrapper's note-on) is upstream's `sync` (the first particle sample of every block of a note-on fires).
// Random: stmlib's global generator is one state per engine here (rng_, 0x21 at init as at Plaits' power-up, then
// 0x21 + track * 0x9e3779b1 by dust_seed so that tracks differ; track 0 is upstream's); the particles draw their whole
// block one after the other, as upstream.
// The diffuser's FxEngine<8192, FORMAT_12_BIT> keeps its delay memory as upstream: 8192 samples of 16 bits (the value
// times 4096, saturated to 16 bits, truncated), circular, every access masked. Here two samples are packed in one 32-bit
// word (selcc's 16-bit loads are not usable, the DSP words are 32 bits), so the buffer is 4096 words = 16,384 bytes per
// voice (upstream: the arena). The state is that buffer plus 200 bytes; dust_init clears it as Reset does.
// Deliberate differences: (1) `0.5f / Sqrt(q * f * Sqrt(density))` uses the division-free Newton reciprocal square root of
// NOISE (noise.c pl_rsqrt; the core has no square root; Sqrt(density) = density * rsqrt(density) once per block); the
// oracle's text is patched the same way (drift 18). (2) the FxEngine context is written out as plain sums in the same
// order; only LFO_1 (the one the diffuser uses) is kept. (3) the float to int16 compression saturates in the float domain
// (the oracle's NaN/overflow cast would be undefined) and truncates toward zero as the cast does.
#ifndef PLAITS_DUST_H
#define PLAITS_DUST_H
#include "engine.h"
#include "../lib/svf.h"

#define kDustParticles 6
#define kDustBufferWords 4096  /* 8192 16-bit samples */

typedef struct { float pre_gain_; Svf filter_; } DustParticle;

typedef struct {
  DustParticle particle_[kDustParticles];
  Svf post_filter_;
  float lp_decay_;
  float lfo_y1_, lfo_y0_;   /* the diffuser's LFO_1: stmlib's CosineOscillator (approximate), 0.3 Hz */
  int write_ptr_;
  unsigned int rng_;
  unsigned int buffer_[kDustBufferWords];
  int fresh_;  /* samples rendered since dust_init (lib/dust_sm.s: the DSP build does not clear buffer_; see there) */
} Dust;

#ifdef __cplusplus
extern "C" {
#endif
/* Init and Reset (the buffer cleared, the write pointer 0). */
void dust_init(Dust *s);
/* Reseed the engine's generator for a track: 0x21 + track * 0x9e3779b1 (track 0: 0x21, as the oracle). Called by the wrapper after dust_init. */
void dust_seed(Dust *s, int track);
void dust_render(Dust *s, const Params *p, float *out, float *aux, int size);
#ifdef __cplusplus
}
#endif
#endif  // PLAITS_DUST_H
