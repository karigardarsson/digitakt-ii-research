/* Immutable recording peak index. Exact min/max, not a sampled sketch. */
#ifndef SAMPLE_LAB_VISUAL_H
#define SAMPLE_LAB_VISUAL_H
#include "engine.h"
typedef struct {sl_i16 low,high;} SLPeak;
typedef struct {
    const sl_i16 *source;SLPeak *tree;
    sl_u32 frames,channels,leaves,peak;
} SLWave;
sl_u32 sl_wave_bytes(sl_u32 frames);
int sl_wave_build(SLWave *,const sl_i16 *,sl_u32,sl_u32,void *,sl_u32,volatile sl_u32 *);
int sl_wave_range(const SLWave *,sl_u32,sl_u32,SLPeak *);
int sl_wave_columns(const SLWave *,sl_u32,sl_u32,SLPeak *,sl_u32);
#endif
