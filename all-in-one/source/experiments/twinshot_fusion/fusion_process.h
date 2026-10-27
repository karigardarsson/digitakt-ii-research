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
