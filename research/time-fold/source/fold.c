#include "fold.h"

#define BLOCKS_PER_MAP 16
#define MAP_STEP 0.010666666666666667f
#define COEFFICIENT_STEP 0.001953125f
#define PHASE_STEP 0.03125f
#define INVERSE_STEP 0.000244140625f

static const float cycle_sine[FOLD_POINTS] = {
    0.0f, .195090322f, .382683432f, .555570233f,
    .707106781f, .831469612f, .923879533f, .980785280f,
    1.0f, .980785280f, .923879533f, .831469612f,
    .707106781f, .555570233f, .382683432f, .195090322f,
    0.0f, -.195090322f, -.382683432f, -.555570233f,
    -.707106781f, -.831469612f, -.923879533f, -.980785280f,
    -1.0f, -.980785280f, -.923879533f, -.831469612f,
    -.707106781f, -.555570233f, -.382683432f, -.195090322f
};

static float clip(float value, float lo, float hi)
{
    if (value < lo)
        return lo;
    return value > hi ? hi : value;
}

static float wrap(float value)
{
    float fraction = value - (float)(int)value;
    return fraction < 0.0f ? fraction + 1.0f : fraction;
}

static float sine(float phase)
{
    float x = phase;
    if (x > .5f)
        x -= 1.0f;
    if (x > .25f)
        x = .5f - x;
    if (x < -.25f)
        x = -.5f - x;
    float t = x * x;
    return x * (6.28318531f + t * (-41.3417022f + t *
        (81.6052493f + t * (-76.7058598f + t * 42.0586939f))));
}

static float bump(float cell)
{
    float x = cell * (1.0f - cell);
    return 16.0f * x * x;
}

/* Degree-one lift. Subtract the moving origin to keep every note-cycle
   endpoint fixed while the clock moves the inner intervals. */
static float lift(float phase, float cells, float width, float depth, float pivot)
{
    return phase + depth * width *
        (bump(wrap(phase * cells + pivot)) - bump(pivot));
}

static float reverse_map(const float *map, float phase)
{
    unsigned left = 0, right = FOLD_POINTS;
    while (right - left > 1) {
        unsigned middle = (left + right) >> 1;
        if (map[middle] <= phase)
            left = middle;
        else
            right = middle;
    }
    float a = map[left], b = map[right];
    /* Invert the piecewise-linear cell by fixed bisection. Its width
       has a positive bound, and this avoids target-dependent division. */
    unsigned fraction = 0;
    unsigned bit = 1u << 11;
    while (bit) {
        float candidate = (float)(fraction + bit) * INVERSE_STEP;
        if (a + (b - a) * candidate <= phase)
            fraction += bit;
        bit >>= 1;
    }
    return ((float)left + (float)fraction * INVERSE_STEP) * PHASE_STEP;
}

void fold_reset(FoldVoice *v)
{
    v->phase = v->increment = 0.0f;
    v->clock = v->subclock = v->drift = 0.0f;
    v->instability = .347f;
    v->accent = 1.0f;
    v->page = v->stage = v->inverse = 0;
    v->initial_delay = v->has_history = 0;
    for (unsigned j = 0; j < FOLD_HARMONICS; j++) {
        v->spectrum.sine[j] = v->goal.sine[j] = (j == 0);
        v->spectrum.cosine[j] = v->goal.cosine[j] = 0.0f;
        v->motion.sine[j] = v->motion.cosine[j] = 0.0f;
    }
    v->valid = 1u;
}

static void start_map(FoldVoice *v, const FoldControls *c)
{
    float fold = clip(c->fold, -1.0f, 1.0f);
    v->inverse = fold < 0.0f;
    if (v->inverse)
        fold = -fold;
    v->nest = clip(c->nest, 0.0f, 1.0f);
    v->memory = .87f * clip(c->memory, 0.0f, 1.0f);
    float speed = clip(c->clock, 0.0f, 1.0f);
    float instability = clip(c->drift, 0.0f, 1.0f);
    float next = v->clock + MAP_STEP * (.12f + 5.88f * speed * speed);
    if (next >= 1.0f)
        v->instability = (3.35f + .60f * instability) *
            v->instability * (1.0f - v->instability);
    v->clock = wrap(next);
    v->drift += .13f * (instability * (v->instability - .5f) - v->drift);
    v->subclock = wrap(v->subclock + MAP_STEP * (.12f + 5.88f * speed * speed) *
        (1.0f + 2.0f * v->nest + v->drift));
    v->pivot = wrap(.14f + .13f * sine(v->clock) + .32f * v->drift);
    v->subpivot = wrap(.65f + .24f * sine(v->subclock) - .28f * v->drift);
    v->depth = fold * (.17f + .11f * v->accent);
    v->accent *= .90f + .095f * v->memory;

    for (unsigned j = 0; j < FOLD_HARMONICS; j++)
        v->target.sine[j] = v->target.cosine[j] = 0.0f;
}

