#include "fusion_native.c"
extern FusionChannel tf_states[32];
typedef char check_fusion_size[(sizeof(FusionChannel) == 1240) ? 1 : -1];
typedef struct {
  float note, tune1, sync, mode;
  unsigned int sample1;
  float tune2, mix;
  unsigned int sample2;
  int trig, released, reset;
} FusionFrame;

void fusion_render(int voice, FusionFrame *f, float *out) {
  TwinControl c;
  FusionChannel *s;
  int i;
  unsigned int a = f->sample1, b = f->sample2;
  if ((unsigned int)voice >= 32u) return;
  s = &tf_states[voice];
  if (f->reset) {
    for (i = 0; i != 118; ++i) {
      s->base.voice[0].w[i] = 0;
      s->base.voice[1].w[i] = 0;
    }
    s->base.ready = 0;
    s->base.slot[0] = s->base.slot[1] = 0;
    s->base.generation[0] = s->base.generation[1] = 0;
    s->base.envelope = s->base.lowpass = 0.0f;
    s->duck = s->progress = s->ring_dc = 0.0f;
  }
  c.slot[0] = a & 1023; c.slot[1] = b & 1023;
  c.reverse[0] = (a & 1024) != 0; c.reverse[1] = (b & 1024) != 0;
  c.tune[0] = f->tune1 * (1.0f / 256.0f);
  c.tune[1] = f->tune2 * (1.0f / 256.0f);
  c.note = f->note * (1.0f / 256.0f);
  c.filter = 64.0f; c.decay = 127.0f;
  c.mix = f->mix * (1.0f / 32512.0f);
  c.trig = f->trig; c.released = f->released; c.channel = voice & 1;
  c.loop[0] = 0; c.loop[1] = 1;
  fusion_channel(s, &c, out, f->sync >= 128.0f,
                  f->mode < 256.0f ? 0 : f->mode < 512.0f ? 1 : f->mode < 768.0f ? 2 : 3);
}
