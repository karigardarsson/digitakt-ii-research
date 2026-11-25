#ifndef TIME_FOLD_H
#define TIME_FOLD_H

#define FOLD_POINTS 32
#define FOLD_HARMONICS 8
#define FOLD_BLOCK 32

typedef struct {
    float fold, nest, memory, clock, drift;
} FoldControls;

typedef struct {
    float sine[FOLD_HARMONICS], cosine[FOLD_HARMONICS];
} FoldSpectrum;

typedef struct {
    float phase, increment, clock, subclock, instability, drift, accent;
    float depth, nest, memory, pivot, subpivot;
    float maps[2][FOLD_POINTS + 1];
    FoldSpectrum spectrum, target, goal, motion;
    unsigned page, stage, inverse, valid;
    unsigned initial_delay, has_history;
} FoldVoice;

void fold_reset(FoldVoice *voice);
void fold_render(FoldVoice *voice, const FoldControls *controls,
                 float increment, float *output);

#endif
