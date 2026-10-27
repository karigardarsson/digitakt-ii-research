/* Shared native sampler core. Original integration code, no vendor payloads.
 * adapter.c and entry.s install it as a machine; compiled_dual_proof.py also
 * exercises it in isolation. Native bindings are stock-image hash gated.
 */
typedef struct { unsigned int w[118]; } SampleRecord;
typedef struct {
  SampleRecord voice[2];
  int ready;
  int slot[2];
  unsigned int generation[2];
  float envelope;
  float lowpass;
  float a[32];
  float b[32];
} TwinChannel;
typedef struct {
  int slot[2];
  float tune[2];
  float mix;
  int reverse[2];
  int loop[2];
  int trig;
  int released;
  int channel;
  float note;
  float decay;
  float filter;
} TwinControl;

extern void tw_slot(int slot, unsigned int *result);
extern volatile unsigned int tw_generations[1025];
unsigned int tw_generation(int slot) {
  if ((unsigned int)slot >= 1025u) return 0;
  return tw_generations[slot];
}
extern void tw_load(SampleRecord *r, unsigned int *pcm, int unused,
                    unsigned int length, unsigned int high, unsigned int rate);
extern void tw_arm(SampleRecord *r);
extern void tw_pitch(SampleRecord *r, float note, float tune,
                     int reverse, int loop, float start, float loop_start, float length);
extern void tw_sample(SampleRecord *r, float *out, int count);

/* One stereo side; four native records total per track, not stolen voices.
 * Caller must supply a zeroed state and a correctly preserved native ABI.
 * Keep existing streams through parameter changes, reload on the next trig.
 */
void twinshot_channel(TwinChannel *s, TwinControl *c, float *out) {
  unsigned int slot[5];
  int layer, k;
  float mix = c->mix;
  float decay = c->decay * (1.0f / 127.0f);
  float filter = (c->filter - 64.0f) * (1.0f / 64.0f);
  float cutoff, coefficient, time;
  if (decay < 0.0f) decay = 0.0f;
  if (decay > 1.0f) decay = 1.0f;
  time = 0.003f + 8.0f * decay * decay * decay * decay;
  coefficient = c->decay >= 127.0f ? 1.0f : 1.0f / (1.0f + 1.0f / (48000.0f * time));
  if (filter < -1.0f) filter = -1.0f;
  if (filter > 1.0f) filter = 1.0f;
  cutoff = filter < 0.0f ? 1.0f + filter : filter;
  cutoff = 0.0005f + 0.8f * cutoff * cutoff;
  if (mix < 0.0f) mix = 0.0f;
  if (mix > 1.0f) mix = 1.0f;
  for (layer = 0; layer != 2; ++layer) {
    SampleRecord *r = &s->voice[layer];
    if (c->trig) {
      s->generation[layer] = tw_generation(c->slot[layer]);
      tw_slot(c->slot[layer], slot);
      tw_load(r, (unsigned int *)slot[c->channel], 0, slot[2], 0, slot[3]);
      if (slot[2] >= 141) tw_arm(r);
      s->slot[layer] = c->slot[layer];
      s->ready = 1;
      if (!layer) {
        s->envelope = 1.0f;
        s->lowpass = 0.0f;
      }
    }
    if (s->ready && !c->trig) {
      /* Native header writes bracket metadata replacement with an odd/even
       * generation. Identical reset/restore (ABA) must retire the old cursor.
       */
      tw_slot(s->slot[layer], slot);
      if (s->generation[layer] != tw_generation(s->slot[layer]) ||
          r->w[0] != slot[c->channel] || r->w[0x188/4] != slot[2] ||
          r->w[0x184/4] != slot[3]) {
        r->w[0] = 0;
        r->w[0x1b8/4] = 0;
      }
    }
    if (s->generation[layer] & 1) {
      r->w[0] = 0;
      r->w[0x1b8/4] = 0;
    }
    if (s->ready) {
      tw_pitch(r, c->note, c->tune[layer], c->reverse[layer],
               c->loop[layer] && (!c->released || c->trig), 0.0f, 0.0f, 1.0f);
    }
    tw_sample(r, layer ? s->b : s->a, 32);
    /* A loader interrupt during setup/render also invalidates this block.
     * Native PCM lives in the fixed resident SDRAM window, not freed heap.
     * Discard the potentially mixed-generation output and never resume it.
     */
    if (s->generation[layer] != tw_generation(s->slot[layer])) {
      r->w[0] = 0;
      r->w[0x1b8/4] = 0;
      for (k = 0; k != 32; ++k) {
        if (layer) s->b[k] = 0.0f;
        else s->a[k] = 0.0f;
      }
    }
  }
  for (k = 0; k != 32; ++k) {
    float a = s->a[k];
    s->lowpass += cutoff * (a - s->lowpass);
    if (filter < 0.0f) a = s->lowpass;
    if (filter > 0.0f) a -= s->lowpass;
    a *= s->envelope;
    s->envelope *= coefficient;
    out[k] = (1.0f - mix) * a + mix * s->b[k];
  }
}
