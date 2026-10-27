# PLAITS on the unit: H1 (the hook), H2 (the machine), H3 (a second model), H3b (models 2 to 4), H3c (models 5 and 6), H3d (engines run on between notes), H3e (model 7, SPEECH), H3f (output stage, velocity, RINGS and TERRAIN), H3g (the drums, models 13 to 15), H3h (NOISE, model 9) H3i (PHASE-DIST, model 17), H3j (VCF-VA, model 16), H3k (SQUARE, model 23) H3l (STRING MACHINE, model 22) H3m (DUST, model 10) H3n (SWARM, model 8) H3o (STRING, model 11) and H3p (DX7, models 18 to 20 and 24)

One decision tree for the owner's hardware round on a Digitakt II with OS 1.17. H1 tests core/dsp-inline with a
plain sine (test/inline-tone); H2 tests machines/plaits (the VA model) on the same hook. The images are firmware
derivatives: they live under out/patch/ and are never committed. Results go in the design's hardware table
(docs/design/2026-10-17-plaits-machine-design.md) and, for an image that passed, its executed shapes or words in
tools/patchkit/encoding_proven.toml.

Recovery from a stall (any image): hold FUNC at power-on, press TRIG 4, then send the stock
`firmware/Digitakt_II_OS1.17.syx` with Elektron Transfer's legacy mode over MIDI DIN.

## The images

Every image below except the diagnostics passes the four owner checks: `elektron-firmware-tool -i` reports
`checksums : ok`; `uv run python -m tools.sharc_dev.sharc_bootcheck --syx X --stock firmware/Digitakt_II_OS1.17.syx`
reports 0 errors; `uv run python -m tools.patchkit.pk_encoding_gate X X.plan.json` reports 0 violations; sections 2, 4,
5 and 8 are byte-identical to stock.

