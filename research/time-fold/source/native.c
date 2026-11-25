/* SPDX-License-Identifier: AGPL-3.0-or-later */
#define FOLD_NATIVE
#include "fold.c"

typedef struct {
    unsigned pitch, tune, fold, nest, memory, clock, drift;
    unsigned active, trigger, reset;
} NativeFrame;

extern FoldVoice fold_voices[16];
typedef char verify_state_size[(sizeof(FoldVoice) == 592) ? 1 : -1];
typedef char verify_frame_size[(sizeof(NativeFrame) == 40) ? 1 : -1];

static float pitch_increment(float note)
{
    float octaves = (note - 69.0f) * .083333333333333333f;
    int oct = (int)octaves;
    float fraction = octaves - (float)oct;
    if (fraction < 0.0f) {
        fraction += 1.0f;
        oct--;
    }
    float scale = .009166666666666667f;
    for (; oct < 0; oct++)
        scale *= .5f;
    for (; oct > 0; oct--)
        scale *= 2.0f;
    return scale * (1.0f + fraction * (.693147181f + fraction *
        (.240226507f + fraction * (.055504109f + fraction *
        (.009618129f + fraction * .001333356f)))));
}

void fold_track(unsigned track, const NativeFrame *frame, float *out)
{
    FoldVoice *voice = fold_voices + track;
    if (frame->reset || !frame->active)
        voice->valid = 0;
    if (frame->trigger && !frame->reset) {
        fold_reset(voice);
        voice->initial_delay = track;
    }
    if (!voice->valid) {
        for (unsigned i = 0; i < FOLD_BLOCK; i++)
            out[i] = 0.0f;
        return;
    }

    FoldControls control;
    control.fold = frame->fold < 16384u
        ? ((float)frame->fold - 16384.0f) * .00006103515625f
        : ((float)frame->fold - 16384.0f) * .000062003968253968f;
    const float knob_unit = .000030757874015748f;
    control.nest = (float)frame->nest * knob_unit;
    control.memory = (float)frame->memory * knob_unit;
    control.clock = (float)frame->clock * knob_unit;
    control.drift = (float)frame->drift * knob_unit;
    float note = clip(((float)frame->pitch + (float)frame->tune - 16384.0f)
                        * .00390625f, 0.0f, 127.0f);
    fold_render(voice, &control, pitch_increment(note), out);
}
