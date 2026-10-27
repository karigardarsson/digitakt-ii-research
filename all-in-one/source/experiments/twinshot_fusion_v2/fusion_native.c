/* Keep V1's native setup, metadata/epoch checks and sampler code as the source
 * of truth. Intercept its pitch setter for native start windows and optional
 * B rate sync; restore the original sample metadata immediately.
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
  int released;
  float sweep;
  FusionKey key;
  int a_looping;
} FusionChannel;

extern void tw_pitch(SampleRecord *r, float note, float tune,
                     int reverse, int loop, float start, float loop_start, float length);
static SampleRecord *fusion_sync_record;
static unsigned int fusion_sync_rate;
static float fusion_sync_tune;
static float fusion_span_a, fusion_span_b;
extern float fusion_float(int value);
extern unsigned int fusion_bits(float value);
extern float fusion_phase(SampleRecord *r);
static int fusion_v2;
static float fusion_start_a, fusion_start_b, fusion_shape;
static SampleRecord *fusion_record_a, *fusion_record_b;

void fusion_pitch(SampleRecord *r, float note, float tune,
                   int reverse, int loop, float start, float loop_start, float length) {
  unsigned int rate = r->w[0x184/4];
  if (r == fusion_sync_record && fusion_sync_rate) {
    r->w[0x184/4] = fusion_sync_rate;
    tune = fusion_sync_tune;
  }
  if (fusion_v2) {
    float offset = r == fusion_record_a ? fusion_start_a : fusion_start_b;
    float maximum = r->w[0x188/4] >= 141u ?
      1.0f - 141.0f / fusion_float((int)r->w[0x188/4]) : 0.0f;
    offset = fusion_unit(offset) * maximum;
    start = reverse ? 0.0f : offset;
    loop_start = start;
    length = 1.0f - offset;
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
      if (fusion_v2) {
        /* START shortens native playback windows. Sync those durations, not
         * the original whole-file lengths. Globals avoid the compiler's
         * previously isolated live-local lifetime hazard across helper calls.
         */
        fusion_span_a = fusion_float((int)a[2]);
        fusion_span_a -= fusion_unit(fusion_start_a) * (fusion_span_a - 141.0f);
        fusion_span_b = fusion_float((int)b[2]);
        fusion_span_b -= fusion_unit(fusion_start_b) * (fusion_span_b - 141.0f);
        rate = fusion_float((int)a[3]) * (fusion_span_b / fusion_span_a);
      } else {
        rate = fusion_float((int)a[3]) * (fusion_float((int)b[2]) / fusion_float((int)a[2]));
      }
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
      fusion_sync_tune = sync == 2 ? c->tune[0] + c->tune[1] - 64.0f : c->tune[0];
      fusion_sync_record = &s->base.voice[1];
    }
  }
  /* 1c6056 raises the SDK byte, but 1c7194 clears it at block end.
   * Keep note-off until a new note-on, not just for its first audio block.
   * Do not truncate the native one-shot/release tail or clear other voices.
   */
  if (c->trig) s->released = 0;
  else if (c->released) s->released = 1;
  c->released = s->released;
  if (c->trig) { s->progress = 0.0f; s->duck = 0.0f; s->ring_dc = 0.0f; s->sweep = 0.0f;
    s->key.peak = 0.0f; s->key.hold = 0; }
  fusion_inner(&s->base, c, out);
  fusion_sync_record = 0;
  /* A one-shot used to disappear while B looped forever: DEP then had no
   * key, and AUTO faded silence into B. Keep the participating A stream alive
   * through native looping. A live switch into these modes may find A ended;
   * re-arm only on that transition, after the ordinary ownership checks, never
   * after note-off/reset or a loader invalidation. Next block sets its pitch
   * and start window normally. This does not resurrect stale sample cursors.
   */
  if (fusion_v2 && c->loop[0] && !s->a_looping && !c->trig &&
      !s->released && s->base.ready && s->base.voice[1].w[0x1b8/4] &&
      !s->base.voice[0].w[0x1b8/4]) {
    tw_slot(s->base.slot[0], a);
    if (a[c->channel] && a[2] >= 141u &&
        !(s->base.generation[0] & 1u) &&
        s->base.generation[0] == fusion_generation(s->base.slot[0]) &&
        s->base.voice[0].w[0] == a[c->channel] &&
        s->base.voice[0].w[0x188/4] == a[2] &&
        s->base.voice[0].w[0x184/4] == a[3]) {
      tw_arm(&s->base.voice[0]);
      s->duck = 0.0f; s->key.peak = 0.0f; s->key.hold = 0;
    }
  }
  s->a_looping = c->loop[0];
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
  if (fusion_v2) {
    /* AUTO belongs to MORPH, not the shared response knob in other modes.
     * Previously RING/DUCK/GATE silently ran this clock to1 before a live
     * switch to MORPH, leaving B-only output and no remaining transition.
     */
    if (mode != 3 || fusion_shape <= 0.0f) s->sweep = 0.0f;
    else if (s->base.ready) {
      s->sweep = fusion_unit(s->sweep + (32.0f / 48000.0f) /
                   (0.01f + 8.0f * fusion_shape * fusion_shape));
    }
    fusion_process_v3(s->base.a, s->base.b, out, mode, c->mix,
                      fusion_shape, s->sweep, &s->duck, &s->ring_dc, &s->key);
  } else if (mode != 0) fusion_process(s->base.a, s->base.b, out, mode, c->mix,
                                     s->progress, &s->duck, &s->ring_dc);
}
