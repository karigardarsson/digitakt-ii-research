/* Machine control adapter. Shared tested sampler implementation, not a second
 * renderer. Frame words come from the existing inline-voice SDK helpers. */
#include "dual_native.c"

extern TwinChannel tw_states[32];
typedef char check_state_size[(sizeof(TwinChannel) == 1228) ? 1 : -1];
typedef struct {
  float note, tune1, filter, decay;
  unsigned int sample1;
  float tune2, mix;
  unsigned int sample2;
  int trig, released, reset;
} TwinFrame;

void twinshot_render(int voice, TwinFrame *f, float *out) {
  TwinControl c;
  TwinChannel *s;
  int i;
  unsigned int a = f->sample1, b = f->sample2;
  if ((unsigned int)voice >= 32u) return;
  s = &tw_states[voice];
  if (f->reset) {
    /* Native frame +0x2a selects tracks, both stereo voices. Retire every
     * private cursor/filter/decimator word, not just its visible ACTIVE bit.
     * A simultaneous new trigger below may arm fresh state, never old state.
     */
    for (i = 0; i != 118; ++i) {
      s->voice[0].w[i] = 0;
      s->voice[1].w[i] = 0;
    }
    s->ready = 0;
    s->slot[0] = s->slot[1] = 0;
    s->generation[0] = s->generation[1] = 0;
    s->envelope = 0.0f;
    s->lowpass = 0.0f;
  }
  c.slot[0] = a & 1023;
  c.slot[1] = b & 1023;
  c.reverse[0] = (a & 1024) != 0;
  c.reverse[1] = (b & 1024) != 0;
  c.tune[0] = f->tune1 * (1.0f / 256.0f);
  c.tune[1] = f->tune2 * (1.0f / 256.0f);
  c.note = f->note * (1.0f / 256.0f);
  c.filter = f->filter * (1.0f / 256.0f);
  c.decay = f->decay * (1.0f / 256.0f);
  c.mix = f->mix * (1.0f / 32512.0f);
  c.trig = f->trig;
  c.released = f->released;
  c.channel = voice & 1;
  /* Layer one is a decaying one-shot; layer two sustains until note-off,
   * then finishes its current pass. Stock AMP/HOLD/release still follow us. */
  c.loop[0] = 0;
  c.loop[1] = 1;
  twinshot_channel(s, &c, out);
}
