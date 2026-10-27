# Plaits

PLAITS brings Mutable Instruments Plaits to the Digitakt II as a machine. This version has thirteen models, numbered in
Plaits' own order (0 VA, 1 ASYM-TRI, 2 2FM, 3 FORMANT, 4 ADDITIVE, 5 WAVETABLE, 6 CHORD, 7 SPEECH, 8 SWARM, 9 NOISE,
10 DUST, 11 STRING, 12 RINGS, 13 KICK, 14 SNARE, 15 HIHAT, 16 VCF-VA, 17 PHASE-DIST, 18-20 DX7-1 to DX7-3, 21 TERRAIN,
22 STRING MACHINE, 23 SQUARE, 24 DX7-4; owner, 2026-10-17). These are the engine numbers (the sources, tests and
digests use them). **The MODEL knob's order differs after DX7-3** (owner, 2026-10-22): knob 0..20 = engines 0..20, then
knob 21 DX7-4 (engine 24), 22 TERRAIN (21), 23 STRING MACHINE (22), 24 SQUARE (23). The DSP entry maps knob to engine
(`plaits_knob_engine`, plaits.c), the names and the randomizer's list are in knob order; test_plaits_model_order.py.
A project saved with an earlier image changes meaning for MODEL values 21 to 24:
- 0 VA (virtual analog): a variable square plus a variable saw on OUT, a "monster sync" pair on AUX.
- 1 ASYM-TRI (waveshaping): an asymmetric triangle through a waveshaper and a wavefolder on OUT; on AUX a sine that
  TIMBRE turns into a second wavefolder curve.
