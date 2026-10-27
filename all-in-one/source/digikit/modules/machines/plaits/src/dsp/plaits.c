/* PLAITS for the Digitakt II: the voice wrapper (plaits.h). Plain C99, float only; host and Selache builds.
 *
 * Per Digitakt block (32 samples at 48 kHz):
 *   note      = pitch / 256 + (tune / 256 - 64)                 (the frame's note word and TUNE)
 *   harmonics, timbre, morph = word / 0x7f00, clamped to [0, 1]
 *   mix       = word / 0x7f00, clamped: out = OUT + mix (AUX - OUT)
 *   two engine renders of 16 samples (va_render for model 0, ws_render 1, fm_render 2, grain_render 3,
 *   additive_render 4, wavetable_render 5, chord_render 6, speech_render 7, swarm_render 8, noise_render 9, dust_render 10, string_engine_render 11, modal_render 12, kick_render 13, snare_render 14, hihat_render 15, vcf_render 16, phase_dist_render 17, terrain_render 21, string_machine_render 22, square_render 23), each
 *   followed by Plaits' output stage
 *   (post: the model's OUT and AUX gains, a negative gain through stmlib's Limiter), then the mix, the DC blocker
 *   (one pole at 5 Hz) and the output gain.
 *   Plaits' trigger (Params.trigger) as Plaits' Voice builds it with TRIG patched and the gate held high from the
 *   note-on: RISING_EDGE | HIGH in the first sub-block of a note-on, HIGH in every other sub-block (see TRIG_*);
 *   accent from the note-on's velocity (velocity / 127, latched at the note-on; note_on). Of models 0..7 only
 *   SPEECH (7), RINGS (12), KICK (13), SNARE (14) and HIHAT (15) read them (the strike engines strike at
 *   each rising edge).
 * The model is latched at a note-on; the engine is initialised at the voice's first note-on and when a note-on changes
 * the model, and otherwise runs on from note to note (note_on). A model without an engine renders silence.
 */
#include "plaits.h"
#include "dsp_util.h"
#include "lib/tables.h"

/* 1 / 0x7f00: the full scale of the HARMONICS, TIMBRE, MORPH and MIX rows. */
#define PL_INV_FULL 3.0757874e-05f
#define PL_INV_256 0.00390625f

/* DC blocker y[n] = x[n] - x[n-1] + R y[n-1], R = 1 - 2 pi 5 / 48000 (pole at 5 Hz). */
#define PL_DC_R 0.99934550f

/* Plaits' output stage (plaits/dsp/voice.cc RegisterInstance, ChannelPostProcessor in plaits/dsp/voice.h): each
 * engine has an OUT and an AUX gain; a positive gain scales the channel, a negative one sends it through stmlib's
 * Limiter (stmlib/dsp/limiter.h) with pre-gain -gain and no further gain. Indexed by PLAITS model number: model n is
 * upstream's engine (n + 8) mod 24 (Plaits registers the engine2 engines first). IEEE single bit patterns of the
 * floats (Selache's C writes negative floats in initialised tables with wrong bits, docs/findings/20-patch-toolchains.md),
 * read through PL_F. Upstream then multiplies by -32767 and clips to 16 bits for its DAC: that step is not ported
 * (float throughout; the design's sample format). tests/plaits/test_continuous_host.py checks every entry against
 * voice.cc. */
