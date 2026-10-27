/* Keep V1's native setup, metadata/epoch checks and sampler code as the source
 * of truth. Only intercept its pitch setter to optionally scale layer B's
 * private sample-rate field; restore the original metadata immediately.
 */
#define tw_pitch fusion_pitch
#define twinshot_channel fusion_inner
#define tw_generation fusion_generation
#include "../twinshot/dual_native.c"
#undef tw_pitch
#undef twinshot_channel
#undef tw_generation
#include "fusion_process.h"

typedef struct {
  TwinChannel base;
  float duck;
  float progress;
  float ring_dc;
} FusionChannel;

extern void tw_pitch(SampleRecord *r, float note, float tune,
                     int reverse, int loop, float start, float loop_start, float length);
static SampleRecord *fusion_sync_record;
static unsigned int fusion_sync_rate;
static float fusion_sync_tune;
extern float fusion_float(int value);
extern unsigned int fusion_bits(float value);
extern float fusion_phase(SampleRecord *r);

void fusion_pitch(SampleRecord *r, float note, float tune,
                   int reverse, int loop, float start, float loop_start, float length) {
  unsigned int rate = r->w[0x184/4];
  if (r == fusion_sync_record && fusion_sync_rate) {
    r->w[0x184/4] = fusion_sync_rate;
    tune = fusion_sync_tune;
  }
  tw_pitch(r, note, tune, reverse, loop, start, loop_start, length);
  r->w[0x184/4] = rate;
}

void fusion_channel(FusionChannel *s, TwinControl *c, float *out, int sync, int mode) {
  unsigned int a[5], b[5];
  unsigned int word, exponent;
  float rate, ratio, position;
  fusion_sync_record = 0;
  fusion_sync_rate = 0;
  if (sync) {
    tw_slot(c->trig ? c->slot[0] : s->base.slot[0], a);
    tw_slot(c->trig ? c->slot[1] : s->base.slot[1], b);
    if (a[2] >= 141 && b[2] >= 141 && a[3] && b[3] &&
        a[2] <= 0x7fffffffu && b[2] <= 0x7fffffffu &&
        a[3] <= 768000u && b[3] <= 768000u) {
      rate = fusion_float((int)a[3]) * (fusion_float((int)b[2]) / fusion_float((int)a[2]));
      ratio = rate / fusion_float((int)b[3]);
      /* Bound resampling work and prohibit near-zero playback increments. */
      if (ratio < 0.25f) rate = fusion_float((int)b[3]) * 0.25f;
      if (ratio > 4.0f) rate = fusion_float((int)b[3]) * 4.0f;
      /* Positive bounded rate: decode its IEEE single bits without an
       * unproven FIX encoding, aliasing cast, union or runtime library. */
      word = fusion_bits(rate + 0.5f);
      exponent = word >> 23;
      if (exponent >= 127u && exponent <= 149u)
        fusion_sync_rate = ((word & 0x7fffffu) | 0x800000u) >> (150u - exponent);
      fusion_sync_tune = c->tune[0];
      fusion_sync_record = &s->base.voice[1];
    }
  }
  if (c->trig) { s->progress = 0.0f; s->duck = 0.0f; s->ring_dc = 0.0f; }
  fusion_inner(&s->base, c, out);
  fusion_sync_record = 0;
  /* The native 64-bit Q31 cursor's high word counts pairs of sample frames.
   * Monotonic first-pass phase, including reverse, drives MORPH. No timer,
   * host tempo, natural-clock assumption or independent duration accumulator.
   */
  if (s->base.voice[0].w[0x188/4] >= 141u) {
    position = fusion_phase(&s->base.voice[0]);
    if (c->reverse[0]) position = 1.0f - position;
    position = fusion_unit(position);
    if (position > s->progress) s->progress = position;
  }
  /* The Fusion machine uses the V1 core's bypass/no-added-decay defaults.
   * Its raw independent streams therefore match those used by BLEND exactly.
   */
  if (mode != 0) fusion_process(s->base.a, s->base.b, out, mode, c->mix,
                              s->progress, &s->duck, &s->ring_dc);
}
