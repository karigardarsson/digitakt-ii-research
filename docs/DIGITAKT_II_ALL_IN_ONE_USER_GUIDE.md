# Digitakt II All-in-One v0.3.0 — verified controls

[Download v0.3.0](https://github.com/karigardarsson/digitakt-ii-research/releases/tag/v0.3.0-beta) · [Installation](INSTALLATION.md) · [Stock recovery](RECOVERY.md)

For **Digitakt II OS 1.17**, generated firmware SHA-256
`426dd1be5be3d356b5fa26900c729baf555cc16393611c4e0544ebd2d95eec76`.

These instructions are **SOURCE-PROVEN**, cross-checked against the published corresponding source and existing test reports. They are not a claim that every operation was hardware-tested. Earlier Plaits25/DX7 operation and earlier Musical WAVE/Zoom builds have user hardware evidence; this exact label-fix/credits release remains **hardware-untested**. The recurring Plaits trigger click remains unresolved. Back up important work; DSP capacity varies by engine and track count.

## Plaits

1. Select an audio track. Press **FUNC + SRC** to open machine selection, select **PLAITS**, and confirm with **YES**. Do not count arrow presses: the current menu position can differ.
2. Open its SRC page. Play note trigs, or run a pattern containing note trigs.
3. Turn **B — MODEL** to choose a model. **Trigger a new note after a MODEL change**: engine selection is latched at note-on.

| Encoder | Control | Meaning |
|---|---|---|
| A | TUNE | Stock tuning control; track notes also determine pitch. |
| B | MODEL | 25 positions, **0–24**; names are abbreviated inside the graphical box. |
| C | HARM | 0–127, model-dependent; DX7 patch selection on DX7 models. |
| D | — | Unused. There is no CHAR control in this release. |
| E | TIMB | 0–127, model-dependent timbre control. |
| F | MORPH | 0–127, model-dependent morph control. |
| G | MIX | 0–127, crossfade from the engine's OUT to AUX; **not a universal dry/wet or volume control**. |
| H | LEV | Stock level control. |

MODEL order:

- **0–7:** VA, ASYM-TRI, 2FM, FORMANT, ADDITIVE, WAVETABLE, CHORD, SPEECH.
- **8–15:** SWARM, NOISE, DUST, STRING, RINGS, KICK, SNARE, HIHAT.
- **16–24:** VCF-VA, PHASE-DIST, DX7-1, DX7-2, DX7-3, DX7-4, TERRAIN, STRING MACH, SQUARE.

For DX7, select **MODEL 18–21** for banks **DX7-1–DX7-4**, then use **C — HARM** to select among that bank's 32 patches. HARM's number remains 0–127: it is **not** a patch number from 1–32. Follow the patch name in the lower label; the upper box shows the numeric value. TIMB controls DX7 brightness and MORPH scales envelope times. DX7 note-off/release support is implemented. MIX does not provide a separate DX7 patch-selection function; the engine supplies the same OUT/AUX signal.

The normal parameter/p-lock storage path is retained. MODEL changes, including model locks, select the engine on a note-on. Do not interpret this as proof of exhaustive physical project save/reload or every lock/modulation combination. **Plaits-parameter availability in the stock LFO/MOD destination menu is UNVERIFIED for this guide**; no instruction here depends on choosing Plaits TIMBRE as an LFO destination.

## Musical WAVE / Déjà Vu

Use an audio track's LFO settings. The added WAVE choices exist on all three LFOs. Encoder layout remains stock:

**A SPD · B MULT · C FADE · D DEST · E WAVE · F SPH · G MODE · H DEP**

SPD/MULT govern the LFO's rate. FADE and DEP retain stock fade/depth processing. A loop step is an advance of the DV value sequence with LFO phase: it is **not automatically one sequencer step or one note trigger**. This guide does not prescribe a button combination for cycling LFO pages; use the stock page selection for the LFO shown on screen.

Stock WAVE **0–6** remain **TRI, SINE, SQR, SAW, EXPO, RAMP, RAND**, with their original behavior.

| WAVE | Display | Added behavior |
|---|---|---|
| 7 | DV-F | Free-running stored random-value loop. |
| 8 | DV-T | Same loop mechanism, but a track trigger restarts its read position; it does not erase the stored values. |
| 9 | PMAJ | Major pentatonic TUNE quantization: **0, 2, 4, 7, 9**. |
| 10 | PMIN | Minor pentatonic: **0, 3, 5, 7, 10**. |
| 11 | HIRA | Hirajoshi: **0, 2, 3, 7, 8**. |
| 12 | WTON | Whole-tone: **0, 2, 4, 6, 8, 10**. |
| 13 | DORI | Dorian: **0, 2, 3, 5, 7, 9, 10**. |
| 14 | LYDI | Lydian: **0, 2, 4, 6, 7, 9, 11**. |
| 15 | OCTA | Octatonic: **0, 1, 3, 4, 6, 7, 9, 10**. |

The numbers are **semitone-offset classes relative to TUNE 0**, repeated across octaves—not detected sample pitches or absolute note names. Current WAVE 9 displays **PMAJ**, not the older name DV-Q.

### MODE means loop length on added waves

The UI still shows the stock MODE words. For **WAVE 7–15 only**, their positions select the following numbers of stored values:

| MODE displayed | Stored values |
|---|---:|
| FREE | 2 |
| TRIG | 4 |
| HOLD | 8 |
| ONE | 16 |
| HALF | 32 |

Do **not** read these words as stock trigger modes while an added wave is selected. HOLD is not a freeze command; ONE is not a one-shot loop command. **WAVE 8 / DV-T**, not MODE's TRIG label, selects DV's trigger-reset behavior. Stock WAVE 0–6 retain their stock MODE meanings.

### SPH means mutation on added waves

SPH range is **0–127**:

- **0:** refresh the visited value at every loop-step advance.
- **1–63:** progressively reduce the probability of replacing that value.
- **64:** retain values and their order. **The playhead keeps advancing**; this is not a held output voltage or frozen note.
- **65–126:** progressively increase the probability of swapping retained values within the active loop.
- **127:** attempt a swap at each advance, without generating replacement values. A swap can choose the same position, so not every advance must sound different.

These rules also govern the random loop underneath PMAJ–OCTA. MODE changes its length; SPH changes its mutation. Neither selects a scale or a root. Changing depth, rate or scale can change the heard result even with SPH at 64.

### Using a scale wave with SAMPLE TUNE

1. Use a **SAMPLE** track with a loaded sample and note trigs.
2. On one LFO, select **DEST = SAMPLE TUNE** and **WAVE = PMAJ** (or another scale above). Avoid additional LFOs targeting TUNE for this initial setup.
3. Start with a low depth; increase it to obtain audible pitch steps. Set SPD/MULT for the desired movement rate.
4. For a repeating value order, choose a MODE length and set **SPH = 64**. Move SPH below 64 for new values, or above 64 to reorder retained values.

The quantizer acts on the **effective TUNE result after the stock depth/fade calculation**, not on the raw random values. It quantizes a tuning offset, not the sample's fundamental. The sample's original tuning and the played note still matter. It is therefore **not a guarantee of absolute C-major notes**.

Changing the existing TUNE control biases the input to the fixed scale map; it is **not a dedicated root/transposition selector**. No verified root-selection recipe is provided. To leave the scale-quantizing path, choose **DV-F, DV-T or a stock wave** rather than treating MODE HOLD or a depth setting as a dedicated bypass.

For destinations other than the tuning slot, PMAJ–OCTA use the DV-F generator without this pitch quantization. They do not turn arbitrary destinations into scales. Quantization of **Plaits pitch, MIDI notes or other musical-pitch paths is UNVERIFIED** here; no recipe is supplied for those uses. With multiple TUNE modulators, do not assume a global final-scale guarantee independent of processing order.

## One-Shot Zoom

On an **ONESHOT SRC waveform view with a loaded sample**:

- **Press encoder C** to cycle **1× → 2× → 3.75× → 1×**. No FUNC modifier is required.
- While zoomed, **turn C** to pan. Panning is bounded by the available detail.

This changes the display, not playback or sample data. It is not a Plaits waveform viewer. If the required detail/valid waveform context is unavailable, the implementation falls back to stock drawing/controls. Do not assume that the zoom viewport is project-persistent.

## Limits of the evidence

**Existing recorded tests:** stock-wave/DV differential regressions, all seven scale maps, a non-pitch DV-F-equivalence fixture, native formatter paths, ONESHOT zoom and native Sound-object round trips. These are bounded host/synthetic checks, not new hardware tests.

**UNVERIFIED:** exact-release hardware acceptance; exhaustive Pattern/Project save/reload/power-cycle behavior; preservation of the exact DV random motif across reload/reboot; arbitrary multi-LFO pitch combinations; Plaits/MIDI quantization and Plaits LFO-destination availability. No procedure above depends on these claims. No click fix is implied.

See [the source audit](findings/all-in-one-user-guide-source-audit.md) for exact files, addresses and evidence boundaries. Publishing this guide does not change the firmware. See the release notes for Windows-builder presentation revisions.