const unsigned int plaits_out_gain[25] = {
    0x3f4ccccdu, 0x3f333333u, 0x3f19999au, 0x3f333333u, /*  0 VA 0.8, ASYM-TRI 0.7, 2FM 0.6, FORMANT 0.7 */
    0x3f4ccccdu, 0x3f19999au, 0x3f4ccccdu, 0xbf333333u, /*  4 ADDITIVE 0.8, WAVETABLE 0.6, CHORD 0.8, SPEECH -0.7 */
    0xc0400000u, 0xbf800000u, 0xc0000000u, 0xbf800000u, /*  8 SWARM -3, NOISE -1, DUST -2, STRING -1 */
    0xbf800000u, 0x3f4ccccdu, 0x3f4ccccdu, 0x3f4ccccdu, /* 12 RINGS -1, KICK 0.8, SNARE 0.8, HIHAT 0.8 */
    0x3f800000u, 0x3f333333u, 0x3f800000u, 0x3f800000u, /* 16 VCF-VA 1, PHASE-DIST 0.7, DX7 1, DX7 1 */
    0x3f800000u, 0x3f333333u, 0x3f4ccccdu, 0x3f000000u, /* 20 DX7 1, TERRAIN 0.7, STRING MACHINE 0.8, SQUARE 0.5 */
    0x3f800000u,                                        /* 24 DX7-4: the six-op engine again, the row of model 18 */
};
const unsigned int plaits_aux_gain[25] = {
    0x3f4ccccdu, 0x3f19999au, 0x3f19999au, 0x3f19999au, /*  0 VA 0.8, ASYM-TRI 0.6, 2FM 0.6, FORMANT 0.6 */
    0x3f4ccccdu, 0x3f19999au, 0x3f4ccccdu, 0x3f4ccccdu, /*  4 ADDITIVE 0.8, WAVETABLE 0.6, CHORD 0.8, SPEECH 0.8 */
    0x3f800000u, 0xbf800000u, 0x3f800000u, 0x3f4ccccdu, /*  8 SWARM 1, NOISE -1, DUST 1, STRING 0.8 */
    0x3f4ccccdu, 0x3f4ccccdu, 0x3f4ccccdu, 0x3f4ccccdu, /* 12 RINGS 0.8, KICK 0.8, SNARE 0.8, HIHAT 0.8 */
    0x3f800000u, 0x3f333333u, 0x3f800000u, 0x3f800000u, /* 16 VCF-VA 1, PHASE-DIST 0.7, DX7 1, DX7 1 */
    0x3f800000u, 0x3f333333u, 0x3f4ccccdu, 0x3f000000u, /* 20 DX7 1, TERRAIN 0.7, STRING MACHINE 0.8, SQUARE 0.5 */
    0x3f800000u,                                        /* 24 DX7-4: the six-op engine again, the row of model 18 */
};

/* Output gain, measured in the emulator (tools/patchkit/pk_proof_plaits.py part g, DT2 1.17, 2026-10-17): on track 1 at
 * the default LEV, the default project's stock sample (int16 peak 0.49997, RMS at full scale 0.7071) gives an output
 * peak of 0.0737 per unit of sample peak, so a full-scale copy of it would give an output RMS of 0.0521; default VA
 * (HARMONICS, TIMBRE, MORPH 0x4000, MIX 0) at note 60 with gain 1 gives 0.0867 on the same track. 0.0521 / 0.0867 =
 * 0.601: default VA at note 60 then has the RMS of that full-scale stock sample at the same LEV (voice level: RMS 0.29,
 * peak 0.52). Since Task 10b the voice carries Plaits' per-engine output gains (plaits_out_gain: VA 0.8), so this gain is
 * 0.601 / 0.8 = 0.75125: default VA keeps the calibrated level and every other model keeps Plaits' own balance
 * against it. */
#ifndef PL_OUTPUT_STAGE_NEUTRAL
const float plaits_gain = 0.75125f;
#else
/* Host tests only (tests/patchkit/test_plaits_model_digests.py): the wrapper as it was before Task 10b, without
 * Plaits' output stage (post), with the gain 0.601 and the fixed accent 0.8, to show that nothing else changed. */
const float plaits_gain = 0.601f;
#endif

/* unit(word): the knob word (0 .. 0x7f00) as 0 .. 1. A macro (selcc has no inlining): the same operations as the
   function it replaces, without its call and frame on every block. */
#define PL_UNIT(dst, word) { dst = (word) * PL_INV_FULL; CONSTRAIN(dst, 0.0f, 1.0f); }

static void zeros(float *out, int n)
{
    int i;
    for (i = 0; i < n; i++) {
        out[i] = 0.0f;
    }
}

void plaits_dc_block(PlaitsDcBlock *s, float *x, int n)
{
    float x1 = s->x1;
    float y1 = s->y1;
    int i;
    for (i = 0; i < n; i++) {
        float in = x[i];
        y1 = in - x1 + PL_DC_R * y1;
        x1 = in;
        x[i] = y1;
    }
    s->x1 = x1;
    s->y1 = y1;
}