- 2 2FM (two-operator FM): a carrier and a modulator with feedback on OUT; on AUX a sub-oscillator an octave down.
- 3 FORMANT (granular formant oscillator): two formants made of windowed sine grains on OUT; on AUX windowed sines
  that imitate filtered waveforms (Braids' Z models).
- 4 ADDITIVE (harmonic oscillator): 24 harmonics shaped by a spectral bump on OUT; on AUX only the harmonics of a
  Hammond organ's drawbars.
- 5 WAVETABLE: 4 banks of 8 x 8 waves scanned in three dimensions on OUT; on AUX the same quantised to 5 bits.
- 6 CHORD: a four-note chord from Plaits' chord bank on five voices, divide-down organ/string registrations turning
  into wavetable voices as MORPH rises; on AUX the chord's root.
- 7 SPEECH: speech synthesis. HARMONICS crossfades a naive formant synth into a SAM-style synth into LPC10 vowels,
  then selects one of five LPC10 word banks (colours, numbers, letters, the NATO alphabet, synthesizer words); each
  note-on speaks the word MORPH selects. On AUX the excitation (the "vocal cords").
- 9 NOISE: clocked noise through a multimode filter and two band-passes (Plaits' noise engine; runs on between notes). TIMBRE
  the clock frequency (sample and hold up to white noise), MORPH the filter resonance, HARMONICS the second band-pass's
  offset and OUT's low-pass to high-pass mode. A note-on syncs the two clocks. On AUX the two band-passes.
- 16 VCF-VA: a variable-shape oscillator (saw to square, pulse width) plus a sub oscillator through a two-stage state variable
  filter with soft clipping (Plaits' engine2 VCF engine; runs on between notes). HARMONICS the resonance and the drive
  (and the second filter stage), TIMBRE the cutoff relative to the note, MORPH the shape (saw, square, pulse width) and the
  sub level. OUT the low pass, AUX the high pass.
- 17 PHASE-DIST: phase distortion and phase modulation (Plaits' engine2 phase-distortion engine; runs on between notes). Two
  oscillators with an asymmetric triangle as the modulator, one synced to the note, one free, as the phase of a sine.
  HARMONICS the modulator's frequency ratio (quantized), TIMBRE the modulation amount, MORPH the asymmetry. OUT the synced pair,
  on AUX the free-running one.
- 12 RINGS: modal synthesis (Plaits' modal engine, from Rings): each note-on strikes a 24-mode resonator. HARMONICS
  the structure (inharmonicity), TIMBRE the brightness, MORPH the damping (ring length); velocity the strike (accent).
  On AUX the exciter.
- 13 KICK, 14 SNARE, 15 HIHAT (the three drum engines, each strikes at every note-on; velocity is the accent). KICK:
  an analog 808-style bass drum on OUT, a synthetic one on AUX; HARMONICS the attack FM, self FM and drive, TIMBRE the
  tone, MORPH the decay. SNARE: an analog 808-style snare on OUT, a synthetic 909-style one on AUX; HARMONICS the snappy
  amount, TIMBRE the tone (the synthetic one's FM), MORPH the decay. HIHAT: six square oscillators through a swing VCA
  on OUT, a ring-modulated pair-of-oscillators hat on AUX; HARMONICS the noisiness, TIMBRE the tone, MORPH the decay.
- 21 TERRAIN: a circular path through one of eight 2-D terrains (HARMONICS crossfades them; the last three are the
  wavetable data read as terrains), TIMBRE the path's radius, MORPH its centre. On AUX a sine phase-modulated by the
  path.
- 8 SWARM: Plaits' swarm engine: eight grains, each a sawtooth (OUT) and a sine (AUX) at its own random pitch around the note under its own
  random-length envelope. HARMONICS spreads the grains' pitches (0: all at the note; up: detuned, then scattered over wide intervals), TIMBRE sets
  the grain rate (slow to a fast shimmer), MORPH the grain size (left: long overlapping grains gliding between random pitches, a swarm of
  glissandi; right: short separate grains, a cloud). MIX moves from the saws to the sines. It runs on between notes in Plaits' continuous mode
  (its trigger unpatched): a note-on does not restart the grains, the swarm keeps a steady rate and the Digitakt's amp envelope shapes the note.
  This is a deliberate exception to the trigger convention below (owner decision, 2026-10-22); the burst mode (a note-on restarts the grains) is
  ported and tested but never reached. Its random state is per track.
- 18, 19, 20 and 24 DX7-1 to DX7-4: Plaits' six-operator FM engine (a DX7 voice engine), one patch of a bank of 32 at a time: DX7-1 to DX7-3 are Plaits' three
  built-in banks, DX7-4 is the owner's bank (`data/tyler-favorites.syx`, a DX7 32-voice dump: `*PPG*Vol.1` first; the build checks its header, length and
  checksum). HARMONICS picks the patch (32 steps, a fixed function of the knob value: no hysteresis, a deliberate deviation from upstream, PORTING drift 28; **the knob-turn header shows the patch name**, `Harmonics=BASS    1`, with MODEL on a DX7 model; every other screen shows the number, finding 02 "PLAITS DX7 patch names"), TIMBRE is the brightness (the modulators'
  level), MORPH scales the patch's envelope times; MIX is idle (OUT and AUX are the same). Each note-on starts a voice (two alternate, so a tail rings under
  the next note) with the patch's own operator and pitch envelopes and LFO. **The gate follows the Digitakt's note-off** (a documented exception to the
  note-on-only convention below, as SWARM's): the patch holds its sustain until the key is released or the step's note length ends, then runs its release.
  The DSP learns of the note-off from the voice's release flag (core/dsp-inline `_di_released`: byte `0x24f0ec + v`, set by the stock release path); the wrapper
  latches that pulse until the next note-on. A new patch's first block is silent (upstream's CPU guard). The banks are tables of 16 KB; a voice's state is
  2,740 bytes.
- 11 STRING: Plaits' string engine: a Karplus-Strong string, three strings used in turn, each note-on striking the next one with a burst of noise (so
  notes ring over each other). HARMONICS is the string's non-linearity (left: a curved bridge, a bright buzzing at the start; a dead band around the
  middle: a plain string; right: dispersion, a stiff metallic string with a little noise), TIMBRE the brightness of the strike and the string, MORPH the
  damping (short at the left, a string that never decays at the far right). A strike engine: velocity is the accent (harder: brighter and longer).
  MIX moves from the string (OUT) to the excitation burst (AUX). The sustained mode of an unpatched trigger is not reachable on the Digitakt and is
  not ported. Its random state is per track. Each voice holds 15,360 bytes of delay lines (inside the union DUST sets).
- 10 DUST: Plaits' particle engine: six random impulse trains (TIMBRE the density, HARMONICS the spread of each click's pitch around the note),
  each through a band-pass filter, a low-pass at the note and a diffuser (reverb-like all-pass chain, an 8192 x 16-bit delay memory) whose amount
  MORPH sets below 0.5; above 0.5 MORPH is the resonance. Not pitched; it runs on between notes, a note-on resyncs the first click. OUT is the filtered
  particles, AUX the raw clicks (MIX moves between them). Its random state is per track.
- 22 STRING MACHINE: Plaits' string-machine engine: a four-note chord (HARMONICS picks one of 11) on divide-down string/organ voices,
  MORPH the registration (saw through to square, 11 steps), TIMBRE the low-pass cutoff and, away from 0.5 in either direction, the amount
  of the stereo ensemble (chorus) and its depth. OUT and AUX are the two channels after the ensemble; MIX moves between them. A sustained sound: it
  ignores the trigger and the velocity, the Digitakt's envelope shapes the note.
- 23 SQUARE: Plaits' chiptune engine with its arpeggiator, always clocked: every note-on is Plaits' rising edge and steps the
  arpeggio one note (Plaits' unclocked five-voice chord mode, its internal envelope and `already_enveloped` are not ported: the
  Digitakt's envelope does that). HARMONICS picks the chord (11), TIMBRE the pattern (up, down, up-down, random) and range (1, 2 or 4
  octaves) in 12 steps, MORPH the waveform (a sync'd square, from a narrow pulse to a hard-synced sweep). OUT the arpeggiated
  square voice, AUX an NES-style 5-bit triangle bass one octave below the note (and the arpeggio's octave). HARMONICS 0 with TIMBRE 0
  plays the note itself on every trig. One note-on, one step: a held note does not arpeggiate by itself; use the sequencer.
It plays at the track's note and TUNE, and goes through the track's amp envelope, filter, sends and FX like a sample.
Pick it with FUNC+SRC (the 8th entry).

- Devices and firmware: Digitakt II 1.17.
- SRC page: A TUNE, B MODEL (0 VA, 1 ASYM-TRI, 2 2FM, 3 FORMANT, 4 ADDITIVE, 5 WAVETABLE, 6 CHORD, 7 SPEECH, 9 NOISE, 12 RINGS, 13 KICK, 14 SNARE, 15 HIHAT, 16 VCF-VA, 17 PHASE-DIST, 21
  TERRAIN, 22 STRING MACHINE, 23 SQUARE; the other values up to 23 are the later models and play silence), C HARMONICS, E TIMBRE, F MORPH (per model as in the Plaits manual:
  VA detune / square width and sync / saw shape; ASYM-TRI waveshaper curve / wavefolder amount / asymmetry;
  2FM frequency ratio / modulation index / feedback; FORMANT formant ratio / formant frequency / formant width and
  shape; ADDITIVE number of spectral bumps / most prominent harmonic / bump shape; WAVETABLE bank / row / column;
  CHORD chord / inversion and transposition / waveform; SPEECH synthesis method and word bank / formant shift
  (species) / vowel or word), G MIX (OUT to AUX), H LEV. Knob D
  is unused. Every knob is a normal parameter row: p-lockable and an LFO destination.
- MODEL shows the model's name, not its number, in the header while the knob is touched or turned (`Model=ASYM-TRI`).
  The names, by value: 0 VA, 1 ASYM-TRI, 2 2FM, 3 FORMANT, 4 ADDITIVE, 5 WAVETABLE, 6 CHORD, 7 SPEECH, 8 SWARM,
  9 NOISE, 10 DUST, 11 STRING, 12 RINGS, 13 KICK, 14 SNARE, 15 HIHAT, 16 VCF-VA, 17 PHASE-DIST, 18 DX7-1, 19 DX7-2,
  20 DX7-3, 21 DX7-4, 22 TERRAIN, 23 STRING MACH, 24 SQUARE (knob positions; the engine numbers are above). They come from a small ColdFire function of this module
  (src/cf/model_fmt.s, `plaits_model_fmt`), which core/params' `formatter` field puts in MODEL's parameter row. The knob
  slot still draws LEV's fader (MODEL's template); core/params scales its value to the row's range, so the bar is full at the
  highest model (src/cf/dt2/barscale.s). Tests: tests/patchkit/test_plaits_model_names.py and the harness
  test tests/model_names.toml.
- The engine runs on from note to note, as in Plaits (Task 9b): a note-on latches MODEL, and initialises the engine
  only at the voice's first note-on since boot and when MODEL changed (a knob or p-lock change takes effect at the
  next note-on). Every other note-on leaves the engine, the DC blocker and the interpolator running; the Digitakt's
  amp envelope shapes the note. The engines share one state per voice (16,572 bytes, the size of DUST's (SPEECH 372; RINGS 304, HIHAT 324, SNARE 212, KICK 152, NOISE 112, VCF-VA 116, SQUARE 124, PHASE-DIST 80, TERRAIN 24, STRING MACHINE 4,308: its ensemble's 1024-float delay buffer and sample count; DUST 16,572: the diffuser's 8192 x 16-bit delay memory packed two to a word and the kernel's sample count; PlaitsVoice is 17,124 bytes, 16 voices 273,984), so a model
  change starts the new engine from Plaits' Init + Reset rather than resuming its old state as Plaits would. The DSP
  does not see a track's machine type change: a track switched away and back resumes its engine where it stopped
  (the engine is paused while the voice is not rendered). Proof: tests/plaits/test_continuous_host.py (bit exact
  against upstream rendered continuously, models 0 to 17, 21, 22 and 23).
- A note-on reaches the engine as Plaits' trigger, as Plaits' Voice builds it with its TRIG input patched:
  RISING_EDGE | HIGH in the note-on's first sub-block, then HIGH until the next note-on (the voice routine sees no
  note end; no ported engine reads the gate's length). SPEECH reads it: a note-on speaks the selected word from its
  start, or puts a consonant before an LPC vowel; RINGS strikes. Velocity is Plaits' accent: velocity / 127, latched
  at the note-on (the default velocity 100 gives 0.787, next to Plaits' 0.8 with LEVEL unpatched); SPEECH (word gain)
  and RINGS (strike level, brightness, ring) read it, the other models ignore it. SPEECH's intonation and speed (Plaits' FM and MORPH
  attenuverters with TRIG patched) are fixed at 0, the attenuverters' centre.
- The first note of a voice (and the first note after a model change) still starts from Plaits' power-up state, so
  WAVETABLE (and CHORD at a high MORPH and a low note) plays Plaits' cold-start transient once: about 25 times the
  steady level for a few samples on WAVETABLE (tests/plaits/test_wavetable_chord_host.py `test_note_on_transients`).
  Later notes of the same model have none. The digests (tests/plaits/vectors) were re-recorded at Task 9b; the first
  note after init renders as on H2 (model 0), H3 (1), H3b (2 to 4) and H3c (5, 6); SPEECH's were recorded at H3e.
- The engines are the Plaits C++ source translated to C (MIT, Emilie Gillet), rendered at exactly 48 kHz (Plaits'
  own pitch reference of 47,872.34 Hz is replaced by 48,000 Hz, so notes are in tune), in two sub-blocks of 16
  samples per Digitakt block, with a 5 Hz DC blocker. Plaits' internal envelope, low-pass gate and modulation
  amounts are left out on purpose: the Digitakt's envelopes and LFOs do that work.
- The DSP code comes from one of two modules, which pull this one in: machines/plaits-48k (the default: 48 kHz
  straight into the voice output) or machines/plaits-96k (interpolated to 96 kHz, then the stock decimator).
  Selected alone, this module gives the track the PLAITS name and rows but plays it as MANUAL SLICE.
- Deviation from upstream, models 13 to 15: SNARE's two level constants use a Newton square root (lib/drums.h `pl_sqrt`,
  within 1 ulp of the hardware square root upstream uses) because the DSP has no square root; the oracle applies the same
  function (tests/support/plaits_oracle.py `sqrt_override`). Each drum engine keeps its own noise state (0x21 at init).
