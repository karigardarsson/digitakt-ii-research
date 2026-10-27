/* Original, bounded cross-processing kernel shared by host audition and DSP.
 * Inputs are independent normalized stereo sides; never collapse to mono.
 * BLEND uses MIX as balance. Other modes use MIX as transformation depth.
 */
static float fusion_abs(float x) { return x < 0.0f ? -x : x; }
static float fusion_unit(float x) {
  if (x < 0.0f) return 0.0f;
  if (x > 1.0f) return 1.0f;
  return x;
}

/* V2 is a separate machine: no reinterpretation of type-13 saved controls.
 * SHAPE = clean balance (RING), recovery (DUCK/GATE), auto sweep time (MORPH).
 * MIX = blend, wet depth, sidechain depth, or explicit morph position.
 */
typedef struct {
  float peak;
  int hold;
} FusionKey;

void fusion_process_v3(float *a, float *b, float *out, int mode, float mix,
                       float shape, float sweep, float *duck, float *dc,
                       FusionKey *key) {
  int k;
  float recovery, reference, inverse;
  mix = fusion_unit(mix);
  shape = fusion_unit(shape);
  sweep = fusion_unit(sweep);
  /* A's native playback level is not a normalized key. Track its peak since
   * trigger, without AGC that would promote a decaying tail back to full gain.
   * One reciprocal per block, and a -66 dB floor for silent/near-silent keys.
   * The 5 ms hold bridges carrier zero crossings. RCVR now denotes practical
   * decay to 1%, rather than a time constant with a 4.6x longer audible tail.
   * Saved values/ranges retain their roles; this intentionally repairs their
   * audible response, not their representation or the other modes' curves.
   */
  reference = key->peak;
  if (reference < (1.0f / 2048.0f)) reference = 1.0f / 2048.0f;
  for (k = 0; k != 32; ++k) {
    float level = fusion_abs(a[k]);
    if (level > reference) reference = level;
  }
  key->peak = reference;
  inverse = 1.0f / reference;
  recovery = 4.6051702f / (4.6051702f + 48000.0f *
                                  (0.01f + 1.99f * shape * shape));
  for (k = 0; k != 32; ++k) {
    float x = a[k], y = b[k], dry, wet, product, position;
    float target = fusion_unit(fusion_abs(x) * inverse);
    float gain;
    if (target > *duck) {
      *duck += 0.05f * (target - *duck);
      key->hold = 240;
    } else if (key->hold > 0) --key->hold;
    else *duck += recovery * (target - *duck);
    gain = fusion_unit(1.25f * *duck);
    if (mode == 1) {
      dry = (1.0f - shape) * x + shape * y;
      product = x * y;
      *dc += 0.002f * (product - *dc);
      product -= *dc;
      wet = 2.0f * product / (1.0f + 2.0f * fusion_abs(product));
      /* More clean range, unchanged fully-wet ring character. */
      position = mix * mix;
      out[k] = (1.0f - position) * dry + position * wet;
    } else if (mode == 2) {
      out[k] = y * (1.0f - mix * gain);
    } else if (mode == 4) {
      out[k] = y * (1.0f - mix + mix * gain);
    } else if (mode == 3) {
      position = fusion_unit(mix + (1.0f - mix) * sweep);
      out[k] = (1.0f - position) * x + position * y;
    } else {
      out[k] = (1.0f - mix) * x + mix * y;
    }
  }
}
void fusion_process(float *a, float *b, float *out, int mode, float mix,
                    float phase, float *duck, float *dc) {
  int k;
  mix = fusion_unit(mix);
  phase = fusion_unit(phase);
  for (k = 0; k != 32; ++k) {
    float x = a[k], y = b[k];
    float target = fusion_unit(4.0f * fusion_abs(x));
    float coefficient = target > *duck ? 0.01f : 0.001f;
    float dry, wet, product;
    *duck += coefficient * (target - *duck);
    if (mode == 0) {
      out[k] = (1.0f - mix) * x + mix * y;
    } else {
      dry = 0.5f * (x + y);
      wet = dry;
      if (mode == 1) {
        product = x * y;
        /* Correlated sources otherwise create sustained DC. Remove it before
         * bounded gain compensation; this cannot amplify a DC step above 1. */
        *dc += 0.002f * (product - *dc);
        product -= *dc;
        wet = 2.0f * product / (1.0f + 2.0f * fusion_abs(product));
      }
      if (mode == 2) wet = 0.5f * (x + y * (1.0f - *duck));
      if (mode == 3) wet = (1.0f - phase) * x + phase * y;
      out[k] = (1.0f - mix) * dry + mix * wet;
    }
  }
}