void plaits_voice_clear(PlaitsVoice *v)
{
    v->armed = 0;
    v->released = 0;
    v->model = 0;
    v->dc.x1 = 0.0f;
    v->dc.y1 = 0.0f;
}

/* The DX7 models: 18, 19, 20 (upstream's three banks) and 24 (the owner's bank); bank 0..3. */
#define PL_IS_DX7(model) (((model) >= PLAITS_MODEL_DX7_1 && (model) <= PLAITS_MODEL_DX7_3) || (model) == PLAITS_MODEL_DX7_4)

static int plaits_dx7_bank(int model)
{
    return model == PLAITS_MODEL_DX7_4 ? 3 : model - PLAITS_MODEL_DX7_1;
}

/* A note-on latches the model. The engine is (re)initialised only when the voice has no running engine yet (armed is
 * 0: the voice state is zeroed at its first block, plaits_voice_mem below) or the latched model changes; every other note-on leaves
 * the engine, the DC blocker and the interpolator running, as Plaits keeps its engine across triggers (voice.cc: Init
 * at power-up, and at an engine change only LoadUserData and Reset). Here the engines share one union, so a model
 * change cannot resume the new engine's old state as Plaits does: it starts from Plaits' Init + LoadUserData(NULL) +
 * Reset (additive_init and chord_init include Reset, the other engines' Reset is empty; WAVETABLE's LoadUserData(NULL)
 * is the static wave map of lib/waves.c), with the DC blocker and the interpolator cleared, as Plaits resets its
 * post-processor. A note-on of the latched model still reaches the engine as Plaits' trigger (render_engine): SPEECH
 * starts its consonant or word there, as Plaits' engine does at a trigger without being re-initialised.
 * Plaits' output limiters (post) follow its Voice: both are initialised at power-up (here: the voice's first note-on),
 * the OUT one again at every engine change (ChannelPostProcessor::Reset, called for out_post_processor_ only).
 * Every note-on latches the accent: velocity / 127 (the velocity word / 0x7f00, as the knobs), which puts the
 * Digitakt's default velocity 100 at 0.787, next to Plaits' 0.8 with LEVEL unpatched. */
static void note_on(PlaitsVoice *v, const PlaitsFrame *f, int rate)
{
    int model = (int)(f->model * PL_INV_256);
#ifndef PL_OUTPUT_STAGE_NEUTRAL
    PL_UNIT(v->accent, f->velocity)
#else
    v->accent = 0.8f;
#endif
    if (v->armed && model == v->model) {
        return;
    }
    if (!v->armed) {
        v->aux_peak = 0.5f;
    }
    v->out_peak = 0.5f;
    v->model = model;
    if (model == PLAITS_MODEL_ASYM_TRI) {
        ws_init(PL_WS(v));
    } else if (model == PLAITS_MODEL_2FM) {
        fm_init(PL_FM(v));
    } else if (model == PLAITS_MODEL_FORMANT) {
        grain_init(PL_GRAIN(v));
    } else if (model == PLAITS_MODEL_ADDITIVE) {
        additive_init(PL_ADD(v));
    } else if (model == PLAITS_MODEL_WAVETABLE) {
        wavetable_init(PL_WT(v));
    } else if (model == PLAITS_MODEL_CHORD) {
        chord_init(PL_CHORD(v));
    } else if (model == PLAITS_MODEL_SPEECH) {
        speech_init(PL_SPEECH(v));
    } else if (model == PLAITS_MODEL_SWARM) {
        swarm_init(PL_SWARM(v));
        swarm_seed(PL_SWARM(v), v->track);
    } else if (model == PLAITS_MODEL_NOISE) {
        noise_init(PL_NOISE(v));
        noise_seed(PL_NOISE(v), v->track);
    } else if (model == PLAITS_MODEL_DUST) {
        dust_init(PL_DUST(v));
        dust_seed(PL_DUST(v), v->track);
    } else if (model == PLAITS_MODEL_STRING) {
        string_engine_init(PL_STRING(v));
        string_engine_seed(PL_STRING(v), v->track);
    } else if (model == PLAITS_MODEL_RINGS) {
        modal_init(PL_MODAL(v));
    } else if (model == PLAITS_MODEL_KICK) {
        kick_init(PL_KICK(v));
    } else if (model == PLAITS_MODEL_SNARE) {
        snare_init(PL_SNARE(v));
    } else if (model == PLAITS_MODEL_HIHAT) {
        hihat_init(PL_HIHAT(v));
    } else if (model == PLAITS_MODEL_VCF) {
        vcf_init(PL_VCF(v));
    } else if (model == PLAITS_MODEL_SQUARE) {
        square_init(PL_SQUARE(v));
    } else if (model == PLAITS_MODEL_STRING_MACHINE) {
        string_machine_init(PL_STRING_MACHINE(v));
    } else if (model == PLAITS_MODEL_PHASE_DIST) {
        phase_dist_init(PL_PHASE_DIST(v));
    } else if (model == PLAITS_MODEL_TERRAIN) {
        terrain_init(PL_TERRAIN(v));
    } else if (PL_IS_DX7(model)) {
        /* Voice's engine change for the six-op engine: Init, LoadUserData(its bank), Reset */
        six_op_init(PL_SIX_OP(v));
        six_op_load_bank(PL_SIX_OP(v), plaits_dx7_bank(model));
        six_op_seed(PL_SIX_OP(v), v->track);
    } else {
        va_init(PL_VA(v));
    }
    v->dc.x1 = 0.0f;
    v->dc.y1 = 0.0f;
    /* The interpolator runs only at 96 kHz; at 48 kHz it is never read or written, so it stays as the zero state
       at load (the 718 instructions of hb_init are skipped in the 48 kHz build's note-on blocks). */
    if (rate == 96000) {
        hb_init(&v->hb);
    }
    v->armed = 1;
}