- Deviation from upstream, model 9: NOISE's per-sample gain 1 / Sqrt(x) is a division-free Newton reciprocal square root
  (noise.c `pl_rsqrt`, within about 2.2 ulp of the true value, measured 2.18; upstream's own `1.0f / sqrtf` is itself 1.5 ulp off) because the DSP has no square root; the oracle applies the same function
  (tests/support/plaits_oracle.py `rsqrt_noise_engine`). The engine's two clocked noises draw from one per-engine
  generator (stmlib's global Random: 0x21 + track * 0x9e3779b1, so track 0 is the oracle's 0x21 and two NOISE tracks differ; noise 0's block first, then noise 1's, as
  upstream), so a single voice reproduces upstream's sequence. The TRIGGER_UNPATCHED lowest clock note is not ported.
- Known limits: twenty-one models (0 to 17, 21, 22, 23); a triggered PLAITS voice keeps rendering after its note (the amp envelope silences it),
  so each PLAITS track that has played costs DSP time every block. The rule: **never more than 1 track triggered as
  PLAITS since boot** with the current build (any model; it was 2 on the H2 and H3 images, which had VA and
  ASYM-TRI only), and 1 with the 96 kHz build. A track stops costing DSP only after it has been triggered under
  another machine (the type reaches the DSP only on a trig). Worst case per track per block on the concrete core at
  48 kHz (with the 96 kHz build): VA 76,311 (99,848), ASYM-TRI 42,313 (65,850), 2FM 57,442 (80,979), FORMANT 80,917
  (104,454), ADDITIVE 88,288 (111,825), WAVETABLE 41,168 (64,705), CHORD about 55,000 (about 79,000), SPEECH 34,083
  (57,620), RINGS 124,173 (147,710), TERRAIN 71,204 (94,741) instructions;
  the block is 666,667 cycles at 1 GHz. RINGS is the heaviest single track at the high bound (73.1 % while it
  rings; TERRAIN 65.0 %, WAVETABLE 67.4 %, ADDITIVE 67.1 %), but pairs of it (76.7 %) and of CHORD (70.5 %) fit; ADDITIVE sets
  the limit: two ADDITIVE tracks measure 86.6 %, over the 85 % budget of
  tools/patchkit/pk_dsp_budget.py (docs/findings/06-sharc-engine-and-startup.md, "PLAITS: measured DSP load per
  block" and the model sections after it). 16 tracks do not fit yet.
- Plaits' output stage (Task 10b): each model's OUT and AUX gain from Plaits (VA 0.8, ... SPEECH and RINGS OUT -0.7
  and -1: through Plaits' limiter), then the clip of Plaits' 16-bit DAC step (full scale), then the mix, the DC
  blocker and the output gain 0.601 / 0.8, so default VA keeps the level calibrated against a stock sample and every
  other model keeps Plaits' own balance against it (per-model levels in the Task 10b report).
- TERRAIN after big parameter jumps: its path leaves the unit square, where Plaits' code reads outside its tables
  (undefined behaviour, so what a Plaits module plays there depends on its build). The port guards those reads (the
  sine phase wrapped, the wave coordinates kept inside the table: engines/terrain.c); everywhere else it is bit exact
  with upstream.
- Hardware test (the owner's H1/H2 decision tree, the image hashes and patch commands): HARDWARE-TEST.md.
- A project saved with PLAITS loads on a stock 1.17 without a fault, but that track comes back as ONESHOT (type 0):
  the stock loader keeps only the stock machine types.
- Tests: tests/plaits/test_va_host.py, test_ws_host.py, test_fm_grain_additive_host.py,
  test_wavetable_chord_host.py and test_speech_host.py (host, bit exact against
  upstream Plaits), tests/patchkit/test_plaits_module.py (compiled code on the concrete core, the images),
  test_plaits_model0_h2.py and test_plaits_model_digests.py (each model unchanged since it was first enabled),
  tools/patchkit/pk_proof_plaits.py (emulator). `mise run verify`
  runs tests/plaits (suite "plaits") whenever this module, tests/plaits or tests/support/plaits_* change.
- The bit-exact oracle builds upstream Plaits from ../eurorack (the Mutable Instruments repository next to this one)
  and its stmlib submodule at commit e3bd7c9. Provide stmlib in one of two ways:
  `git -C ../eurorack submodule update --init stmlib` (offline from a local clone: `git -C ../eurorack submodule init
  stmlib`, `git -C ../eurorack config submodule.stmlib.url <clone>/stmlib`,
  `git -C ../eurorack -c protocol.file.allow=always submodule update stmlib`, then set the URL back), or
  `PLAITS_STMLIB=<directory holding a stmlib/ checkout at e3bd7c9>`. Without stmlib the oracle tests skip;
  `PLAITS_ORACLE_REQUIRED=1` turns that skip into a failure, and verify's python-tests step sets it (override with
  `PLAITS_ORACLE_REQUIRED=0`).

The gallery shows `summary` from module.toml and `gallery/icon.svg`. Keep this file free of firmware bytes and vendor
tool names.

## SRC page behaviour (owner requests, 2026-10-22)

- **SRC+YES (randomize page)** changes MODEL (over the 25 built models, uniformly), HARMONICS, TIMBRE and MORPH over their
  full range, and not TUNE, MIX or LEV. Why it did nothing before: the page randomizer calls ParameterSet::vfunc_7 per
  parameter code, which runs the randomizer functor of the code's value-class object; the LEV template of PLAITS' rows has none
  (finding 02, "SRC+YES randomizes a page"). The `randomize` field of core/params' rows (`randomize = 1` uniform,
  `randomize = "plaits_model_rand"` a function; src/cf/randomize.s lists the models) fixes it. Tests:
  tests/patchkit/test_plaits_randomize.py.
- **One SRC page**: a second SRC press no longer opens the redundant second page (core/machines `one_page = 1`, a reusable
  rule for any machine). Tests: tests/patchkit/test_plaits_ui.py.