| image | sha256 | built with |
|---|---|---|
| `out/patch/h1b-tone-48k-1.17.syx` | `6171da820712e3b1c66c014dd102c2cc7ee3c038d9c50fa81e540d63c283e8e9` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules test/inline-tone-48k -o out/patch/h1b-tone-48k-1.17.syx` |
| `out/patch/h1a-tone-96k-1.17.syx` | `bc6a3314170f013b5974cf35b8f001e76adcb000f6c843ae961648af2b8fb139` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules test/inline-tone-96k -o out/patch/h1a-tone-96k-1.17.syx` |
| `out/patch/plaits-va-48k-1.17.syx` | `507434164c90e42f26c3111d9b5a366b8b2b36f6d7d100c4e519b80d5c3f7492` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules machines/plaits-48k -o out/patch/plaits-va-48k-1.17.syx` |
| `out/patch/plaits-va-96k-1.17.syx` | `f37e87a0025ab07895c17275c5e0ee531e04125de34feafa0866781cc761c83f` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules machines/plaits-96k -o out/patch/plaits-va-96k-1.17.syx` |
| `out/patch/plaits-2models-48k-1.17.syx` (H3) | `68fee193f40a611a58613409b3e9c4a81fa242fcf3b09355fa7f073d75d236e4` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules machines/plaits-48k -o out/patch/plaits-2models-48k-1.17.syx` (at 3608a92) |
| `out/patch/plaits-5models-48k-1.17.syx` (H3b) | `e768eb4823f708c976242805f90615586ca0ec7e9c247e7f203f8b1506fe70ec` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules machines/plaits-48k -o out/patch/plaits-5models-48k-1.17.syx` |
| `out/patch/plaits-5models-names-48k-1.17.syx` (H3b with the MODEL names; verified by the owner, PLAY included, with a new project; built by the coordinator, its owner checks are not repeated here) | `c3b7596bc379113124554105c0aec89d47c96a4596bbeea75087469171aec397` | the same command at 119d75d |
| `out/patch/plaits-7models-48k-1.17.syx` (H3c) | `09ac59c0c1fb47460e39cd4639886f2fd0a8bbd733e71d318520f0637627b8a3` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules machines/plaits-48k -o out/patch/plaits-7models-48k-1.17.syx` (at 6bf06f1) |
| `out/patch/plaits-7models-names-splash-48k-1.17.syx` (H3d: engines run on between notes, MODEL names, boot splash) | `8032d08653293d7fa322c4e7f68b20752342aee3542e3e182a50038324c2754d` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-7models-names-splash-48k-1.17.syx` (at ed36e25) |
| `out/patch/plaits-8models-names-splash-48k-1.17.syx` (H3e: H3d plus model 7, SPEECH) | `8701a9bf5c73df271a20f4b0540230044903abdc09fa538ccec202e4a61d0e24` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-8models-names-splash-48k-1.17.syx` (at caba134) |
| `out/patch/plaits-10models-names-splash-48k-1.17.syx` (H3f: Plaits' output stage, velocity, models 12 RINGS and 21 TERRAIN) | `a16f9ae2e0ff56ec9b219b42001bb3bea4f5b0b0dc5bbf1cc125c837d7555f45` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-10models-names-splash-48k-1.17.syx` (at ff6303a) |
| `out/patch/plaits-13models-names-splash-48k-1.17.syx` (H3g: H3f plus models 13 KICK, 14 SNARE and 15 HIHAT) | `6d5644cb761b2faed173d3d711622748aad889ce04500156d3b08645cdab7aa2` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-13models-names-splash-48k-1.17.syx` (at 07f4466; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed) |
| `out/patch/plaits-14models-names-splash-48k-1.17.syx` (H3h: H3g plus model 9 NOISE) | `c1ce25156f128531b43db4b668c7e6b987edd3d2a6614e15e1810ce105b7deb4` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-14models-names-splash-48k-1.17.syx` (at 696fbfa; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed: the image is rebuilt from the committed objects) |
| `out/patch/plaits-15models-names-splash-48k-1.17.syx` (H3i: H3h plus model 17 PHASE-DIST) | `eb38ae989ec6a67ed728e26b39fb9fae96b80d78603db50d64fb91a4b215ffda` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-15models-names-splash-48k-1.17.syx` (at ffffe4f; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed: the image is rebuilt from the committed objects) |
| `out/patch/plaits-14models-names-splash-48k-1.17-rings-opt.syx` (H-opt1: H3h with the RINGS SIMD kernel) | `ceb8dec7e0f39ebbc2358ba552c1321ba68886e6b4132b19ecca03b784365b0d` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-14models-names-splash-48k-1.17-rings-opt.syx` (lane rings-opt, fix round 1) |
| `out/patch/plaits-14models-names-splash-48k-1.17-rings-sm.syx` (H-opt2: H3h with the RINGS kernel that makes no data transfer in SIMD mode) | `0adcca1714720d0e768f77e6e6618b4bca5de8db6ac872236e91568fae4d06ed` | same command, lane rings-opt after the H-opt1 failure (module sources list `lib/resonator_svf4_sm.s`) |
| `out/patch/plaits-15models-drumsfast-names-splash-48k-1.17.syx` (H-opt3: H3i with the drum kernels `lib/drums_sm.s`) | `030afaf362ee51f8d20975e09dc1249226dfa87f5fd0ee240d7af654905cfa31` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-15models-drumsfast-names-splash-48k-1.17.syx` (lane drums-opt at 7c73637; `RUSTUP_TOOLCHAIN=1.98.1-aarch64-apple-darwin`) |
| `out/patch/probe-samedag-store-14models-48k-1.17.syx` (probe, branch probe/simd-l2) | `5e4fae1d534c1cc5a1b40f0aad1ba82d70691a0deb3a52d0d667aa2b58410577` | H3h plus one `DM(I4, M6) = M4` in VA's render; `PL_PROBE_STORE 2` |
| `out/patch/probe-simd-l2-load-14models-48k-1.17.syx` / `-store-` (probes, branch probe/simd-l2) | `d0085564…` / `855474a8…` | H3h plus one SIMD load (store) on `_pl_stack` in VA's render; `PL_PROBE_STORE` in `src/dsp/probe_simd.s` |
| `out/patch/plaits-16models-names-splash-48k-1.17.syx` (H3j: H3i plus model 16 VCF-VA) | `d93c8bbb53d97786c24e0ae9ecb79daf73502aad6986b6704980a76ce46fd2ef` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-16models-names-splash-48k-1.17.syx` (at 024cda0; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed) |
| `out/patch/plaits-17models-fast2-names-splash-48k-1.17.syx` (H-opt4: H3k with the output-stage kernel and the CHORD kernels) | `c17059bb9163ef185c564562ddce264219c2efd7cbef64851d64d652e5d4e4af` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-17models-fast2-names-splash-48k-1.17.syx` (lane opt-o3 at e4826af; `RUSTUP_TOOLCHAIN=1.98.1-aarch64-apple-darwin`) |
| `out/patch/plaits-17models-names-splash-48k-1.17.syx` (H3k: H3j plus model 23 SQUARE) | `f49cc711086d2e6847ee2c07a30d4c601e8b915410d5659a5a7f7860fa095650` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-17models-names-splash-48k-1.17.syx` (at c5b4198; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed; needs the 1.98 Rust toolchain) |
| `out/patch/plaits-18models-names-splash-48k-1.17.syx` (H3l: H3k plus model 22 STRING MACHINE) | `1ab2ef0b4918f84216256cf2d0b5597a092cb37aab88462609a1e2a1b19a21b4` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-18models-names-splash-48k-1.17.syx` (at ed83575; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed; needs the 1.98 Rust toolchain) |
| `out/patch/plaits-19models-names-splash-48k-1.17.syx` (H3m: H3l plus model 10 DUST) | `3146da3d555702e9e5c772945d6b809285e13658a3280300094ff3add4f1d2cc` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-19models-names-splash-48k-1.17.syx` (lane dust; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed; needs the 1.98 Rust toolchain; section 7 is 656,244 bytes of the 1,048,575 cap, dsp.l2 613,108 of 922,756; the voices are reserved memory, not in the boot stream) |
| `out/patch/plaits-19models-fast4-names-splash-48k-1.17.syx` (H-opt6: H3m with the PHASE-DIST, ADDITIVE and VA kernels and every kernel assembled compressed) | `126c9716a283ee316cd83a5c4a18ce51328c0d64b4e51849bedb302cffcdfe39` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-19models-fast4-names-splash-48k-1.17.syx` (lane opt-o5 at d27ccad, objects from `pk_build build-all`; `RUSTUP_TOOLCHAIN=1.98.1-aarch64-apple-darwin`) |
| `out/patch/plaits-20models-names-splash-48k-1.17.syx` (H3n: H3m plus model 8 SWARM) | `bbc893b81a3d39b4659929d49d0e91287d33c782dbaf092887e6fb042d25bdf8` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-20models-names-splash-48k-1.17.syx` (lane swarm; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed; needs the 1.98 Rust toolchain; section 7 is 662,636 bytes of the 1,048,575 cap; SWARM's state is 484 bytes per voice, the voice size is unchanged) |
| `out/patch/plaits-21models-fast5-names-splash-48k-1.17.syx` (H-opt7: H3o (STRING) with the O6 kernels: FORMANT, NOISE, DUST, STRING MACHINE, SWARM, TERRAIN, 2FM, VCF-VA, WAVETABLE, SPEECH, ASYM-TRI, VA's mix loops; STRING MACHINE without its init clear; the dispatch tree) | `25d5e89256a90231bdf06518a4353d39cd650c0a906fdff680f373fff45af65f` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-21models-fast5-names-splash-48k-1.17.syx` (lane opt-o6 at 27bf26a, objects from `pk_build`; `RUSTUP_TOOLCHAIN=1.98.1-aarch64-apple-darwin`; section 7 is 706,556 bytes) |
| `out/patch/plaits-25models-dx7-names-splash-48k-1.17.syx` (H3p: H3o plus models 18, 19, 20 and 24, the DX7) | `b4bad7a7780a8a5fdc5e5fb67e871f737b9c2a5ba8eec400265af772ba514495` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-25models-dx7-names-splash-48k-1.17.syx` (lane dx7, merged with plaits/design at c8a39e34) |
| `out/patch/plaits-21models-names-splash-48k-1.17.syx` (H3o: H3n plus model 11 STRING; all 21 in-scope models) | `dce0e3c1acd67a6f2132058c6a53a0630f806eeed0db1c88bd53743c32c319bd` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-21models-names-splash-48k-1.17.syx` (lane string; run `mise run patch:build -- modules/machines/plaits-48k` first when sources changed; needs the 1.98 Rust toolchain; section 7 is 675,228 bytes of the 1,048,575 cap, dsp.l2 632,092 of 922,756; STRING's state is 15,700 bytes per voice, the voice size is unchanged) |
| `out/patch/plaits-25models-rand-names-splash-48k-1.17.syx` (H3q: H3p plus SRC+YES randomize, one SRC page, the MODEL bar over its range, and the MODEL knob order 21 DX7-4, 22 TERRAIN, 23 STRING MACHINE, 24 SQUARE) | `78d4fd39da8d47c30f93b850666ea50766a5430925819069bcf49e2a7f2c5cf6` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-25models-rand-names-splash-48k-1.17.syx` (lane rand, merged with plaits/design at 3ca75fe7; run `uv run python -m tools.patchkit.pk_build build-all` first when sources changed; needs the 1.98 Rust toolchain; the encoding gate passes with no new shape: the knob-order map is float arithmetic) |
| `out/patch/plaits-25models-dx7names-splash-48k-1.17.syx` (H3r: H3q plus DX7 patch names in the HARMONICS knob-turn header and the memoryless DX7 patch quantiser; lane names, merged with plaits/design a31639a; owner checks: checksums ok, sharc_bootcheck 0 errors, encoding gate passes (default), sections 2, 4, 5, 8 equal to stock; not yet on hardware) | `1bfd3f819914b5f5e4a52e9575a0e0ee487e816399e2ad78b4f7d10ad35ca87e` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-25models-dx7names-splash-48k-1.17.syx` (run `pk_build build-all` first; needs the Rust 1.98 toolchain: `mise exec --`) |
| `out/patch/diag-h1-i13return-48k-1.17.ungated.syx` | `401ccf2be3f2814e4bb79c6aed1872912da4ee73f2010e93c5b387fbf4279cf3` | a copy of the pre-Task-5b h1b; not rebuildable from this branch |
| `out/patch/diag-h1-i13return-96k-1.17.ungated.syx` | `02926b3f833ccdaa51172b98af3de83c4f65779999a3ea901d1a527c6796652c` | a copy of the pre-Task-5b h1a; not rebuildable from this branch |
| `out/patch/plaits-25models-fast5-names-splash-48k-1.17.syx` (lane/opt-o6: DX7 + O6 kernels, voice 17,124 B; owner checks: checksums ok, bootcheck 0 errors, encoding gate 0 violations, sections 2, 4, 5, 8 byte-equal to stock; not yet on hardware) | `f640129cd648e0e44ef061dabfb20b2fb8457fe8d344d09882e68cd30d5bccc0` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-25models-fast5-names-splash-48k-1.17.syx` |
| `out/patch/plaits-25models-fast6-names-splash-48k-1.17.syx` (H-opt8: lane/opt-o7, the STRING and DX7 kernels; owner checks: checksums ok, bootcheck 0 errors, encoding gate 0 violations, sections 2, 4, 5, 8 byte-equal to stock; not yet on hardware) | `aa8f314c345d1173d1229fac59a1ff74be8d0fa0e64a3776178def3fa33f41bd` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-25models-fast6-names-splash-48k-1.17.syx` (after `pk_build build-all`; `RUSTUP_TOOLCHAIN=1.98.1-aarch64-apple-darwin`) |
| `out/patch/plaits-25models-fast7-names-splash-48k-1.17.syx` (H-opt9: lane/opt-o8, DX7 as kernels (voice, patch unpack, envelope set-up, buffer loops), the wrapper's per-block path in wrap_sm.s, interp without a nested call; owner checks: checksums ok, bootcheck 0 errors, encoding gate 0 violations, sections 2, 4, 5, 8 byte-equal to stock; not yet on hardware) | `b9a10e2382f52c9750b93bd88e100c409dd7f35378a7b52c4afdfcec948bbc85` | `uv run python -m tools.patchkit.patchkit patch --firmware firmware/Digitakt_II_OS1.17.syx --modules ui/boot-splash,machines/plaits-48k -o out/patch/plaits-25models-fast7-names-splash-48k-1.17.syx` (lane opt-o8 at 7b47bd07, after `pk_build build-all`; `RUSTUP_TOOLCHAIN=1.98.1-aarch64-apple-darwin`) |

`mise run patch -- ...` is the same command. The gate runs by default; never add `--no-encoding-gate` for an image
you flash. The H2 hashes above are those of the images after the final fix wave (the stack-switch order was proved
interrupt-safe without a code change, so they equal the images of Task 6).

## The decision tree

**After every flash, create a new project before any check:** an older project may not play. The PLAY failures
reported on H3b went away with a new project (owner, 2026-10-18: `plaits-5models-names-48k` plays, PLAY included);
a project saved under an earlier image is the likely cause. Every step below that flashes an image starts with this.

1. **Flash h1b** (`6171da82…`, 48 kHz straight into the voice output).
   - Create a new project after flashing (an older project may not play).
   - Select ITONE on track 1: FUNC+SRC, then 7 x DOWN (ITONE is the 8th entry of the machine list), YES.
   - Make sure track 2 has a sample (a new project's track 2 may have none: if trigging it is silent, load any sample onto track 2 first), then check that it plays.
   - Trig track 1: note 60 should be a 261.6 Hz sine. PLAY, then reboot and check again.
   - P-lock TUNE on one step (hold the trig, turn TUNE): only that step's pitch changes. The routine renders both
     voices of the track in every block, so a releasing voice also follows a TUNE p-lock: the tail of the previous
     note bends to the p-locked step's tune while it decays. That is expected, not a fault.
   - **h1b passes:** go to step 3 (H2 with plaits-48k).
   - **h1b stalls or is silent:** recover, then step 2.
2. **Flash h1a** (`bc6a3314…`, 96 kHz through the stock decimator). Create a new project after flashing (an older project may not play). Then the same checks as in step 1.
   - **h1a passes:** go to step 4 (H2 with plaits-96k, one track only).
   - **h1a stalls too:** no H2. Stop and report both H1 results (the hook itself does not run on the unit).
3. **H2 with plaits-48k** (`50743416…`; only after h1b passed).
   0. Create a new project after flashing (an older project may not play).
   1. FUNC+SRC, 7 x DOWN (PLAITS is the 8th entry), YES. Make sure track 2 has a sample (a new project's track 2 may have none: if trigging it is silent, load any sample onto track 2 first), then check that it plays. Trig track 1:
      note 60 is 261.6 Hz, a bright VA tone. PLAY; reboot and check again.
   2. Play notes across the keyboard (KEYBOARD mode): in tune.
   3. P-lock HARMONICS (C), TIMBRE (E), MORPH (F) and TUNE (A) on single steps: only those steps change.
   4. LFO on TIMBRE: audible movement.
   5. SRC+YES: record what it does.
   6. PLAITS on track 1, then also on track 2 (two tracks at once). **Never more than 2 tracks triggered as PLAITS
      since boot on this image.** A track stops costing DSP only after it has been triggered under another machine
      (the type reaches the DSP only on a trig): switching a track away from PLAITS without a trig does not free it.
      Two tracks measure 73 % of the DSP block at the high bound with default settings; extreme HARMONICS, TIMBRE
      and MORPH on both tracks approach the 85 % budget (finding 06).
   7. Switch a ringing PLAITS track to ONESHOT and back (each change takes effect at the next trig). A small click
      on the first stock note after a track leaves PLAITS is expected: this image skips the stock decimator, whose
      state is then stale for that voice's first stock block.
   - **plaits-48k stalls or is silent** (with h1b passed, the hook works and the fault is in PLAITS): recover and
     report; optionally repeat 3.1 to 3.5 with plaits-96k, one track only (step 4).
   - **plaits-48k passes:** go on to H3 (section "H3: model 1, ASYM-TRI" below), then H3b (section "H3b: models 2 to
     4"), then H3c (section "H3c: models 5 and 6"), then H3d (section "H3d"), then H3e (section "H3e"), then step 6.
4. **H2 with plaits-96k** (`f37e87a0…`; after h1a passed and h1b did not, or as the optional fallback of step 3). Create a new project after flashing (an older project may not play).
   Then steps 3.1 to 3.5 and 3.7 on track 1. **Never more than 1 track triggered as PLAITS since boot on this image** (the same
   rule: a track stops costing DSP only after a trig under another machine). Do not run step 3.6: two tracks on this
   image measure 85.6 % of the block at the high bound, over the 85 % budget.
5. **Diagnostics come last,** and only if both h1b and h1a passed. Optional: they tell whether the earlier stalls came
   from the `JUMP (M13, I13)` return.
   - Create a new project after flashing (an older project may not play).
   - `diag-h1-i13return-48k-1.17.ungated.syx` and `diag-h1-i13return-96k-1.17.ungated.syx`: the same tone, returning
     through `JUMP (M13, I13)`. **FAILS THE ENCODING GATE; may stall; recovery: hold FUNC at power-on, TRIG 4, send the
     stock .syx with Transfer's legacy mode over MIDI DIN.**
   - If they stall while h1a/h1b passed, the stall is pinned on `083f683f`. If one passes, `word = "083f683f"` may be
     added to encoding_proven.toml with that evidence.
6. Record every result (H1, H2, H3, H3b, H3c, H3d and H3e) in the design's hardware table, and the shapes or `word =` entries of each image that passed in
   tools/patchkit/encoding_proven.toml. Decision for PLAITS: h1b passing keeps mode 1 (plaits-48k) as the default;
   otherwise mode 0 (plaits-96k).

## H3: model 1, ASYM-TRI (only after H2 with plaits-48k passed)

H3 (`out/patch/plaits-2models-48k-1.17.syx`, `68fee193…`) is H2 plus model 1, ASYM-TRI (Plaits' waveshaping model).
Model 0 (VA) renders bit for bit as in H2 (tests/patchkit/test_plaits_model0_h2.py). The model numbers now follow
Plaits' own order: 0 VA, 1 ASYM-TRI, 2 2FM, ... 23 SQUARE; 2 to 23 still play silence. Flash it only after step 3
(H2 with plaits-48k) passed; there is no 96k H3 image.

1. Flash H3. Create a new project after flashing (an older project may not play). FUNC+SRC, 7 x DOWN (PLAITS, the 8th entry), YES on track 1. Make sure track 2 has a sample (a new project's track 2 may have none: if trigging it is silent, load any sample onto track 2 first), then check that it plays.
   Trig track 1 with MODEL 0: the H2 VA tone at 261.6 Hz, unchanged.
2. Turn MODEL (knob B) to 1. A MODEL change takes effect at the next note-on: trig again. Note 60 is 261.6 Hz, a
   folded-triangle tone that sounds clearly different from VA.
3. On model 1, per the Plaits manual (the waveshaping model: an asymmetric triangle through a waveshaper and a
   wavefolder; AUX a variant with another wavefolder curve):
   - HARMONICS (C): the waveshaper's waveform (the middle is the linear shaper; either end gives other curves).
   - TIMBRE (E): the wavefolder amount (more folds, brighter and buzzier as it rises).
   - MORPH (F): the waveform's asymmetry (triangle to saw-like).
   - MIX (G) to the right: the AUX variant (a sine at TIMBRE 0, the second wavefolder as TIMBRE rises).
4. MODEL p-lock:
   - set MODEL (knob B) back to 0 (the track's own value: the steps without a lock play it);
   - place trigs on steps 1, 3, 5, 7, 9, 11, 13, 15;
   - p-lock MODEL = 1 on steps 1, 5, 9, 13 (hold the step's trig, turn knob B to 1);
   - PLAY: the notes alternate VA / ASYM-TRI (steps 1, 5, 9, 13 ASYM-TRI; 3, 7, 11, 15 VA).
   Also p-lock TUNE, HARMONICS, TIMBRE, MORPH on single model-1 steps: only those steps change.
5. Save the project, reboot, load it: MODEL and the knob values are back and model 1 plays.
6. **Track rule (unchanged from H2): never more than 2 tracks triggered as PLAITS since boot on this image,** with
   any mix of models. Two ASYM-TRI tracks measure 71.5 % of the block at the high bound and 73.5 to 75.7 % at
   worst-case settings; two VA tracks at worst-case settings (81 to 85 %) still set the limit (finding 06). A track stops costing DSP only after it has been triggered under another machine.
- **H3 stalls or is silent** while H2 passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to the H2 image
  (`plaits-va-48k-1.17.syx`, `50743416…`) and report which step failed.

## H3b: models 2 to 4, 2FM, FORMANT and ADDITIVE (only after H3 passed)

H3b (`out/patch/plaits-5models-48k-1.17.syx`, `e768eb48…`) is H3 plus models 2 (2FM, Plaits' two-operator FM), 3
(FORMANT, Plaits' granular formant oscillator) and 4 (ADDITIVE, Plaits' harmonic oscillator). Models 0 and 1 render bit
for bit as in H3 (tests/patchkit/test_plaits_model0_h2.py, test_plaits_model_digests.py); 5 to 23 still play silence.
Flash it only after H3 passed; there is no 96k H3b image.

1. Flash H3b. Create a new project after flashing (an older project may not play). FUNC+SRC, 7 x DOWN (PLAITS, the 8th entry), YES on track 1. Make sure track 2 has a sample (a new project's track 2 may have none: if trigging it is silent, load any sample onto track 2 first), then check that it plays.
   Trig track 1 with MODEL 0, then MODEL 1 (a trig after each change): the H3 tones, unchanged.
2. **2FM.** MODEL (knob B) to 2, trig. With HARMONICS (C) at 60 (a 2:1 ratio) note 60 is 261.6 Hz, an FM tone. Per the Plaits manual:
   - HARMONICS (C): the frequency ratio between the two operators (quantised to musical ratios; between the steps it
     glides through inharmonic ones).
   - TIMBRE (E): the modulation index (0: a pure sine; up: brighter, then metallic).
   - MORPH (F): feedback from the output into the modulator, none at the centre: to the right as phase modulation,
     to the left as frequency modulation (both roughen the tone towards noise at the ends, each differently).
   - MIX (G) to the right: AUX, the sub-oscillator (an octave below, phase-modulated by the carrier).
3. **FORMANT.** MODEL to 3, trig. Note 60 at 261.6 Hz, a vocal or resonant buzz. Per the manual:
   - HARMONICS (C): the frequency ratio between the two formants (the second formant above or below the first);
     towards the left also more of the carrier itself.
   - TIMBRE (E): the formant frequency (sweeps like a resonant filter or a vowel).
   - MORPH (F): the formant width and shape (narrow and buzzy to wide and soft; its effect fades on high notes).
   - MIX (G) to the right: AUX, windowed sines that imitate filtered waveforms (Braids' Z models): HARMONICS selects
     the filter character (peaking, low-pass, band-pass, high-pass), MORPH the carrier shape, TIMBRE the cutoff.
4. **ADDITIVE.** MODEL to 4, trig. Note 60 at 261.6 Hz, an organ-like tone. Per the manual:
   - HARMONICS (C): the number of bumps in the spectrum (one smooth peak to several).
   - TIMBRE (E): the index of the most prominent harmonic (the brightness: low harmonics to high ones).
   - MORPH (F): the bump shape, from flat and wide to peaked and narrow.
   - MIX (G) to the right: AUX, only the harmonics of a Hammond organ's drawbars (1, 2, 3, 4, 6, 8, 10, 12).
   - A change of HARMONICS, TIMBRE or MORPH on this model glides into the new spectrum over about a third of a second
     (Plaits smooths the harmonic amplitudes); that is expected.
5. MODEL p-lock across the new models:
   - set MODEL (knob B) back to 0;
   - place trigs on steps 1, 3, 5, 7, 9, 11, 13, 15;
   - p-lock MODEL = 2 on steps 3 and 11, 3 on steps 5 and 13, 4 on steps 7 and 15 (hold the step's trig, turn knob
     B);
   - PLAY: VA, 2FM, FORMANT, ADDITIVE, VA, 2FM, FORMANT, ADDITIVE; each note starts clean, with no click or leftover
     of the previous model.
   Also p-lock TUNE, HARMONICS, TIMBRE, MORPH on single steps of each new model: only those steps change.
6. Save the project with MODEL 4 on track 1, reboot, load it: MODEL is back at 4 and model 4 plays.
7. **Track rule (lower than H2 and H3): never more than 1 track triggered as PLAITS since boot on this image,** any
   model. One ADDITIVE track at its worst settings measures 67.1 % of the DSP block at the high bound; two measure
   86.6 %, over the 85 % budget (finding 06, "PLAITS models 2 to 4"). A track stops costing DSP only after it has
   been triggered under another machine. Do not test two PLAITS tracks on this image.
- **H3b stalls or is silent** while H3 passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to the H3 image
  (`plaits-2models-48k-1.17.syx`, `68fee193…`) and report which step and which model failed.

## H3c: models 5 and 6, WAVETABLE and CHORD (only after H3b passed)

H3c (`out/patch/plaits-7models-48k-1.17.syx`, `09ac59c0…`) builds on the owner-verified `plaits-5models-names-48k`
(`c3b7596b…`: MODEL names, models 0 to 4) and adds models 5 (WAVETABLE, Plaits' wavetable oscillator) and 6 (CHORD,
Plaits' chord engine). Models 0 to 4 render bit for bit as on H3b (tests/plaits/vectors); 7 to 23 still play silence.
There is no 96k H3c image.

1. Flash H3c. Create a new project after flashing (an older project may not play). FUNC+SRC, 7 x DOWN (PLAITS, the
   8th entry), YES on track 1. Make sure track 2 has a sample (a new project's track 2 may have none: if trigging it is silent, load any sample onto track 2 first), then check that it plays. Trig track 1 with MODEL 0, 1, 2, 3, 4 (a
   trig after each change; MODEL shows the names): the H3b tones, unchanged.
2. **WAVETABLE.** MODEL (knob B) to 5 (`WAVETABLE`), trig. Note 60 is 261.6 Hz. Per the Plaits manual:
   - HARMONICS (C): the bank (8 x 8 waves each: additive, formant-like, Braids/Shruthi waves, then a shuffled mix of
     the other banks' waves); in the right half the banks come back in reverse order and the scanning becomes stepped
     (no interpolation between the waves).
   - TIMBRE (E): the column of the 8 x 8 grid; MORPH (F): the row. Both scan smoothly through the waves.
   - MIX (G) to the right: AUX, the same wave at 5-bit resolution (a lo-fi, gritty version).
   - **Expected on H3c: a click at the start of every note** on this model (owner, 2026-10-18: WAVETABLE and CHORD
     "sound very percussive and almost click at the beginning of a note"). On H3c a note-on restarts the engine from
     Plaits' own power-up state, and Plaits' wavetable starts with a jump of about 25 times the steady level for a few
     samples (tests/plaits/test_wavetable_chord_host.py `test_note_on_transients`). H3d fixes it (section "H3d").
3. **CHORD.** MODEL to 6 (`CHORD`), trig. Per the manual:
   - HARMONICS (C): the chord (OCT, 5, sus4, m, m7, m9, m11, 69, M9, M7, M; HARMONICS at 0 is the octave).
   - TIMBRE (E): the inversion and transposition (the chord climbs as the notes rotate).
   - MORPH (F): the waveform, from organ and string-machine registrations (left) to wavetable voices (right).
   - MIX (G) to the right: AUX, the chord's root alone.
   - With HARMONICS, TIMBRE and MORPH at 0, note 60 is the octave chord at 261.1 Hz (Plaits detunes its chord 0.998).
   - On H3c, at a high MORPH and a low note the note start can click as on WAVETABLE (smaller; about 7.5 times at
     note 0), and at a high TIMBRE each note rotates through the chord's inversions for about 10 ms. H3d fixes both.
4. MODEL p-lock across all models: set MODEL back to 0; trigs on steps 1, 3, 5, 7, 9, 11, 13, 15; p-lock MODEL = 5 on
   steps 3 and 11, 6 on steps 5 and 13, 4 on step 7, 2 on step 15; PLAY: each note plays its model. Also p-lock TUNE,
   HARMONICS, TIMBRE, MORPH on single WAVETABLE and CHORD steps: only those steps change.
5. Save the project with MODEL 6 on track 1, reboot, load it: MODEL is back at 6 and CHORD plays.
6. **Track rule (unchanged from H3b): never more than 1 track triggered as PLAITS since boot on this image,** any
   model. WAVETABLE and CHORD fit two to a pair (76.7 % and 70.5 % of the DSP block at the high bound; CHORD's
   figure was measured at MORPH 0x3c00 with TIMBRE 0, not at its worst, which Task 9b measured: one CHORD track at TIMBRE 0x7f00, MORPH 0x3c00 and a note just under the point where its top voice mutes measures 60.4 % and two 70.9 %, finding 06, "PLAITS CHORD (model 6) at its worst knobs"), but
   two ADDITIVE tracks still measure 86.6 % (finding 06, "PLAITS models 5 and 6"). A track stops costing DSP only after
   it has been triggered under another machine.
- **H3c stalls or is silent** while H3b passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to `plaits-5models-names-48k`
  (`c3b7596b…`) and report which step and which model failed.

## H3d: engines run on between notes, and the boot splash (only after H3c passed)

H3d (`out/patch/plaits-7models-names-splash-48k-1.17.syx`, `8032d086…`) is H3c with two changes:

- **Engines run on between notes, as in Plaits.** A note-on initialises the engine only at the voice's first note
  since boot and when MODEL changed; every other note-on leaves the engine running and the amp envelope shapes the
  note. This removes the click and the sweep at every note start that the owner heard on H3c (WAVETABLE about 25
  times the steady level, CHORD up to 19 times at note 0; after: at the steady level). The first note after boot, and
  the first note after a MODEL change, still start from Plaits' power-up state and may still tick once on WAVETABLE
  and CHORD; a pattern that changes MODEL on every step (MODEL p-locks) re-initialises at each of those steps.
- **The boot splash** (ui/boot-splash, merged at 4b15d2c and owner-verified on the unit) is part of the build:
  `--modules ui/boot-splash,machines/plaits-48k`.

Models 0 to 6 sound as on H3c at the first note; only what happens at later note-ons changed (tests/plaits/vectors,
re-recorded at Task 9b; tests/plaits/test_continuous_host.py). There is no 96k H3d image.

1. **Boot.** Flash H3d and power on: the splash shows for about 1.5 s instead of the stock intro, then the unit boots
   as usual.
2. Create a new project after flashing (an older project may not play). FUNC+SRC, 7 x DOWN (PLAITS, the 8th entry),
   YES on track 1. Make sure track 2 has a sample (a new project's track 2 may have none: if trigging it is silent,
   load any sample onto track 2 first), then check that it plays.
3. **Models 0 to 6.** Trig track 1 with MODEL 0, 1, 2, 3, 4, 5, 6 (a trig after each change; MODEL shows the names):
   each sounds as on H3c.
4. **WAVETABLE retriggers (the acceptance check).** MODEL 5, trig once and let it ring. Then:
   - set a slow amp attack (AMP page, ATTACK around the middle) and trig repeatedly, fast (also hold the trig and
     play KEYBOARD notes): every note must swell in smoothly, **no click, no percussive start**;
   - set the attack back to 0 and trig rapidly at one pitch, then at different pitches: no click beyond the note's
     own start, and no sweep of the timbre at each note (on H3c each note swept in from another wave over about
     20 ms);
   - turn TIMBRE and MORPH between notes: the next note starts at the new position, with no sweep from the defaults.
5. **CHORD retriggers.** MODEL 6, HARMONICS at a chord (e.g. m7), TIMBRE and MORPH to the right, a low note (e.g.
   C1). The same checks as in step 4: with a slow attack the notes swell in smoothly, with no click; at attack 0 and
   rapid trigs no click and no rotation through the inversions at each note start.
6. **First note after a MODEL change.** MODEL 0, trig; MODEL 5, trig: this first WAVETABLE note may tick once (Plaits'
   power-up state); trig again: smooth. Report how audible that first tick is.
7. MODEL p-lock across all models as in H3c step 4 (each note plays its model), and p-lock TUNE, HARMONICS, TIMBRE,
   MORPH on single WAVETABLE and CHORD steps: only those steps change. PLAY with trigs on every step of one model:
   smooth.
8. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot on this image,** any model.
   Two ADDITIVE tracks measure 86.6 % of the DSP block at the high bound (finding 06); one CHORD track at its worst
   knobs (TIMBRE 0x7f00, MORPH 0x3c00, note 64) measures 60.4 % and two 70.9 % (finding 06, "PLAITS CHORD (model 6) at its worst knobs").
- **H3d stalls or is silent** while H3c passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3c
  (`plaits-7models-48k-1.17.syx`, `09ac59c0…`) and report which step failed.

## H3e: model 7, SPEECH (only after H3d passed)

H3e (`out/patch/plaits-8models-names-splash-48k-1.17.syx`, `8701a9bf…`) is H3d plus model 7, SPEECH (Plaits' speech
engine: a naive formant synth, a SAM-style synth and an LPC10 synth with five banks of words). Models 0 to 6 render bit
for bit as on H3d (tests/plaits/vectors); 8 to 23 still play silence. The boot splash is the same. There is no 96k H3e
image.

1. **Boot.** Flash H3e and power on: the splash, then the unit boots as usual.
2. Create a new project after flashing (an older project may not play). FUNC+SRC, 7 x DOWN (PLAITS, the 8th entry),
   YES on track 1. Make sure track 2 has a sample (a new project's track 2 may have none: if trigging it is silent,
   load any sample onto track 2 first), then check that it plays.
3. **Models 0 to 6.** Trig track 1 with MODEL 0 to 6 (a trig after each change): each sounds as on H3d.
4. **SPEECH: the HARMONICS walk.** MODEL (knob B) to 7 (`SPEECH`). Give the amp envelope a long decay (or hold) so
   that a word is not cut short; the amp envelope shapes every PLAITS note. Per the Plaits manual, HARMONICS (C)
   crossfades between the three synthesis methods and then selects a word bank (knob C's values, 0 to 127; the
   edges are approximate, and the bank selector has hysteresis, so a bank changes a little later in the direction
   you turn):
   - **0: the naive formant synth**, a pulse through five formant filters: a sustained vowel at the note (note 60 is
     261.6 Hz). MORPH (F) picks the vowel, TIMBRE (E) the vocal register (the formants shift).
   - **0 to about 21: towards SAM** (the Shruthi/Braids-style synth of three sine formants); about 21 is SAM alone.
   - **about 21 to 42: from SAM to the LPC10 vowels;** about 42 to 55 the LPC vowels alone. MORPH scans the vowels;
     TIMBRE shifts the formants (left a deep voice, right a small, high one).
   - **about 56 to 67: words, bank 1, colours** (red, orange, yellow, green, blue, indigo, violet).
   - **about 68 to 80: bank 2, numbers** (zero to ten).
   - **about 81 to 93: bank 3, letters** (a to z).
   - **about 94 to 106: bank 4, the NATO alphabet** (alpha to zulu).
   - **about 107 to 127: bank 5, synthesizer words** (analog, circuit, clock, control, digital, electronic, filter,
     frequency, generator, instrument, knob, machine, modular, modulator, operator, oscillator, patch, sequencer,
     synthesizer, VCA, voltage, waveform).
   - In a word bank MORPH selects the word (left the first, right the last) and TIMBRE shifts the formants; below
     about 51 and above about 76 TIMBRE also stretches the word (slower towards the ends of its travel).
   - MIX (G) to the right: AUX, the excitation alone (the "vocal cords" before the formant filters).
5. **Trigger behaviour.**
   - In a word bank every trig speaks the selected word once, from its start; a trig during a word restarts it.
     After the word the voice is silent until the next trig. Turn MORPH, trig: the new word. Turning MORPH between
     trigs does not change the word being spoken.
   - Holding a trig (or a long note length) changes nothing: the Digitakt gives PLAITS the note-on only, and
     Plaits' speech engine ignores how long its gate stays high too. The word plays at the note's pitch, in a
     monotone (Plaits' intonation and speed attenuverters are at noon here; there is no knob for them).
   - Where the LPC vowels sound (about 21 to 55, crossfaded with SAM below about 42) each trig puts a short random
     consonant (about 25 ms) before the vowel.
   - In the naive and SAM range (0 to about 21) a trig only starts the amp envelope: the vowel runs on (as on Plaits
     with its TRIG input patched).
   - Turning HARMONICS from a word bank down into the LPC vowels without a trig goes straight back to the vowels
     (Plaits reads past the end of a table there; the port stops the word instead).
6. **Sequenced words.** HARMONICS at a bank (e.g. about 74, numbers), trigs on steps 1, 5, 9, 13 with MORPH p-locked
   to different values on each: PLAY speaks a different word on each step. Then MODEL p-locks across models (e.g.
   MODEL 7 on steps 1 and 9, MODEL 0 and 5 on the others): each note plays its model.
7. **First note after a MODEL change.** The first note after a MODEL change starts the engine from Plaits' power-up
   state and can tick once (WAVETABLE and CHORD, as on H3d; on SPEECH in the naive range at high notes, about 3.5
   times the steady step). This is expected: do not report it unless it is more than a single tick.
8. Save the project with MODEL 7 on track 1, reboot, load it: MODEL is back at 7 and SPEECH plays.
9. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot on this image,** any model.
   One SPEECH track at its worst knobs (HARMONICS near 0, TIMBRE at the right, the top of the pitch range) measures
   54.3 % of the DSP block at the high bound and two 61.0 % (finding 06, "PLAITS SPEECH (model 7)"); two ADDITIVE
   tracks still measure 86.6 %, which sets the rule.
- **H3e stalls or is silent** while H3d passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3d
  (`plaits-7models-names-splash-48k-1.17.syx`, `8032d086…`) and report which step failed.

## H3f: output stage, velocity, RINGS (12) and TERRAIN (21) (only after H3e passed)

H3f (`out/patch/plaits-10models-names-splash-48k-1.17.syx`, `a16f9ae2…`) is H3e plus Plaits' output stage (each
model's own output level and SPEECH's limiter, as in Plaits), velocity, and models 12 RINGS and 21 TERRAIN. The other
values from 8 to 23 still play silence.

1. **Boot and a new project.** Flash H3f and power on: the splash, then the unit boots. Create a new project (an
   older project may not play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if
   trigging it is silent, load any sample onto track 2 first), then check that it plays.
2. **Loudness across models.** At default knobs trig track 1 with MODEL 0 to 7: VA is as loud as on H3e; the others
   are equal or up to about 3 dB quieter (Plaits' own balance: ASYM-TRI, FORMANT and TERRAIN -1.2 dB, 2FM and
   WAVETABLE -2.5 dB, SPEECH -3.2 dB). **SPEECH is no longer much louder:** MODEL 7, HARMONICS 0, TIMBRE and MORPH
   at the left, a high note (e.g. TUNE up): on H3e this peaked about 12 dB over VA; now it stays at about VA's
   level (Plaits' limiter; a short peak at the first note after a model change is the limiter catching up).
3. **RINGS: velocity.** MODEL to 12 (`RINGS`). Give the amp envelope a long decay. Trig with step velocities 127,
   then 30 (or a velocity p-lock): 127 strikes louder and brighter and rings longer; 30 is soft and dull. The
   default velocity 100 sounds like Plaits with nothing in its LEVEL input. HARMONICS (C) bends the partials
   (around 38 harmonic, left and right inharmonic/bell-like), TIMBRE (E) the brightness, MORPH (F) the ring length.
4. **RINGS: fast retriggers.** MORPH to the right (long ring), velocity 127, trigs on every step at a fast tempo
   (or retrig 1/32): each trig strikes again on top of the ring, with no runaway loudness, no crackle and no
   silence; the level settles. Then MORPH to the left: short plucks.
5. **TERRAIN's knobs.** MODEL to 21 (`TERRAIN`). HARMONICS walks through eight terrains (smooth crossfades; the
   last third are the wavetable data read as terrains, brighter and buzzier); TIMBRE widens the path (left a near
   sine, right rich); MORPH moves the path's centre. MIX (G) to the right: AUX, a phase-modulated sine. After big
   jumps (a high note with TIMBRE right, then a low note) TERRAIN's path can stay wider than its terrain until the
   next big change, which changes its colour. Report it only if it crackles or drops out.
6. **First note after a MODEL change.** As on H3e it can tick once (WAVETABLE's is now clipped to full scale, as in
   Plaits). Do not report a single tick.
7. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One RINGS track
   while it rings measures 73.1 % of the DSP block at the high bound (the heaviest model now), one TERRAIN track at
   its worst knobs 65.0 % (finding 06, "PLAITS RINGS and TERRAIN"); two of either would exceed 85 %.
8. Save the project with MODEL 12 on track 1, reboot, load it: MODEL is back at 12 and RINGS plays.
- **H3f stalls or is silent** while H3e passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3e
  (`plaits-8models-names-splash-48k-1.17.syx`, `8701a9bf…`) and report which step failed.

## H3g: the drums, KICK (13), SNARE (14) and HIHAT (15) (only after H3f passed)

H3g (`out/patch/plaits-13models-names-splash-48k-1.17.syx`, `6d5644cb…`) is H3f plus models 13 KICK, 14 SNARE and 15 HIHAT.
The values 8 to 11 and 16 to 20, 22 and 23 still play silence.

1. **Boot and a new project.** Flash H3g and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **The three drums, with velocity.** MODEL (B) to 13 (`KICK`), 14 (`SNARE`), 15 (`HIHAT`) in turn: the header reads
   the name. Trig note 60 (give the amp envelope a long decay and the filter open) and then with step velocities 127
   against 30 (or a velocity p-lock): 127 strikes louder and harder, 30 is soft and quieter. The default velocity 100
   sounds like Plaits with nothing in its LEVEL input.
   - KICK: an 808-style bass drum. HARMONICS (C) from the left adds attack FM, then self FM, then drive (right is a hard,
     driven kick); TIMBRE (E) the tone (left dark and round, right bright and clicky); MORPH (F) the decay (left a short
     thump, right a long boom). MIX (G) to the right: AUX, a more synthetic bass drum with its own click.
   - SNARE: an 808-style snare. HARMONICS (C) the snappy amount (left only the shell, right mostly the noise); TIMBRE (E)
     the tone (left two modes, right more modes); MORPH (F) the decay. MIX to the right: a 909-style snare.
   - HIHAT: HARMONICS (C) the noisiness (left metallic, right noisy), TIMBRE (E) the colour (left dark, right bright),
     MORPH (F) the decay (left a closed hat, right an open one). MIX to the right: a second, ring-modulated hat.
3. **Fast retriggers.** Each drum, MORPH to the right, velocity 127, a trig on every step at a fast tempo (or retrig
   1/32): each trig strikes again, with no runaway loudness, no crackle and no silence; the level settles. Then MORPH to
   the left: short hits. A trig 1/32 apart at a low note (TUNE down) on KICK must not click or distort badly.
4. **Level** against VA at default knobs: within a few dB (the output stage is Plaits' own: all three have gain 0.8, as
   Plaits).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high
   bound measures 62.3 % (KICK), 63.3 % (SNARE) and 64.6 % (HIHAT) of the DSP block while ringing (finding 06, "PLAITS
   KICK, SNARE and HIHAT"); RINGS, at 73.1 %, is still the heaviest; two drum tracks would be about 80 to 87 % (not
   measured).
6. **Recovery check.** After the drums, MODEL back to 0 (VA) and trig: VA plays as on H3f (the engines share one state;
   a model change starts the new one from its Init). Save the project with MODEL 14 on track 1, reboot, load it: MODEL is
   back at 14 and SNARE plays.
- **H3g stalls or is silent** while H3f passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3f
  (`plaits-10models-names-splash-48k-1.17.syx`, `a16f9ae2…`) and report which step failed.

## H3h: model 9, NOISE (only after H3g passed)

H3h (`out/patch/plaits-14models-names-splash-48k-1.17.syx`, `c1ce2515…`) is H3g plus model 9, NOISE. The values 8, 10, 11,
16 to 20, 22 and 23 still play silence.

1. **Boot and a new project.** Flash H3h and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **Model 9.** MODEL (B) to 9: the header reads `Model=NOISE`. Trig note 60 (give the amp envelope a long decay and the
   filter open): a filtered, clocked noise, not a pitched tone unless MORPH is high. NOISE's knobs:
   - TIMBRE (E) is the clock of the noise: left a slow, stepped random (sample and hold at a very low rate), right white
     noise (the clock above a quarter of the sample rate).
   - MORPH (F) is the filter resonance: left a wide, gentle filter, right a very sharp one that rings at the note (a
     whistling, pitched noise; the level stays even, the filter is gain-compensated).
   - HARMONICS (C) moves the second band-pass away from the note (centre: both at the note; left an octave or two below,
     right above, up to two octaves) and sweeps OUT's filter from low-pass (left) through band-pass (centre) to high-pass
     (right).
   - MIX (G) to the right: AUX, the two band-passes added (a more resonant, pitched noise).
   - Every trig restarts the two clocks together (a sync): with TIMBRE low the trig is audible as a new random step.
3. **Velocity / trigger:** NOISE does not read the velocity (it is not a strike engine): velocity 127 against 30 sounds
   the same. It runs on between notes: a trig while it plays does not click.
4. **Level** against VA at default knobs: within a few dB (the output stage is Plaits' own).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high
   bound measures 73.1 % of the DSP block with NOISE at its worst knobs (finding 06, "PLAITS NOISE"); on the same windows (100,500) RINGS measures 70.6 %, so NOISE is the heaviest model (the 1-track rule stands).
6. **Recovery check.** After NOISE, MODEL back to 0 (VA) and trig: VA plays as on H3g. Save the project with MODEL 9 on
   track 1, reboot, load it: MODEL is back at 9 and NOISE plays.
- **H3h stalls or is silent** while H3g passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3g
  (`plaits-13models-names-splash-48k-1.17.syx`, `6d5644cb…`) and report which step failed.

## H3i: model 17, PHASE-DIST (only after H3h passed)

H3i (`out/patch/plaits-15models-names-splash-48k-1.17.syx`, `eb38ae98…`) is H3h plus model 17, PHASE-DIST. The values 8, 10, 11,
16, 18 to 20, 22 and 23 still play silence.

1. **Boot and a new project.** Flash H3i and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **Model 17.** MODEL (B) to 17 (past 16, which is silent): the header reads `Model=PHASE-DIST`. Trig note 60: a sine
   at the note, bent by phase distortion (a bright, bent tone at the default knobs; at TIMBRE 0 and MORPH 0 a pure sine, at
   TIMBRE 0 and higher MORPH a sine with a slightly warped phase). PHASE-DIST's knobs:
   - TIMBRE (E) is the modulation amount: left a (nearly) pure sine, right a strongly bent, bright, vocal tone.
   - HARMONICS (C) is the frequency ratio of the modulator (quantized to musical ratios): left a low ratio, right high
     ones; away from the octaves the tone is inharmonic with the unsynced AUX voice.
   - MORPH (F) is the asymmetry of the modulator (pulse width): left symmetric, right strongly skewed (a brighter,
     sawtooth-like bend).
   - MIX (G) to the right: AUX, the free-running modulator (a phase-modulation sound that is not locked to the note).
3. **Velocity / trigger:** PHASE-DIST reads neither the velocity nor the trigger (it is not a strike engine): velocity
   127 against 30 sounds the same. It runs on between notes: a trig while it plays does not click.
4. **Level** against VA at default knobs: within a few dB (the output stage is Plaits' own).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high
   bound measures 71.4 % of the DSP block with PHASE-DIST at its worst knobs (finding 06, "PLAITS PHASE-DIST"); RINGS and
   NOISE measure 73.1 % (the 1-track rule stands).
6. **Recovery check.** After PHASE-DIST, MODEL back to 0 (VA) and trig: VA plays as on H3h. Save the project with
   MODEL 17 on track 1, reboot, load it: MODEL is back at 17 and PHASE-DIST plays.
- **H3i stalls or is silent** while H3h passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3h
  (`plaits-14models-names-splash-48k-1.17.syx`, `c1ce2515…`) and report which step failed.

## H-opt1: RINGS on its SIMD kernel (only after H3h passed) — FAILED on the unit 2026-10-20 (DSP hang on the first RINGS block; root cause confirmed by probe-samedag-store: a DAG register stored through its own DAG)

H-opt1 (`out/patch/plaits-14models-names-splash-48k-1.17-rings-opt.syx`, `ceb8dec7…`) is H3h with RINGS' resonator bank
(`lib/resonator_svf4.s`, OPTIMIZING.md) running as a hand-written SIMD kernel: the first image whose PLAITS code
switches the DSP into SIMD mode. The sound must not change at all (bit-identical on the emulator); one RINGS track
measured 52.9 % at the high bound (41.9 % nominal; was 73.1 %; stock alone 45.9 %) before fix round 1, which adds
about 16 instructions per kernel call (192 per block, under 0.1 point).

1. **Boot and a new project.** Flash H-opt1, power on (splash, boot), create a new project, FUNC+SRC, 7 x DOWN, YES on
   track 1; make sure track 2 has a sample and plays.
2. **RINGS sounds identical.** MODEL (B) to 12 (`RINGS`): trig note 60 at velocity 127 and 30, sweep HARMONICS, TIMBRE
   and MORPH: it sounds exactly as on H3h (compare with H3h if in doubt). Then MODEL 0, 9, 13 and 21: as on H3h.
3. **2 and 3 RINGS tracks.** Put RINGS (MODEL 12) on tracks 1 and 2, trig both together (HARMONICS right, MORPH
   right: the worst knobs): both ring, no dropouts, the sequencer and screen stay responsive. Then a third RINGS track:
   the same check. Extrapolated (not measured): about 60 % for 2 tracks and 67 % for 3 at the high bound (7 points per
   extra RINGS track); other models are heavier now (NOISE, ADDITIVE), so keep the other tracks off PLAITS.
4. **Recovery check.** MODEL back to 0 (VA), trig: VA plays. Save with MODEL 12, reboot, load: RINGS plays.
- **H-opt1 stalls, clicks or is silent** while H3h passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3h and report which step
  failed. Every instruction word has a stock precedent, but stock never runs SIMD *loads* from L2 (its SIMD data is in
  L1): RINGS that sounds wrong, thin (filters 1 and 3 missing) or blows up points there more than a stall does. The next
  image is then a one-variable probe, not a kernel change: a PEYEN bracket that loads and stores a known pair on
  `_pl_stack` with the PEy half made audible.

## P-probes: one suspect access each (only if the coordinator asks)

`probe-simd-l2-load-14models-48k-1.17.syx` (`d0085564…`) and `probe-simd-l2-store-…` (`855474a8…`) are H3h plus, in
VA's render only, one SIMD-mode load (or store) of a known pair on `_pl_stack`. VA's output is scaled by 4x the PEy half
of the pair (0.25). `probe-samedag-store-14models-48k-1.17.syx` (`5e4fae1d…`) instead does no SIMD and stores M4
through DAG1 once (`DM(I4, M6) = M4`, H-opt1's push form, illegal per the PRM), then scales VA by 4x the stored word.
1. New project, PLAITS on track 1, MODEL 0 (VA), trig note 60. VA at its usual level means the access works. Silence
   means PEy read 0. A much louder or distorted VA means PEy got the PEx word. A hang (audio dies) on the first trig
   means the access itself hangs the DSP.
2. Recover as below if it hangs. Report which image, and which of the four outcomes.

## H-opt2: RINGS kernel without SIMD-mode transfers (only after H3h passed) — PASSED on the unit 2026-10-20 (3 RINGS tracks play concurrently)

H-opt2 (`out/patch/plaits-14models-names-splash-48k-1.17-rings-sm.syx`, `0adcca17…`) is H3h with
`lib/resonator_svf4_sm.s`. SIMD is used for compute only, and every load and store runs in SISD, as stock does with L2
data. On the emulator it is bit-identical to H3h, at 31,472 instructions per RINGS block (H3h: 125,766). It also
pushes its DAG registers through R0 (no same-DAG store).
1. New project, PLAITS on track 1, MODEL 12 (RINGS): trig note 60 at velocity 127 and 30, sweep HARMONICS, TIMBRE and
   MORPH. It sounds exactly as on H3h. Then MODEL 0, 9, 13 and 21: as on H3h.
2. Two RINGS tracks triggered together at the worst knobs (HARMONICS and MORPH right): no dropouts, UI responsive.
3. Recovery check: MODEL 0, trig; save with MODEL 12, reboot, load: RINGS plays.
- **H-opt2 hangs** (audio dies on the first RINGS trig): recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode), go back to H3h. The SIMD-transfer hypothesis is then
  and the same-DAG hypothesis are wrong or incomplete; flash the probes next.

## H3j: model 16, VCF-VA (only after H3i passed)

H3j (`out/patch/plaits-16models-names-splash-48k-1.17.syx`, `d93c8bbb…`) is H3i plus model 16, VCF-VA. The values 8, 10, 11,
18 to 20, 22 and 23 still play silence.

1. **Boot and a new project.** Flash H3j and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **VCF-VA's knobs.** MODEL (B) to 16: the header reads `Model=VCF-VA`. Trig note 60: a sawtooth (or pulse) oscillator
   plus a sub oscillator through a resonant low pass filter.
   - TIMBRE (E) is the cutoff relative to the note: left dark and dull, right fully open and bright.
   - HARMONICS (C) is the filter's slope and resonance: at the left both filter stages act (steeper, 24 dB) and it rings
     with resonance; around 0.4 to 0.6 the resonance is lowest (clean); to the right the second stage fades out (gentler,
     12 dB) and the resonance rises again.
   - MORPH (F) is the shape: left a saw, rising to a square, then a narrowing pulse; away from the centre a sub
     oscillator (an octave down) fades in.
   - MIX (G) to the right: AUX, the high pass output of the first filter stage.
3. **Velocity / trigger:** VCF-VA reads neither (it is not a strike engine): velocity 127 against 30 sounds the same. It
   runs on between notes: a trig while it plays does not click.
4. **Level** against VA at default knobs: within a few dB (the output stage is Plaits' own).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high
   bound measures 60.4 % of the DSP block with VCF-VA at its worst knobs (finding 06, "PLAITS VCF-VA"); RINGS and
   NOISE measure 73.1 % (the 1-track rule stands).
6. **Recovery check.** After VCF-VA, MODEL back to 0 (VA) and trig: VA plays as on H3i. Save the project with
   MODEL 16 on track 1, reboot, load it: MODEL is back at 16 and VCF-VA plays.
- **H3j stalls or is silent** while H3i passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3i
  (`plaits-15models-names-splash-48k-1.17.syx`, `eb38ae98…`) and report which step failed.

## H-opt3: the drums on SISD kernels (only after H-opt2 passed)

H-opt3 (`out/patch/plaits-15models-drumsfast-names-splash-48k-1.17.syx`, `030afaf3…`) is H3i with the sample loops of
KICK (13), SNARE (14) and HIHAT (15) in `lib/drums_sm.s`: SISD only (no SIMD mode at all), DAG registers pushed
through R0, every instruction shape taken from stock code inside functions. On the emulator it is bit-identical to
H3i (core digests unchanged); fuzz worst per block (seed 1): KICK 67,911 → 23,375, SNARE 74,168 → 26,340, HIHAT
72,783 → 27,959 instructions.
1. New project, PLAITS on track 1, MODEL 13 (KICK): trig note 60 at velocity 127 and 30, sweep HARMONICS, TIMBRE and
   MORPH. It sounds exactly as on H3i. The same for MODEL 14 (SNARE) and MODEL 15 (HIHAT); then MODEL 0 and 12: as
   on H3i.
2. KICK, SNARE and HIHAT on four or more tracks together (e.g. tracks 1-2 KICK, 3-4 SNARE, 5-6 HIHAT, 16th-note
   trigs, worst knobs: HARMONICS and MORPH right): no dropouts, UI responsive. Then six HIHAT tracks together.
3. Recovery check: MODEL 0, trig; save with MODEL 15, reboot, load: HIHAT plays.
- **H-opt3 hangs or clicks** (audio dies on the first drum trig): recover (hold FUNC at power-on, TRIG 4, send the
  stock `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode), go back to H3i, and report which drum.

## H-opt4: shared output stage and CHORD on SISD kernels (only after H-opt3 passed)

H-opt4 (`out/patch/plaits-17models-fast2-names-splash-48k-1.17.syx`, `c17059bb…`) is H3k (H3j plus SQUARE, 23) plus
lane opt-o3: every voice's output stage (both limiters/gains, the mix, the DC blocker and the output gain) in
`post_sm.s`, CHORD's two oscillator loops and buffer loops in `lib/wavetable_sm.s`, `lib/string_synth_sm.s` and
`lib/chord_loops_sm.s`, hb_init skipped at 48 kHz, and the odd voice's copy as a hardware loop. SISD only, DAG
registers pushed through R0, every instruction shape from stock code inside functions. On the emulator every model is
bit-identical to H3k (core digests unchanged); fuzz worst per block (seed 1): VA 80,557 → 75,535, CHORD 56,771 →
30,711, RINGS 31,494 → 25,083, KICK 23,386 → 17,999 instructions.
1. Create a new project. PLAITS on track 1; walk MODEL 0 to 23 with one trig each (note 60, velocity 100, default
   knobs, then HARMONICS, TIMBRE and MORPH right): every model sounds exactly as on H3k (the unported ones stay
   silent). Listen closely to SPEECH (7), NOISE (9) and RINGS (12), whose output runs through the limiter, and to
   CHORD (6).
2. 4 drum tracks (KICK, SNARE, HIHAT, KICK: 13, 14, 15, 13), 2 RINGS tracks (12) and 1 CHORD track (6, MORPH in the
   middle, the crossfade where both kinds of voice render) together, 16th-note trigs: no dropouts, UI responsive.
   Then add tracks one at a time (a second CHORD, then more drums) and note where it first glitches.
3. Recovery check: MODEL 0, trig; save with MODEL 6, reboot, load: CHORD plays.
- **H-opt4 hangs or clicks** (audio dies on the first trig of any model): recover (hold FUNC at power-on, TRIG 4,
  send the stock `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode), go back to H-opt3, and report the
  model and whether every model or only CHORD fails (the stage kernel runs for every model, the CHORD kernels only for
  model 6).
## H3k: model 23, SQUARE (only after H3j passed)

H3k (`out/patch/plaits-17models-names-splash-48k-1.17.syx`, `f49cc711…`) is H3j plus model 23, SQUARE. The values 8, 10, 11,
18 to 20 and 22 still play silence.

1. **Boot and a new project.** Flash H3k and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **SQUARE arpeggiating from sequencer trigs.** MODEL (B) to 23: the header reads `Model=SQUARE`. SQUARE is always
   clocked: each trig (note-on) steps the arpeggio one note, so put the same note (say C-4) on steps 1, 5, 9 and 13, or on every
   step, and press PLAY. A single trig plays one note; a held note does not arpeggiate by itself. At the default knobs you hear a
   chiptune square stepping through the notes of a chord, with a quieter NES-style triangle bass an octave below
   (MIX (G) to the right brings the bass forward: it is AUX).
3. **The knobs.**
   - HARMONICS (C) picks the chord (11 of them, from a single note at the far left, then octaves, fifths, minor and major triads and
     sevenths); a change takes effect at the next trig.
   - TIMBRE (E) picks the pattern in 12 steps: up, down, up-down and random, each over 1, 2 or 4 octaves. TIMBRE 0 with HARMONICS 0
     plays the note on every trig. Change TIMBRE while the sequence runs: the pattern changes at the next trig without
     restarting.
   - MORPH (F) is the waveform: near the middle a clean square, to the left a narrower pulse tone, to the right a hard-synced
     sweep that gets brighter and harsher.
   - Fast retrigger (every step, or 1/16 notes): each trig still steps once; no runaway, no crackle, no hang.
4. **Level** against VA at default knobs: within a few dB (the output stage is Plaits' own, 0.5 gain for SQUARE: it may be quieter).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high
   bound measures 52.3 % of the DSP block with SQUARE at its worst knobs (finding 06, "PLAITS SQUARE"); RINGS and NOISE
   measure 73.1 % (the 1-track rule stands).
6. **Recovery check.** After SQUARE, MODEL back to 0 (VA) and trig: VA plays as on H3j. Save the project with
   MODEL 23 on track 1, reboot, load it: MODEL is back at 23 and SQUARE plays (the arpeggio starts over: the first trig steps to its first note).
- **H3k stalls or is silent** while H3j passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3j
  (`plaits-16models-names-splash-48k-1.17.syx`, `d93c8bbb…`) and report which step failed.

## H3l: model 22, STRING MACHINE (only after H3k passed)

H3l (`out/patch/plaits-18models-names-splash-48k-1.17.syx`, `1ab2ef0b…`) is H3k plus model 22, STRING MACHINE. The values 8, 10, 11
and 18 to 20 still play silence.

1. **Boot and a new project.** Flash H3l and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **Model 22.** MODEL (B) to 22: the header reads `Model=STRING MACH`. Trig note 60 (hold it or use a long envelope): a sustained
   string-machine chord (a single note at HARMONICS 0), with a slow chorus movement.
3. **STRING MACHINE's knobs.**
   - HARMONICS (C) picks the chord (11 of them: from a single note, then octaves, fifths, minor and major triads and sevenths); the
     notes change smoothly at the next trig.
   - MORPH (F) is the registration: left a plain saw sound, right a hollow square (organ-like) sound, steps and blends in between.
   - TIMBRE (E) is the filter cutoff (dull at 0, bright at the right) and the ensemble: at the middle (0.5) the chorus is off (dry);
     toward either end it fades in, deeper and wider as TIMBRE rises.
   - MIX (G) to the right moves from one channel to the other (they differ by the ensemble and the filter pair).
4. **Level** against VA at default knobs: within a few dB (Plaits' output stage, AUX gain as for the other models).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high bound measures
   60.3 % of the DSP block with STRING MACHINE at its worst knobs (finding 06, "PLAITS STRING MACHINE"); RINGS and NOISE
   measure 73.1 % (the 1-track rule stands).
6. **Recovery check.** After STRING MACHINE, MODEL back to 0 (VA) and trig: VA plays as on H3k. Save the project with
   MODEL 22 on track 1, reboot, load it: MODEL is back at 22 and STRING MACHINE plays.
- **H3l stalls or is silent** while H3k passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3k
  (`plaits-17models-names-splash-48k-1.17.syx`, `f49cc711…`) and report which step failed.

## H3m: model 10, DUST (only after H3l passed)

H3m (`out/patch/plaits-19models-names-splash-48k-1.17.syx`, `3146da3d…`) is H3l plus model 10, DUST. The values 8, 11 and 18 to 20 still play
silence. This image uses 206 KB more DSP memory than H3l (every voice now holds DUST's 16 KB reverb memory: 17,116 bytes per voice, 16 voices), now reserved
memory that is not loaded at boot (and not zero at load: the voices are initialised at their first note). The first boot is the first thing to check.

1. **Boot and a new project.** Flash H3m and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **Model 10.** MODEL (B) to 10: the header reads `Model=DUST`. Trig note 60 with TIMBRE (E) at the right: a dense crackle of random
   clicks, like dust or rain on a roof (it is not pitched; the note sets the filter range the clicks ring in). It keeps playing while the
   note is held and between trigs.
3. **DUST's knobs.**
   - TIMBRE (E) is the density: sparse separate ticks at the left, a continuous noisy crackle at the right.
   - HARMONICS (C) is how far each click's pitch is spread: at 0 all clicks ring at the note, to the right they scatter widely.
   - MORPH (F) at the middle is the dry crackle; to the left it adds a reverb-like diffusion (a smeared wash, most at 0); to the right the
     clicks ring more and more (resonance, up to a whistling tone).
   - MIX (G) to the right moves from one channel to the other (OUT is the filtered particles, AUX the raw clicks).
   - Retrig: each trig restarts the first click of the block (no runaway, no crackle between trigs).
4. **Level** against VA at default knobs: within a few dB (Plaits' output stage).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high bound measures
   63.2 % of the DSP block with DUST at its worst knobs (two tracks, extrapolated: 82.1 %; finding 06, "PLAITS DUST"). Two DUST tracks play
   different clicks (the random state is per track: seed 0x21 + track * 0x9e3779b1).
6. **Recovery check.** After DUST, MODEL back to 0 (VA) and trig: VA plays as on H3l. Save the project with MODEL 10 on track 1, reboot,
   load it: MODEL is back at 10 and DUST plays. Also change the model from DUST to another and back a few times: the first trig after each change
   may take slightly longer to start (the reverb memory is cleared) but must not drop the note.
- **H3m stalls or is silent** while H3l passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3l
  (`plaits-18models-names-splash-48k-1.17.syx`, `1ab2ef0b…`) and report which step failed (a stall at boot points at the memory growth).

## H-opt6: PHASE-DIST, ADDITIVE and VA on SISD kernels, compressed kernels (only after H3m passed)

H-opt6 (`out/patch/plaits-19models-fast4-names-splash-48k-1.17.syx`, `126c9716…`) is H3m plus lane opt-o5:
PHASE-DIST's oscillator loop and its 2x downsampling (`lib/phase_dist_sm.s`), VA's three shape oscillators on the
same kernel and its variable saw (`lib/va_sm.s`), ADDITIVE's harmonic oscillators and amplitude update
(`lib/additive_sm.s`), and every PLAITS kernel (drums, RINGS, output stage, CHORD too) assembled in the compressed
encodings outside its hardware loops (each loop stays 48-bit; the encoding gate now checks loop ends and delayed-branch
slots). SISD only, DAG registers pushed through R0, every shape from stock code inside functions. On the emulator
every model is bit-identical to H3m (core digests unchanged, both modes). Fuzz worst per block: PHASE-DIST 97,034 →
33,953, ADDITIVE 88,156 → 25,466, VA 75,626 → 34,381 instructions.
1. Create a new project. PLAITS on track 1.
2. Quick walk: MODEL 0 to 23 with one trig each (note 60, velocity 100, default knobs, then HARMONICS, TIMBRE and MORPH
   right): every model sounds exactly as on H3m (8, 11, 18 to 20 stay silent). Listen closely to VA (0), ADDITIVE (4)
   and PHASE-DIST (17), and to RINGS (12) and the drums (13 to 15), whose kernels changed encoding.
3. Multi-track: PHASE-DIST on tracks 1 to 4 (16th-note trigs, TUNE high, HARMONICS right, TIMBRE in the middle), then
   the same with ADDITIVE (MORPH and TIMBRE right) and with VA (TUNE high, HARMONICS right): no dropouts, UI
   responsive. Then add tracks of the same model one at a time and note where it first glitches.
4. 4 KICK (13) + 2 RINGS (12) + 1 CHORD (6, MORPH in the middle) together, 16th-note trigs: on `plaits-16models-fast` (48cefe81)
   4 KICK + 2 RINGS played cleanly and adding the CHORD made it choppy. Note whether it is clean now, and if so add tracks (a second CHORD, then KICK) until it glitches.
5. Recovery check: MODEL 17, trig; save, reboot, load: PHASE-DIST plays.
- **H-opt6 hangs or clicks** (audio dies on the first trig of a model): recover (hold FUNC at power-on, TRIG 4, send
  the stock `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode), go back to H3m, and report the model:
  0, 4 or 17 points at the new kernels, any model at the compressed output stage.

## H3n: model 8, SWARM (only after H3m passed)

H3n (`out/patch/plaits-20models-names-splash-48k-1.17.syx`, `bbc893b8…`) is H3m plus model 8, SWARM. The values 11 and 18 to 20 still play silence. The DSP
memory is the same as H3m's (SWARM's state is 484 bytes per voice, inside the union that DUST sets).

1. **Boot and a new project.** Flash H3n and power on (splash, then boot). Create a new project (an older project may not
   play). FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample (if trigging it is silent, load
   any sample onto track 2 first), then check that it plays.
2. **Model 8.** MODEL (B) to 8: the header reads `Model=SWARM`. Trig note 60: a detuned swarm of sawtooths with a thin layer of sines, shimmering
   around the note. SWARM runs in Plaits' continuous mode (an owner decision, an exception to the usual trig convention): a trig does NOT restart
   the grains; the swarm runs at a steady rate from the first trig and the Digitakt's amp envelope shapes the note. Trig again: the same swarm goes on.
3. **SWARM's knobs.**
   - HARMONICS (C) is the spread: at 0 all eight grains sit at the note (a single clean saw plus sine); a little to the right detunes them into a
     chorus; at the right they scatter widely around the note (up to a few octaves).
   - TIMBRE (E) is the grain rate: slow, separate grains at the left, a fast shimmer at the right.
   - MORPH (F) is the grain size: at the right short separate grains (a cloud of blips); to the left long, overlapping grains that glide between
     random pitches (a swarm of glissandi, most at 0).
   - MIX (G) to the right moves from the sawtooths (OUT) to the sines (AUX).
   - Retrig: fast retriggers change nothing in the swarm itself: no runaway, no crackle, no restart of the grains.
4. **Level** against VA at default knobs: within a few dB (Plaits' output stage: OUT goes through its limiter at 3 x).
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track at the high bound measures
   54.4 % of the DSP block with SWARM at its worst knobs (two tracks, extrapolated: about 65 %; finding 06, "PLAITS SWARM"). Two SWARM tracks play
   different grains (the random state is per track: seed 0x21 + track * 0x9e3779b1).
6. **Recovery check.** After SWARM, MODEL back to 0 (VA) and trig: VA plays as on H3m. Save the project with MODEL 8 on track 1, reboot,
   load it: MODEL is back at 8 and SWARM plays. Also change the model from SWARM to another and back a few times: no dropped notes.
- **H3n stalls or is silent** while H3m passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3m
  (`plaits-19models-names-splash-48k-1.17.syx`, `3146da3d…`) and report which step failed.

## H-opt7: the O6 kernels (only after H3o passed)

H-opt7 (`out/patch/plaits-21models-fast5-names-splash-48k-1.17.syx`, `25d5e892…`) is H3o (STRING, model 11) plus lane
opt-o6: SISD kernels for FORMANT (3), NOISE (9), DUST (10), STRING MACHINE (22), SWARM (8), TERRAIN (21), 2FM (2),
VCF-VA (16), WAVETABLE (5), SPEECH (7: its SAM synth and the formant filters, which HIHAT's coloration filter shares),
ASYM-TRI (1) and VA's (0) mixing loops. STRING MACHINE's chorus line is no longer cleared when the model is selected (its
kernel never reads a word the clear would have zeroed); DUST clears its delay memory with a fast loop (4x lighter
model-change block). The engines are dispatched through a tree of model tests. SISD only, DAG registers pushed through
R0, every shape from stock code inside functions (TERRAIN's kernel has local CALL / RTS subroutines, as core/dsp-inline
does); the four owner checks pass. On the emulator every changed model is bit-identical to H3o (kernel tests against
the C; core digests). Projected high bound for 8 tracks of one model (isolated core, worst block, cold caches): FORMANT
201 -> 109 %, NOISE 162 -> 93 %, 2FM 151 -> 94 %, ASYM-TRI 134 -> 95 %, VCF-VA 155 -> 106 %, TERRAIN 167 -> 110 %,
WAVETABLE 140 -> 108 %, DUST 280 -> 156 % (its model-change block; steady blocks about 120 %).

1. **Create a new project.** Flash H-opt7, power on, new project, PLAITS on track 1 (FUNC+SRC, 7 x DOWN, YES).
2. **Quick walk.** MODEL 0 to 23 with one trig each (note 60, velocity 100, default knobs, then HARMONICS, TIMBRE and
   MORPH right): every model sounds exactly as on H3o (18 to 20 stay silent). Listen closely to the changed ones:
   0, 1, 2, 3, 5, 7, 8, 9, 10, 16, 21, 22 and HIHAT (15). For STRING MACHINE (22) and DUST (10) switch to the model from
   another one and trig at once, several times: no click, no burst of old sound at the start (STRING MACHINE's chorus
   memory is not cleared any more; if that were wrong this is where it would be heard).
3. **8x each of the heaviest models.** The same model on tracks 1 to 8, 16th-note trigs on all eight, TUNE high and the
   knob that made it heaviest on H3o, for NOISE (9), 2FM (2), ASYM-TRI (1), FORMANT (3), VCF-VA (16), TERRAIN (21),
   WAVETABLE (5), then SPEECH (7), VA (0), SWARM (8), STRING MACHINE (22), DUST (10): for each, whether 8 play cleanly,
   and if not the highest track count that does (remove tracks one at a time). The projection says NOISE, 2FM and
   ASYM-TRI are the nearest to clean at 8 (93-95 %, choppy started between 87.5 and 101.3 % in O5's calibration).
4. **The mixes.** 4 KICK + 2 RINGS + 1 CHORD (choppy on fast3/fast4; those models are unchanged, so still choppy: a
   check of the calibration). Then 4 NOISE + 2 2FM + 2 KICK, 16th notes: report clean or choppy.
5. **Recovery.** MODEL 22, trig; save, reboot, load: STRING MACHINE plays.
- **H-opt7 hangs or clicks** (audio dies on the first trig of a model): recover (hold FUNC at power-on, TRIG 4, send
  the stock `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode), go back to H3o and report the model: each
  changed model points at its kernel.

## H3p: models 18, 19, 20 and 24, the DX7 (only after H3o passed)

H3p (`out/patch/plaits-25models-dx7-names-splash-48k-1.17.syx`, `b4bad7a7…`) is H3o plus the six-operator FM engine as four models: MODEL
18 `DX7-1`, 19 `DX7-2`, 20 `DX7-3` (Plaits' three built-in banks) and 24 `DX7-4` (the owner's bank, `*PPG*Vol.1` first); MODEL now goes to 24.
HARMONICS picks the patch (32 steps per bank), TIMBRE is brightness (the modulators' level), MORPH scales the envelope times. **The patch
name is NOT shown** on HARMONICS: the formatter cannot learn the selected track (finding 06, "PLAITS DX7"), so the table below gives the
patch and the HARMONICS value (UI 0 to 127) where it plays. Each DX7 voice has its own envelopes: a note keeps its level until the Digitakt's
note-off (an owner-approved exception to the note-on-only convention: the gate follows the note-off) and then goes through the patch's release.
A new patch plays a blank first block (upstream's guard): a note shorter than two blocks (1.3 ms) is nearly silent. DSP memory: the four banks
are 16,384 bytes of tables, a voice 2,740 bytes inside the union (17,120 per voice, 16 voices); section 7 is 716,784 of 1,048,575 bytes.

1. **Boot and a new project.** Flash H3p and power on (splash, then boot). Create a new project (an older project may not play). FUNC+SRC,
   7 x DOWN (PLAITS), YES on track 1. Make sure track 2 has a sample and plays.
2. **DX7-1..4 patch walks.** MODEL (B) to 18: the header reads `Model=DX7-1`. Trig note 60, then walk HARMONICS (C) from 0 to 127 in single
   steps, trigging at each: a different patch every 3 to 4 steps (basses, electric pianos, bells, strings, drums at the far end). Repeat for
   MODEL 19 (`DX7-2`), 20 (`DX7-3`) and 24 (`DX7-4`, the owner's bank). The patches by HARMONICS value (UI), names from the banks:

   | patch | HARM (UI) | DX7-1 (18) | DX7-2 (19) | DX7-3 (20) | DX7-4 (24) |
   |---|---|---|---|---|---|
   | 1 | 0-3 | SOLID BASS | E.PIANO 1 | CLICK 124 | *PPG*Vol.1 |
   | 2 | 4-7 | Mooger Low | FENDER 1 | *Hammond 1 | *PPG*Vol.2 |
   | 3 | 8-11 | LeaderTape | WINTRHODES | E.ORGAN 3 | *PPG*Vol.3 |
   | 4 | 12-15 | MORHOL TB1 | RS-EP C | 60-S ORGAN | *PPG*Vol.4 |
   | 5 | 16-19 | BASS    3 | *Mark III | OPTIC 28 | *PPG*Vol.5 |
   | 6 | 20-23 | BILL BASS | CLAV-E.PNO | PIPES   1 | *PPG*Vol.6 |
   | 7 | 24-27 | BASS    1 | SYN-CLAV | PIPES   3 | *PPG*Vol.7 |
   | 8 | 28-31 | ELEC BASS | CLAVINET | PIPES   2 | CHORUS   3 |
   | 9 | 32-35 | S.BAS 27.7 | PIANO   5 | JX-33-P | SYN.BASS 8 |
   | 10 | 36-38 | RESONANCES | GRD PIANO1 | SOUNDTRACK | SYN.HARMO. |
   | 11 | 39-42 | SYN-BASS 2 | STEINWAY | ICE PAD  2 | *m.Voice 3 |
   | 12 | 43-46 | PRC SYNTH1 | GUIT ACOUS | M1 PADS | *Belcanto |
   | 13 | 47-50 | CROMA 2 | SITAR | CARLOS   2 | *Cristals |
   | 14 | 51-54 | ANALOG  4 | KOTO | SOFT TOUCH | *syn.Vox 1 |
   | 15 | 55-58 | ANALOG A | HARPSICH 1 | *Planets | *syn.Vox 2 |
   | 16 | 59-62 | ANALOG  6 | CLAV    3 | CIRRUS | *Synclav.1 |
   | 17 | 63-66 | CS 80 | XYLOPHONE | ENTRIX | *Synclav.2 |
   | 18 | 67-70 | INSERT 1 | MARIMBA | MAL POLY | *Synclav.3 |
   | 19 | 71-73 | SPIRAL | VIBE    1 | Textures 6 | SYN.BRA. 3 |
   | 20 | 74-77 | DX-TROTT | GLOKENSPL | Etherial5a | *Vocoder 2 |
   | 21 | 78-81 | GASHAUS | BELL C | 'Airy' | *Vocoder 3 |
   | 22 | 82-85 | RING DING | BELLS | BORON A | *Sequence |
   | 23 | 86-89 | PAPAGAYO | TUB BELLS | VANGELIS 1 | *space Odd |
   | 24 | 90-93 | WINEGLASS | GONG    2 | STRINGS C | *Metalls 1 |
   | 25 | 94-97 | AMYTAL | KETTLE 6 | STRINGS 3 | *Metalls 2 |
   | 26 | 98-101 | FAIRLIGHT | MID DRM 3 | STRINGS 2 | *Fairl. 2 |
   | 27 | 102-105 | *PPG*Vol.1 | ORI DRUM 1 | STRINGS 7 | *Fairl. 3 |
   | 28 | 106-108 | *PPG*Vol.2 | WOOD 6 | FULL STRIN | Mooger Low |
   | 29 | 109-112 | *Fairl. 3 | LATN DRM 4 | SYN-ORCH | *Floating |
   | 30 | 113-116 | *Vocoder 2 | CIMBAL | BRASS   1 | *Gomono |
   | 31 | 117-120 | *Sequence | SYNDM 25.8 | BRASS 6 BC | MUTE BASS |
   | 32 | 121-127 | Bounce 4 | B.DRM-SNAR | BR TRUMPET | *syn.Orga |

   UI 35 sits on the boundary of patches 9 and 10 and may play either, depending on the direction you came from.
3. **Held notes release on note-off.** On DX7-1 with HARMONICS about 55 (`ANALOG A`), hold TRIG 1 for 2 s and let go: the sound holds while the key is
   down and goes into its release when you let go (the amp envelope shapes the level on top). With a sequencer note (step length 1/4 note) the same:
   the sound ends with the step's note length, not at the next note. TIMBRE (E) to the left darkens the patch, to the right brightens it; MORPH (F)
   changes how fast the envelopes run (left slower attack and decay, right faster).
4. **Fast retriggers.** Trig a step of 1/16 or 1/32 notes at 150 BPM on a pad patch (DX7-3, HARM about 70): every trig plays, the previous note's tail
   rings under the next (two internal voices alternate), no runaway, no crackle. Also hold one trig and tap another: the second starts a new note.
5. **Track rule (unchanged): never more than 1 track triggered as PLAITS since boot,** any model. One track DX7 at its worst knobs measures 63.1 %
   of the DSP block at the high bound (note 127, HARM max, TIMB 0x37e4, MORPH max; fuzz worst 96,314 instructions; two tracks, extrapolated: about 82 %,
   not measured; finding 06, "PLAITS DX7").
6. **Recovery check.** MODEL back to 0 (VA) and trig: VA plays as on H3o. Save the project with MODEL 24 on track 1, reboot, load it: MODEL is back
   at 24 and the owner's bank plays. Change between DX7-1 and the other models a few times: no dropped notes.
- **H3p stalls or is silent** while H3o passed: recover (hold FUNC at power-on, TRIG 4, send the stock `firmware/Digitakt_II_OS1.17.syx` with Transfer's
  legacy mode over MIDI DIN), go back to H3o and report which step failed. If the DX7 sounds but never releases (or releases at once), report it with
  the step: the note-off source is the DSP's per-voice release flag (finding 06).

## H3q: SRC+YES randomize, one SRC page, the MODEL bar, the MODEL order (only after H3p passed)

H3q (`out/patch/plaits-25models-rand-names-splash-48k-1.17.syx`, `78d4fd39…`) is H3p plus four UI changes, all in core/params and core/machines (ColdFire
only; the DSP changes only by the knob-to-engine map in the entry). Its four owner checks (`elektron-firmware-tool -i`, `sharc_bootcheck`,
`pk_encoding_gate`, sections 2, 4, 5 and 8 identical to stock) are in the report of lane rand.

**MODEL order changed:** the knob now runs 0 VA ... 18 DX7-1, 19 DX7-2, 20 DX7-3, **21 DX7-4, 22 TERRAIN, 23 STRING MACH, 24 SQUARE**
(before: 21 TERRAIN, 22 STRING MACH, 23 SQUARE, 24 DX7-4). **A project saved under an earlier image reads MODEL 21 to 24 as other models**: create a new
project after every flash, as always.

1. **Boot and a new project.** Flash H3q, power on, create a new project. FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1. Make sure track 2
   (or any other track) has a sample.
2. **MODEL order and names.** Turn MODEL (B) from 0 upward: the header names follow the list above, and each knob position is the model named:
   21 DX7-4 (the owner's bank), 22 TERRAIN (wide pitched sweeps), 23 STRING MACH, 24 SQUARE (chiptune arpeggio). 25 models, no gaps, nothing silent.
3. **MODEL bar.** At the highest MODEL (24 SQUARE) the knob bar is full; at 12 about half; HARMONICS, TIMBRE and MORPH
   bars are full at their maxima, as before.
4. **One SRC page.** On the PLAITS track press SRC, then SRC again, and again: the page does not change (no second page of small bars).
5. **SRC+YES on PLAITS.** Hold SRC and press YES (header `Randomize Page SRC`): MODEL, HARMONICS, TIMBRE and MORPH change; TUNE, MIX
   and LEV do not. Press it ten times: MODEL lands on many of the 25 models (never on a gap or silence), the other three move over their whole range.
   Undo is as for a stock page (check UNDO if you use it); a p-locked value on a trig is randomized in the same way as a stock parameter's.
6. **SRC+YES on stock machines is unchanged.** On a track with a SAMPLE machine press SRC twice (two pages as before) and
   SRC+YES on each: the stock parameters change as before. Same on a SLICED SMP track.
7. **Recovery check.** Select PLAITS, change MODEL, save the project, reboot, load: MODEL and the knob order are as left.
- **H3q stalls, the screen is wrong or SRC+YES crashes** while H3p passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3p
  (`plaits-25models-dx7-names-splash-48k-1.17.syx`, `b4bad7a7…`) and report which step failed.

## H3r: DX7 patch names on HARMONICS (only after H3q passed)

H3r (`out/patch/plaits-25models-dx7names-splash-48k-1.17.syx`, `1bfd3f81…`) is H3q plus these changes. **The DX7 patch is now a fixed function of
HARMONICS** (no hysteresis; a deliberate deviation from upstream, owner decision 2026-10-23: PORTING drift 28), and **the knob-turn header of
HARMONICS shows the patch name** (`Harmonics=BASS    1`, trailing spaces trimmed) when MODEL is 18 DX7-1, 19 DX7-2, 20 DX7-3 or 21 DX7-4
(banks 1 to 4). Every other model, and every other parameter, shows the number as before. The inline label under the knob shows a 6-character short name (step 5 below), also while a trig is held. Name and sound come from one generated table. Pinned digests of all models are unchanged.

1. **Boot and a new project.** Flash H3r, power on, create a new project. FUNC+SRC, 7 x DOWN (PLAITS), YES on track 1; track 2 with a sample.
2. **DX7 names match the sound.** MODEL (B) to 18. Press SRC, turn HARMONICS (C) slowly: the header reads the name; trig note 60 after each
   step. Step slowly across each boundary of the table below (for example UI 35 to 36, 31 to 32) **upward and downward**: the name changes at
   the same UI value in both directions and the sound is that patch (the old direction-dependent boundary is gone). Repeat for 19, 20, 21.

   | patch | HARM (UI) | DX7-1 (18) | DX7-2 (19) | DX7-3 (20) | DX7-4 (21) |
   |---|---|---|---|---|---|
   | 1 | 0-3 | SOLID BASS | E.PIANO 1 | CLICK 124 | *PPG*Vol.1 |
   | 2 | 4-7 | Mooger Low | FENDER 1 | *Hammond 1 | *PPG*Vol.2 |
   | 3 | 8-11 | LeaderTape | WINTRHODES | E.ORGAN 3 | *PPG*Vol.3 |
   | 4 | 12-15 | MORHOL TB1 | RS-EP C | 60-S ORGAN | *PPG*Vol.4 |
   | 5 | 16-19 | BASS    3 | *Mark III | OPTIC 28 | *PPG*Vol.5 |
   | 6 | 20-23 | BILL BASS | CLAV-E.PNO | PIPES   1 | *PPG*Vol.6 |
   | 7 | 24-27 | BASS    1 | SYN-CLAV | PIPES   3 | *PPG*Vol.7 |
   | 8 | 28-31 | ELEC BASS | CLAVINET | PIPES   2 | CHORUS   3 |
   | 9 | 32-35 | S.BAS 27.7 | PIANO   5 | JX-33-P | SYN.BASS 8 |
   | 10 | 36-38 | RESONANCES | GRD PIANO1 | SOUNDTRACK | SYN.HARMO. |
   | 11 | 39-42 | SYN-BASS 2 | STEINWAY | ICE PAD  2 | *m.Voice 3 |
   | 12 | 43-46 | PRC SYNTH1 | GUIT ACOUS | M1 PADS | *Belcanto |
   | 13 | 47-50 | CROMA 2 | SITAR | CARLOS   2 | *Cristals |
   | 14 | 51-54 | ANALOG  4 | KOTO | SOFT TOUCH | *syn.Vox 1 |
   | 15 | 55-58 | ANALOG A | HARPSICH 1 | *Planets | *syn.Vox 2 |
   | 16 | 59-62 | ANALOG  6 | CLAV    3 | CIRRUS | *Synclav.1 |
   | 17 | 63-66 | CS 80 | XYLOPHONE | ENTRIX | *Synclav.2 |
   | 18 | 67-70 | INSERT 1 | MARIMBA | MAL POLY | *Synclav.3 |
   | 19 | 71-73 | SPIRAL | VIBE    1 | Textures 6 | SYN.BRA. 3 |
   | 20 | 74-77 | DX-TROTT | GLOKENSPL | Etherial5a | *Vocoder 2 |
   | 21 | 78-81 | GASHAUS | BELL C | 'Airy' | *Vocoder 3 |
   | 22 | 82-85 | RING DING | BELLS | BORON A | *Sequence |
   | 23 | 86-89 | PAPAGAYO | TUB BELLS | VANGELIS 1 | *space Odd |
   | 24 | 90-93 | WINEGLASS | GONG    2 | STRINGS C | *Metalls 1 |
   | 25 | 94-97 | AMYTAL | KETTLE 6 | STRINGS 3 | *Metalls 2 |
   | 26 | 98-101 | FAIRLIGHT | MID DRM 3 | STRINGS 2 | *Fairl. 2 |
   | 27 | 102-105 | *PPG*Vol.1 | ORI DRUM 1 | STRINGS 7 | *Fairl. 3 |
   | 28 | 106-108 | *PPG*Vol.2 | WOOD 6 | FULL STRIN | Mooger Low |
   | 29 | 109-112 | *Fairl. 3 | LATN DRM 4 | SYN-ORCH | *Floating |
   | 30 | 113-116 | *Vocoder 2 | CIMBAL | BRASS   1 | *Gomono |
   | 31 | 117-120 | *Sequence | SYNDM 25.8 | BRASS 6 BC | MUTE BASS |
   | 32 | 121-127 | Bounce 4 | B.DRM-SNAR | BR TRUMPET | *syn.Orga |

3. **Non-DX7 models show numbers.** MODEL 0, 17, 22, 23, 24: turning HARMONICS shows `Harmonics=NN` (0 to 127), as before.
4. **MODEL changes the display.** On a DX7 model turn MODEL to the next DX7 model, then nudge HARMONICS: the name is the new bank's patch.
5. **The inline label (N2).** The value label under the HARMONICS knob on the SRC page shows the **short** patch name (at most 6 characters: the whole
   name when it fits, else the first characters with a trailing number kept, `BASS    3` -> `BASS3`, `*PPG*Vol.3` -> `*PPG*3`) for a DX7 model, a
   number otherwise. The labels show values only after a knob touch (idle they show the parameter names). The header keeps the full name.
6. **P-lock editing.** With grid recording on (RECORD) and trigs on some steps, hold a trig and turn HARMONICS: the label under HARMONICS names the patch
   of the *effective* model: the step's MODEL p-lock when it has one (hold the trig, turn MODEL: a DX7 bank gives that bank's names, any other model
   the number), else the track's MODEL. The held step's sound is that patch. Check a step locked to a different DX7 bank and a step locked to a non-DX7 model.
   (The knob-turn header is not drawn while a trig is held; only the label.)
7. **Recovery check.** Save the project with MODEL on DX7-2, reboot, load: MODEL and the names are as left.
- **H3r stalls, shows garbage text or a wrong name** while H3q passed: recover (hold FUNC at power-on, TRIG 4, send the stock
  `firmware/Digitakt_II_OS1.17.syx` with Transfer's legacy mode over MIDI DIN), go back to H3q (`78d4fd39…`) and report the step.

## Known, not faults

- A triggered PLAITS voice keeps rendering until its track is triggered under another machine; the amp envelope
  silences it. That is why the track limits above count tracks triggered as PLAITS since boot.
- A project saved with PLAITS loads on stock 1.17 without a fault, with that track as ONESHOT (type 0): the stock
  loader keeps only the stock machine types.
- The track limits come from the DSP load measured in the emulator with the manual's stall rules (finding 06,
  "PLAITS: measured DSP load per block" and the later model sections), kept at or under 85 % of the block at the
  worst-case parameters: 2 tracks on H2 and H3, 1 track on H3b, H3c, H3d, H3e (ADDITIVE sets it), H3f (RINGS, TERRAIN and ADDITIVE), H3g (the drums are under RINGS), H3h (NOISE is under RINGS) H3i (PHASE-DIST is under RINGS) H3j (VCF-VA is under RINGS) H3k (SQUARE is under RINGS) and H3l (STRING MACHINE is under RINGS, if its measured load stays below it) and H3m (DUST, 63.2 % at 1 track, is under RINGS) and H3n (SWARM, 54.4 % at 1 track, is under RINGS) and H3o (STRING, 61.3 % at 1 track) and H3p (DX7, 63.1 % at 1 track, about 82 % at 2 tracks, extrapolated).

## H-opt8: STRING and DX7 kernels

H-opt8 (`out/patch/plaits-25models-fast6-names-splash-48k-1.17.syx`, `aa8f314c…`) is the opt-o6 image plus the O7
kernels: STRING (11) runs its sample loop and excitation in `lib/string_sm.s` and no longer clears its delay lines
at a note-on that selects it; DX7 (18, 19, 20, 24) runs its operator loop in `lib/dx_sm.s`. Both are bit-identical to
the C on the concrete core and in the compiled-core digests. Projected 8-track load (isolated core, cold): STRING
235 % -> 119 %, DX7 236 % -> 161 % (OPTIMIZING.md, 2026-10-23); neither reaches 8 clean tracks.

1. Load H-opt8, new project, track 1 PLAITS on STRING. Play notes across the range with HARMONICS at 0, 0.25 and 1
   (curved bridge, plain, dispersion), TIMBRE and MORPH swept, MORPH at the top (infinite decay). Expected: as H-opt7,
   no click at the first note after selecting STRING (the lines start silent), no DSP stall.
2. Switch MODEL between STRING and another model with notes in between, several times. Expected: STRING starts
   clean each time.
3. DX7-1..4: play each bank's patches (HARMONICS sweep) with held notes and releases. Expected: as H3p.
4. Three tracks of STRING and three of DX7 on one step. Expected: no dropouts; report the first track count that
   crackles if any.

## H-opt9: DX7 kernels and the shared wrapper

H-opt9 (`out/patch/plaits-25models-fast7-names-splash-48k-1.17.syx`, `b9a10e23…`) is H-opt8 plus the O8 work: DX7
(18, 19, 20, 24) renders its voice, unpacks patches and sets its envelopes in `lib/dx_sm.s`; every model's per-block
glue (plaits_render, render_engine, render_sub) runs in `wrap_sm.s`. All bit-identical to the C on the concrete core.
Projected 8-track load (isolated core, cold / 7 of 8 warm): DX7 161 / 144 % -> 103 / 86 %; the other models 1-5 pp
lower (OPTIMIZING.md, 2026-10-23).

1. Load H-opt9, new project, track 1 PLAITS on DX7-1. Play each bank's patches (HARMONICS sweep, DX7-1..4) with held
   notes and releases, MORPH and TIMBRE swept. Expected: as H3p (sound, note-off releases, patch names).
2. Switch MODEL across several models (VA, CHORD, SWARM, DX7-2, KICK) with notes in between. Expected: each plays as
   on H-opt8; SWARM keeps its continuous grains; DX7 releases at note-off.
3. Six DX7 tracks on one step, then eight. Expected: no dropouts at six; report the first track count that crackles.
4. A stereo sample (ONESHOT) on a neighbouring track alongside four PLAITS tracks. Expected: it plays normally.

## Release checklist (final PLAITS image)

Image: the release candidate built from plaits/design (see the image table). Create a new project after flashing.

1. The unit boots; the splash shows.
2. A walk through all 25 models (MODEL knob order: ... DX7-1, DX7-2, DX7-3, DX7-4, TERRAIN, STRING MACHINE, SQUARE).
   Each sounds; the MODEL bar reaches the top at SQUARE.
3. DX7: HARMONICS names in the knob header and the inline label, including while holding a trig (RECORD on) on a step
   whose MODEL is p-locked to another DX7 bank or to a non-DX7 model (shows the number). Held notes release on note-off.
4. SRC+YES on a PLAITS track changes MODEL, HARMONICS, TIMBRE and MORPH only; on a stock machine as before.
5. Pressing SRC repeatedly stays on the one PLAITS page.
6. A stereo sample on a Oneshot track next to several PLAITS tracks keeps its stereo image (no cross-track corruption).
7. Multi-track: see "What fits" below; e.g. 4 KICK + 2 RINGS + CHORD should now be clean.
8. Recovery: hold FUNC at power-on, press TRIG 4, and use Transfer's legacy mode over MIDI DIN.

## What fits (calibrated estimate)

The emulator's high-bound projection of 8 tracks of one model, with warm caches (the projection closest to the unit:
8 KICK measured 91.7 % on the unit's calibration path), the per-voice share over the stock baseline (48.6 %), and the
number of tracks of that model expected to stay clean (≤ about 87 %, the calibrated choppy threshold). Mixed kits add
up per voice. DUST's figure is its model-change block; its steady state is lower. Estimates, not measurements.

| Model | 8 tracks, projected (warm) | Per voice | Tracks that fit clean (est.) |
|---|---|---|---|
| SQUARE | 77 % | 3.5 % | 10 |
| ASYM-TRI | 80 % | 3.9 % | 9 |
| 2FM | 82 % | 4.2 % | 9 |
| NOISE | 83 % | 4.3 % | 8 |
| KICK | 85 % | 4.5 % | 8 |
| DX7-1 | 86 % | 4.7 % | 8 |
| DX7-2 | 85 % | 4.5 % | 8 |
| DX7-4 | 84 % | 4.4 % | 8 |
| ADDITIVE | 88 % | 4.9 % | 7 |
| RINGS | 90 % | 5.2 % | 7 |
| HIHAT | 90 % | 5.2 % | 7 |
| DX7-3 | 88 % | 4.9 % | 7 |
| FORMANT | 99 % | 6.3 % | 6 |
| WAVETABLE | 93 % | 5.5 % | 6 |
| CHORD | 99 % | 6.3 % | 6 |
| SPEECH | 97 % | 6.0 % | 6 |
| SNARE | 93 % | 5.5 % | 6 |
| VCF-VA | 94 % | 5.7 % | 6 |
| PHASE-DIST | 94 % | 5.7 % | 6 |
| TERRAIN | 98 % | 6.2 % | 6 |
| VA | 103 % | 6.8 % | 5 |
| SWARM | 105 % | 7.0 % | 5 |
| STRING | 102 % | 6.7 % | 5 |
| STRING MACH | 113 % | 8.1 % | 4 |
| DUST | 128 % | 9.9 % | 3 |