/* Plaits' trigger, as its Voice builds it with TRIG patched (plaits/dsp/voice.cc: (rising_edge ? RISING_EDGE : LOW)
 * | (gate high ? HIGH : LOW)). The Digitakt gives the voice routine the note-on only (the trigger latch), not the
 * note's end, so the gate is taken as high from a note-on to the next: RISING_EDGE | HIGH in the note-on's first
 * sub-block, HIGH after. No ported engine reads HIGH apart from the rising edge (in Plaits only the DX7 engines,
 * which are not ported, read it), so the choice of HIGH or LOW after the edge does not change SPEECH's output
 * (tests/plaits/test_speech_host.py); HIGH with the edge does: SPEECH's naive and SAM synths compare the trigger with
 * RISING_EDGE alone, which Plaits' patched trigger never equals. */
#define TRIG_RISING_HIGH 5
#define TRIG_HIGH 4
/* SWARM only, the owner's decision (2026-10-22): the continuous mode of Plaits' unpatched trigger. Every block of model 8 carries
 * TRIGGER_UNPATCHED alone (no RISING_EDGE, no HIGH): note-ons do not start bursts, the grains run on at a steady random rate,
 * and the Digitakt's amp envelope shapes the note. A documented exception to the convention above. */
#define TRIG_UNPATCHED 2

#if defined(__ADSP21569__) && !defined(PL_POST_C) && !defined(PL_OUTPUT_STAGE_NEUTRAL)
/* The DSP build: each sub-block's output stage (both plaits_post calls, the mix, the DC blocker and the output gain)
   runs in post_sm.s, bit-identical to the C below, which stays for the host and as the -DPL_POST_C reference
   (tests/plaits/test_post_kernel.py). t = &v->out[0]: OUT, AUX, dc, out_peak, aux_peak follow it (plaits.h). */
#define PL_POST_KERNEL 1
void plaits_stage_kernel(float *t, float *dst, const float *k);
#endif

/* Plaits' ChannelPostProcessor::Process without the low-pass gate (bypassed: no Plaits envelope here, the Digitakt's
 * does that work): a negative gain is stmlib's Limiter::Process(-gain) in place (peak_ is *peak;
 * SLOPE(peak_, fabsf(s), 0.05f, 0.00002f)), a positive one a plain gain; then the saturation of its DAC step,
 * Clip16(1 + (int)(x * gain * -32767)), in full-scale units: clamped to [-1, 1] (the step's 16-bit quantisation and
 * its one-LSB offset are not ported: float throughout). The clip bounds any engine's overshoot, as in Plaits. A NaN or infinite input sample is taken as 0 first, so that
 * nothing downstream (the limiter's peak, the DC blocker) can latch it (upstream's Clip16 collapses a NaN to a finite
 * value too; its limiter would keep it). */
