/* Original, freestanding Sample Lab processing core. No native buffer ownership. */
#ifndef SAMPLE_LAB_ENGINE_H
#define SAMPLE_LAB_ENGINE_H
typedef signed short sl_i16;
typedef unsigned int sl_u32;
typedef __UINTPTR_TYPE__ sl_uptr;
enum { SL_OK=0, SL_INVALID=-1, SL_CAPACITY=-2, SL_NO_CONTEXT=-3,
       SL_OVERLAP=-4, SL_STALE=-5, SL_LIMIT=-6, SL_INSUFFICIENT=-7 };
#define SL_MAX_REGIONS 64
#define SL_MAX_FRAMES 3168000u
typedef struct { sl_u32 start, end; } SLRegion; /* frames, end EXCLUSIVE */
typedef struct {
    const sl_i16 *source; /* caller-owned immutable snapshot, NEVER a borrowed SPI reply */
    sl_u32 frames, channels, rate, generation, count, selected;
    SLRegion regions[SL_MAX_REGIONS];
} SLSession;
int sl_detect(const sl_i16 *,sl_u32,sl_u32,sl_u32,sl_u32,sl_u32,SLRegion *,sl_u32,sl_u32 *);
int sl_snap(const sl_i16 *,sl_u32,sl_u32,sl_u32,sl_u32,sl_u32,sl_u32,sl_u32 *);
int sl_copy(const sl_i16 *,sl_u32,sl_u32,sl_u32,sl_u32,sl_i16 *,sl_u32);
/* Linear audio crossfade with smoothstep weight; use pre-roll or post-roll.
 * Output retains EXACTLY end-start frames. Neither source nor destination is
 * touched on a rejected request. No context -> explicit error, never shorten. */
int sl_loop(const sl_i16 *,sl_u32,sl_u32,sl_u32,sl_u32,sl_u32,sl_i16 *,sl_u32);
int sl_beat_frames(sl_u32,sl_u32,sl_u32,sl_u32 *);
int sl_open(SLSession *,const sl_i16 *,sl_u32,sl_u32,sl_u32,sl_u32);
int sl_analyze(SLSession *,sl_u32,sl_u32,sl_u32);
/* COUNT selects actual detected onsets by attack strength; an insufficient
 * source leaves the current grid untouched and reports how many were found. */
int sl_analyze_count(SLSession *,sl_u32,sl_u32,sl_u32 *);
/* EVEN divides the entire immutable recording into exactly count regions. */
int sl_analyze_even(SLSession *,sl_u32,sl_u32);
int sl_select(SLSession *,sl_u32,sl_u32);
int sl_render(SLSession *,sl_u32,sl_u32,sl_i16 *,sl_u32);
void sl_invalidate(SLSession *);
#endif
