# Digitakt II All-in-One v0.3.0 — community beta

Unofficial OS1.17 modification: 25 optimized Plaits selections, four DX7 banks, Kári's rectangular UI2.1, Musical WAVE V4/Déjà Vu, One-Shot Zoom V11 and the original Elektron splash.

The builder displays DigiKit attribution. Generated firmware preserves the original Elektron animation and then shows a five-second credits panel with origin, copyright, AGPL/no-warranty and source/license information. This is the only firmware change since the DX7 upper-label fix. The developer console-boot flag skips the intro task, as in stock.

[**Practical HOW TO: Plaits, Musical WAVE, Déjà Vu and Zoom/Pan**](DIGITAKT_II_ALL_IN_ONE_USER_GUIDE.md) · [Installation](INSTALLATION.md) · [Recovery](RECOVERY.md) · [Windows download](https://github.com/karigardarsson/digitakt-ii-research/releases/download/v0.3.0-beta/DT2-All-in-One-v0.3.0-beta-Windows-r2.zip)

Windows presentation revision r2 updates the title, feature list, ready status and credits, and handles invalid inputs correctly. Generated firmware is unchanged.

## Build your own firmware

Windows x64. Extract the whole package to a normal local folder. Start `DT2-All-in-One-Builder.exe`, choose **your own official Digitakt II OS1.17 `.syx`**, choose a new output filename, then Build. No Python, Rust or WSL is needed to use the packaged application. It works offline, creates a file only, and never connects to or flashes a device. The executable is unsigned; Windows may show a SmartScreen warning. Do not disable security software or run untrusted copies.

Only this official input is accepted:
`26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`

Expected generated firmware SHA-256:
`426dd1be5be3d356b5fa26900c729baf555cc16393611c4e0544ebd2d95eec76`

Obtain the official image from Elektron; it is not included here. Modified inputs, other OS versions, corrupt resources, unexpected results and overwriting an existing file are refused. Do not upload or redistribute the generated `.syx`: it contains proprietary Elektron firmware.

## Features and controls

- One PLAITS machine. MODEL0–24: VA, ASYM-TRI, 2FM, FORMANT, ADDITIVE, WAVETABLE, CHORD, SPEECH, SWARM, NOISE, DUST, STRING, RINGS, KICK, SNARE, HIHAT, VCF-VA, PHASE-DIST, DX7-1, DX7-2, DX7-3, DX7-4, TERRAIN, STRING MACHINE, SQUARE.
- DX7 MODEL selects one of four banks; HARM selects one of32 patches. The lower native label names the patch; the upper graphical box shows the numeric HARM value. Native note-off support retained.
- Musical WAVE: stock0–6, DV-F7, DV-T8; PMAJ9, PMIN10, HIRA11, WTON12, DORI13, LYDI14, OCTA15. Scale waves quantize SAMPLE TUNE; other destinations retain DV-F behavior. Existing LFO controls retain their meanings.
- ONESHOT waveform Zoom: press encoder C to cycle the existing zoom levels; turn C to pan when zoomed. No new audio/DSP behavior in this release-preparation batch.

## Installation and rollback

Back up projects, Sounds and samples first. Keep official stock OS1.17, Elektron Transfer and a working physical MIDI-DIN interface available **before** experimenting. Building a file is not authorization to flash it.

For a deliberate installation, follow Elektron's normal OS-update instructions in its Digitakt II manual/Transfer software; confirm the generated file's hash first. Previous Plaits builds sometimes failed normal Transfer updates, including 0%/PREPARING stalls. Do not assume that route is reliable on every beta installation.

Recovery uses the startup menu: computer MIDI OUT → Digitakt II MIDI IN; hold FUNC while powering on, choose TRIG4 OS UPGRADE, then Transfer's SYSEX TRANSFER → OS upgrade via device startup menu and send the **official stock** image. Startup-menu recovery uses physical MIDI DIN, not USB. Do not choose reset/format options as part of this procedure.

Sources: [Elektron startup-menu manual](https://www.elektron.se/wp-content/uploads/2024/10/Digitakt-2-User-Manual_ENG_OS1.03_241018.pdf), [Elektron SysEx-transfer guidance](https://support.elektron.se/support/solutions/articles/43000662701-how-to-update-your-device-via-sysex-transfer). Follow the current instructions shown by Transfer. No update or recovery is automated by this builder.

## Validation and known issues

- **Recurring Plaits trigger click remains.** It can affect all models. The cause is unresolved; this release does not claim to fix it.
- Kári confirmed boot, audible Plaits/model selection and the DX7 interface on the preceding `8fb583c463…` build. The exact `b9ee18b521…` upper-label correction has passed offline/native UI, integration, binary-delta, HMAC, encoding and SHARC boot checks; it is **not independently confirmed hardware-tested**.
- The release's exact `426dd1be5b…` image adds credits only and is **not hardware-tested**. All five original intro callbacks retain their original frame sequence/duration before credits. The credits callback passed 1,635 bounded frame/ABI tests. DSP and all non-MAIN sections are identical to b9ee.
- The Windows builder must reproduce exactly the release hash. Builder tests are not additional hardware audio proof. Prior stock SAMPLE PCM parity is not exhaustive Drive+/sample-loading or whole-project filesystem proof.
- DSP load depends on engine, controls, effects and track count. Start with one Plaits track. There is no16-track guarantee. Upstream calibrated estimates are not measurements of this combined firmware on your unit. AMP silence alone may not stop the synth's DSP workload.
- Old14-model Plaits MODEL values/p-lock mappings are incompatible. Use backed-up projects or new Sounds. Existing25-model mapping is unchanged by this UI fix.
- Normal Transfer reliability, exhaustive save/reload, every p-lock/modulation combination and long-session stability are not guaranteed.

## Community policy

Experimental community beta, provided **as-is**, without warranty. No promised update schedule, guaranteed support or future OS compatibility. Use [GitHub Issues](https://github.com/karigardarsson/digitakt-ii-research/issues) for reproducible bugs and community discussion . Include builder version, input/output hashes, model/controls, exact steps and expected/actual behavior; **do not attach Elektron firmware, private projects or personal data**. Community contributions are welcome. Requests do not create a commitment to implement features.

## Source, licenses and attribution

This is a modified work derived from **digikit by Em D - https://github.com/ElekLabs/digikit**. Optimized Plaits integration: Tyler Neely/spacejam, pinned `e48d0a2a83f055c549391173247cea02c571927f`. Original Plaits/stmlib algorithms and tables: Emilie Gillet, MIT. Graphical integration, Musical WAVE and Zoom: Kári's DT2 work, supplied in this community edition under AGPL-3.0-or-later without retroactive relicensing of earlier assets. Original MIT notices are retained; DigiKit's AGPL license and additional NOTICE terms are included. This does not grant rights to Elektron firmware or trademarks and implies no endorsement.

`DT2-All-in-One-Corresponding-Source-r2.zip` accompanies the application, including exact pinned DigiKit source, local graphics/integration, preferred DV/Zoom sources, source-only V5.1 reconstruction, native-tool source and the27 linked Rust dependency sources. See `SOURCE.md`, `THIRD_PARTY_NOTICES.md` and `licenses/`. Attribution alone is not the source offer: keep the corresponding-source archive and notices available alongside every distributed binary. No future feature/support promise is required.

Provenance disclosure: upstream speech tables disclose TI-ROM-derived word data, and the fourth DX7 bank is the upstream owner's curated bank; those provenance facts are not an independent third-party rights clearance. Both the builder and generated firmware now display DigiKit attribution. Source, full license and NOTICE accompany the package. No Elektron redistribution rights or endorsement are implied.