void plaits_post(float *peak, float gain, float *x, int n)
{
    int i;
    float y;
    for (i = 0; i < n; i++) {
        y = x[i];
        if (!(y - y == 0.0f)) {
            x[i] = 0.0f;
        }
    }
    if (gain < 0.0f) {
        float pre = -gain;
        float pk = *peak;
        for (i = 0; i < n; i++) {
            float s = x[i] * pre;
            float error = absf(s) - pk;
            float g;
            pk += (error > 0.0f ? 0.05f : 0.00002f) * error;
            g = pk <= 1.0f ? 1.0f : 1.0f / pk;
            y = s * g * 0.8f;
            CONSTRAIN(y, -1.0f, 1.0f)
            x[i] = y;
        }
        *peak = pk;
    } else {
        for (i = 0; i < n; i++) {
            y = x[i] * gain;
            CONSTRAIN(y, -1.0f, 1.0f)
            x[i] = y;
        }
    }
}

/* The latched model's engine for one sub-block: a binary tree of model tests (five per call), not the chain of up to
   15 tests it replaces, so the executed path touches a few code lines. Only models with an engine reach it
   (PL_PORTED); the leaves are the engines, in model order (DX7: 18, 19, 20 and 24). */
static void render_sub(PlaitsVoice *v, const Params *p)
{
    int m = v->model;
    if (m < 10) {
        if (m < 5) {
            if (m < 2) {
                if (m < 1) {
                    va_render(PL_VA(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    ws_render(PL_WS(v), p, v->out, v->aux, PLAITS_SUB);
                }
            } else {
                if (m < 3) {
                    fm_render(PL_FM(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    if (m < 4) {
                        grain_render(PL_GRAIN(v), p, v->out, v->aux, PLAITS_SUB);
                    } else {
                        additive_render(PL_ADD(v), p, v->out, v->aux, PLAITS_SUB);
                    }
                }
            }
        } else {
            if (m < 7) {
                if (m < 6) {
                    wavetable_render(PL_WT(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    chord_render(PL_CHORD(v), p, v->out, v->aux, PLAITS_SUB);
                }
            } else {
                if (m < 8) {
                    speech_render(PL_SPEECH(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    if (m < 9) {
                        swarm_render(PL_SWARM(v), p, v->out, v->aux, PLAITS_SUB);
                    } else {
                        noise_render(PL_NOISE(v), p, v->out, v->aux, PLAITS_SUB);
                    }
                }
            }
        }
    } else {
        if (m < 15) {
            if (m < 12) {
                if (m < 11) {
                    dust_render(PL_DUST(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    string_engine_render(PL_STRING(v), p, v->out, v->aux, PLAITS_SUB);
                }
            } else {
                if (m < 13) {
                    modal_render(PL_MODAL(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    if (m < 14) {
                        kick_render(PL_KICK(v), p, v->out, v->aux, PLAITS_SUB);
                    } else {
                        snare_render(PL_SNARE(v), p, v->out, v->aux, PLAITS_SUB);
                    }
                }
            }
        } else {
            if (m < 21) {
                if (m < 16) {
                    hihat_render(PL_HIHAT(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    if (m < 17) {
                        vcf_render(PL_VCF(v), p, v->out, v->aux, PLAITS_SUB);
                    } else {
                        if (m < 18) {
                            phase_dist_render(PL_PHASE_DIST(v), p, v->out, v->aux, PLAITS_SUB);
                        } else {
                            six_op_render(PL_SIX_OP(v), p, v->out, v->aux, PLAITS_SUB);
                        }
                    }
                }
            } else {
                if (m < 22) {
                    terrain_render(PL_TERRAIN(v), p, v->out, v->aux, PLAITS_SUB);
                } else {
                    if (m < 23) {
                        string_machine_render(PL_STRING_MACHINE(v), p, v->out, v->aux, PLAITS_SUB);
                    } else {
                        if (m < 24) {
                            square_render(PL_SQUARE(v), p, v->out, v->aux, PLAITS_SUB);
                        } else {
                            six_op_render(PL_SIX_OP(v), p, v->out, v->aux, PLAITS_SUB);
                        }
                    }
                }
            }
        }
    }
}

/* 32 samples at 48 kHz of the latched model (0..10, 12..24) into dst; trig: a note-on in this block. */
static void render_engine(PlaitsVoice *v, const PlaitsFrame *f, int trig, float *dst)
{
    /* The DX7 gate's state: a note-on clears the note-off, a release pulse of the DSP (PlaitsFrame.released: non-zero in
       the block of the note-off, 0 again after) sets it until the next note-on. */
    if (trig) {
        v->released = 0;
    }
    if (f->released != 0) {
        v->released = 1;
    }

    Params p;
    float mix;
    int s;
#ifdef PL_POST_KERNEL
    float k[4];
#else
    int i;
#endif

    p.note = f->pitch * PL_INV_256 + (f->tune * PL_INV_256 - 64.0f);
    PL_UNIT(p.harmonics, f->harmonics)
    PL_UNIT(p.timbre, f->timbre)
    PL_UNIT(p.morph, f->morph)
    PL_UNIT(mix, f->mix)
    p.accent = v->accent;
#ifdef PL_POST_KERNEL
    k[0] = PL_F(plaits_out_gain)[v->model];
    k[1] = PL_F(plaits_aux_gain)[v->model];
    k[2] = mix;
    k[3] = plaits_gain;
#endif
    for (s = 0; s < PLAITS_BLOCK; s += PLAITS_SUB) {
        p.trigger = (trig && s == 0) ? TRIG_RISING_HIGH : TRIG_HIGH;
        if (v->model == PLAITS_MODEL_SWARM) {
            p.trigger = TRIG_UNPATCHED;
        } else if (PL_IS_DX7(v->model)) {
            /* The DX7 exception: the gate is high from the note-on (RISING_EDGE | HIGH in its first sub-block) until the
               Digitakt's note-off (v->released: latched from the DSP's release pulse f->released, cleared at the next
               note-on), then LOW. */
            p.trigger = (trig && s == 0) ? TRIG_RISING_HIGH : (v->released != 0 ? 0 : TRIG_HIGH);
        }
        render_sub(v, &p);
#ifdef PL_POST_KERNEL
        plaits_stage_kernel(v->out, dst + s, k);
    }
#else
#ifndef PL_OUTPUT_STAGE_NEUTRAL
        plaits_post(&v->out_peak, PL_F(plaits_out_gain)[v->model], v->out, PLAITS_SUB);
        plaits_post(&v->aux_peak, PL_F(plaits_aux_gain)[v->model], v->aux, PLAITS_SUB);
#endif
        for (i = 0; i < PLAITS_SUB; i++) {
            float o = v->out[i];
            dst[s + i] = o + mix * (v->aux[i] - o);
        }
    }
    plaits_dc_block(&v->dc, dst, PLAITS_BLOCK);
    for (i = 0; i < PLAITS_BLOCK; i++) {
        dst[i] = dst[i] * plaits_gain;
    }
#endif
}

/* The models with an engine: 0..7, SWARM 8, NOISE 9, DUST 10, STRING 11, RINGS 12, KICK 13, SNARE 14, HIHAT 15, VCF-VA 16, PHASE-DIST 17, DX7 18..20, TERRAIN 21, STRING MACHINE 22, SQUARE 23, DX7-4 24. */
/* Every model 0 .. 24 has an engine now, so the test is a range (a macro: no call and frame on every block). */
#define PL_PORTED(model) ((model) >= PLAITS_MODEL_VA && (model) <= PLAITS_MODEL_DX7_4)

#if defined(__ADSP21569__) && !defined(PL_WRAP_C) && !defined(PL_POST_C) && !defined(PL_OUTPUT_STAGE_NEUTRAL)
/* The DSP build: plaits_render at 48 kHz, render_engine and render_sub run in wrap_sm.s (lib/gen_wrap_kernel.py),
   bit-identical to the C below, which stays for the host, for other rates (the kernel jumps here) and as the
   -DPL_WRAP_C reference (tests/plaits/test_wrap_kernel.py). The kernel reads the voice at fixed offsets. */
#define PL_WRAP_KERNEL 1
typedef char pl_engine_size_check[(sizeof(PlaitsEngine) == 16572) ? 1 : -1]; /* E in lib/gen_wrap_kernel.py */
void plaits_note_on_c(PlaitsVoice *v, const PlaitsFrame *f, int rate)
{
    note_on(v, f, rate);
}
#define plaits_render plaits_render_c
#endif

int plaits_render(PlaitsVoice *v, const PlaitsFrame *f, int trig, float *out, int n, int rate)
{
    float *dst;

    if (trig) {
        note_on(v, f, rate);
    }
    if (!v->armed || !f->active || !(PL_PORTED(v->model))) {
        zeros(out, n);
        return 0;
    }
    dst = rate == 96000 ? v->buf48 : out;
    render_engine(v, f, trig, dst);
    if (rate == 96000) {
        hb_up2(&v->hb, v->buf48, out, PLAITS_BLOCK);
    }
    return 1;
}
#ifdef PL_WRAP_KERNEL
#undef plaits_render
#endif

/* The state of the 16 tracks' voices (the even voice of each track renders; the odd one copies it). On the DSP it is
 * reserved memory (`.PKBSS plaits_voice_mem.` in plaits_entry.s: not loaded, so not zeros in the boot stream, and
 * not zero at load): plaits_track zeroes a voice's non-engine fields (armed, model, the DC blocker, the limiter peaks,
 * the interpolator) at the voice's first block, and the engine union is initialised by note_on before any engine reads it
 * (armed 0 -> init). On the host it is an ordinary zeroed static. */
#ifdef __SELACHE__
extern PlaitsVoice plaits_voice_mem[16];
#else
static PlaitsVoice plaits_voice_mem[16];
#endif
typedef char pl_voice_mem_size_check[(sizeof(PlaitsVoice) * 16 == 273984) ? 1 : -1]; /* the .PKBSS size in plaits_entry.s */
static int plaits_voice_ready[16]; /* zero at load (loaded data, 64 bytes) */

/* The MODEL knob's order (owner, 2026-10-22) against the engine numbers (which stay as they were, so the digests, tests and
 * vectors are keyed as before): knob 0..20 are the engines 0..20, then knob 21 DX7-4 (engine 24), 22 TERRAIN (21),
 * 23 STRING MACHINE (22), 24 SQUARE (23). The names (src/cf/model_fmt.s) and the randomizer's list (src/cf/randomize.s,
 * the knob positions 0..24) follow the knob order; tests/patchkit/test_plaits_model_order.py checks the three together.
 * Out-of-range knob values are left alone (no engine renders them). */
int plaits_knob_engine(int knob)
{
    if (knob == 21) {
        return PLAITS_MODEL_DX7_4;
    }
    if (knob >= 22 && knob <= 24) {
        return knob - 1;
    }
    return knob;
}

int plaits_track(int track, const PlaitsFrame *f, int trig, float *out, int n, int rate)
{
    PlaitsVoice *v = &plaits_voice_mem[track & 15];
    /* The same map on the float word (no int <-> float conversions: an instruction shape the encoding gate has not seen).
     * The entry rebuilds _pl_frame from the rows at every block, so the model word can be rewritten in place. */
    float m = f->model;
    if (m >= 5376.0f && m < 5632.0f) { /* knob 21 (0x1500) -> DX7-4, engine 24 (0x1800) */
        ((PlaitsFrame *)f)->model = m + 768.0f;
    } else if (m >= 5632.0f && m < 6400.0f) { /* knobs 22 .. 24 -> engines 21 .. 23 */
        ((PlaitsFrame *)f)->model = m - 256.0f;
    }
    if (plaits_voice_ready[track & 15] == 0) {
        int *p = (int *)(void *)&v->out;
        int k;
        for (k = 0; k < (int)((sizeof(PlaitsVoice) - sizeof(PlaitsEngine)) >> 2); ++k) {
            p[k] = 0;
        }
        plaits_voice_ready[track & 15] = 1;
    }
    v->track = track & 15;
    return plaits_render(v, f, trig, out, n, rate);
}