static void advance_map(FoldVoice *v, const FoldControls *controls)
{
    if (!v->stage)
        start_map(v, controls);
    const float *old = v->maps[v->page];
    float *next = v->maps[v->page ^ 1u];
    unsigned first = (v->stage & 7u) << 2;

    if (v->stage < 8u) {
        for (unsigned i = first; i < first + 4u; i++) {
            float phase = (float)i * PHASE_STEP;
            float inner = lift(phase, 4.0f, .25f, v->depth * v->nest, v->subpivot);
            float middle = lift(inner, 2.0f, .5f, v->depth * v->nest, v->pivot);
            float driven = lift(middle, 1.0f, 1.0f, v->depth, 0.0f);
            float previous = v->has_history ? old[i] : phase;
            float history = lift(previous, 2.0f, .5f, v->depth * .12f, v->subpivot);
            next[i] = driven + v->memory * (history - driven);
        }
        next[FOLD_POINTS] = 1.0f;
    } else {
        for (unsigned i = first; i < first + 4u; i++) {
            float phase = (float)i * PHASE_STEP;
            if (i)
                phase = v->inverse ? reverse_map(next, phase) : next[i];
            float warped = sine(phase) * .0625f;
            unsigned harmonic = i;
            for (unsigned j = 0; j < FOLD_HARMONICS; j++) {
                v->target.sine[j] += warped * cycle_sine[harmonic & (FOLD_POINTS - 1)];
                v->target.cosine[j] += warped *
                    cycle_sine[(harmonic + FOLD_POINTS / 4) & (FOLD_POINTS - 1)];
                harmonic += i;
            }
        }
    }
}

void fold_render(FoldVoice *v, const FoldControls *controls, float increment, float *out)
{
    if (!v->valid)
        fold_reset(v);
    increment = clip(increment, 0.00002f, .475f);
    if (v->increment == 0.0f)
        v->increment = increment;
    float pitch_step = (increment - v->increment) * .03125f;
    float conservative_pitch = increment > v->increment ? increment : v->increment;
    float gains[FOLD_HARMONICS + 1];
    for (unsigned j = 0; j < FOLD_HARMONICS; j++) {
        float frequency = conservative_pitch * (float)(j + 1);
        gains[j] = clip((.46f - frequency) * 12.5f, 0.0f, 1.0f);
    }
    gains[FOLD_HARMONICS] = pitch_step;

    unsigned waiting = v->initial_delay;
    if (waiting)
        v->initial_delay--;
    else
        advance_map(v, controls);
#ifdef FOLD_NATIVE
    extern void fold_emit(FoldVoice *, const float *, float *);
    fold_emit(v, gains, out);
#else
    for (unsigned i = 0; i < FOLD_BLOCK; i++) {
        float s = sine(v->phase);
        float c = sine(wrap(v->phase + .25f));
        float sh = s, ch = c, out_sample = 0.0f;
        for (unsigned j = 0; j < FOLD_HARMONICS; j++) {
            out_sample += gains[j] * (v->spectrum.sine[j] * sh + v->spectrum.cosine[j] * ch);
            v->spectrum.sine[j] += v->motion.sine[j];
            v->spectrum.cosine[j] += v->motion.cosine[j];
            float next_sine = sh * c + ch * s;
            ch = ch * c - sh * s;
            sh = next_sine;
        }
        out[i] = .65f * out_sample;
        v->phase = wrap(v->phase + v->increment);
        v->increment += pitch_step;
    }
#endif
    v->increment = increment;

    if (!waiting && ++v->stage == BLOCKS_PER_MAP) {
        v->stage = 0;
        v->page ^= 1u;
        v->has_history = 1u;
        for (unsigned j = 0; j < FOLD_HARMONICS; j++) {
            v->spectrum.sine[j] = v->goal.sine[j];
            v->spectrum.cosine[j] = v->goal.cosine[j];
            v->goal.sine[j] = v->target.sine[j];
            v->goal.cosine[j] = v->target.cosine[j];
            v->motion.sine[j] = (v->target.sine[j] - v->spectrum.sine[j]) * COEFFICIENT_STEP;
            v->motion.cosine[j] = (v->target.cosine[j] - v->spectrum.cosine[j]) * COEFFICIENT_STEP;
        }
    }
}
