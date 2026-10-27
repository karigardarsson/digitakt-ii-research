# On-device Credits (OS1.17, private All-in-One)

Scoped replacement for the public beta's five-second startup attribution.
The current FANG private baseline already has stock intro timing. Its missing
piece is accessible on-device attribution, not a second startup timing patch.

Architecture: append CREDITS / ABOUT to the native SystemSettings menu after
all stock items. Reuse native VerticalMenuView construction, input, shared
ownership and submenu close callbacks; only the Credits draw method changes.
Three full-width pages avoid clipping attribution into narrow menu columns.
No DSP, settings, stored Sounds, native item replacement or hidden gesture.

Evidence gate: native System constructor runs in the existing OS1.17 fixture;
0x4010a67e restores saved registers, providing a six-byte tail trampoline.
Native 0x4011a0ba creates shared View ownership and child close callback;
0x4011a592 records the submenu and shows it. These contracts require final-byte
execution tests before delivery. UI and hardware evidence are separate.

Base: f9db0e8 / 32de4571df62b4baa6012afc33396a5d3166519cda124383866ea6acb66c92a9.
This does not merge the separate ARP experiments or reserved CHIMERA engine.
Keep original DigiKit NOTICE and LICENSE with source and every package.
New glue is AGPL-3.0-or-later, with DigiKit's NOTICE conditions preserved.
No Elektron firmware bytes belong in Git; no endorsement is claimed.

digikit by Em D - https://github.com/ElekLabs/digikit

This is modified firmware derived from DigiKit, not the original project.
Neither Em D nor Elektron endorses this private experiment. Original NOTICE
and AGPL-3.0-or-later LICENSE are retained verbatim beside this builder.

## On the instrument

Open SETTINGS, select SYSTEM, then CREDITS / ABOUT and press YES. Use UP/DOWN
for the three pages; NO returns to SYSTEM. All original System entries remain.
Page 1 shows attribution and the original URL, page 2 copyright/license/no
warranty/conveyance, page 3 source/license availability and non-endorsement.

The public beta added 150 frames at 30 fps (five seconds) to each intro
variant. This composition preserves the stock intro callbacks and durations
byte-for-byte. Relative to the current private FANG baseline there is **zero**
startup timing change; it already had stock timing. This is not a new DSP or
musical feature and is not a rewrite of the public Windows builder.

## Reproduce and validate locally

Use the existing `spacejam-hopt2-playable/.venv/bin/python` (Python3.12), with
its pinned e48 source, source/tests and host directory on PYTHONPATH. Execute
`build.py --compile-only`, then `test_credits.py` for the bounded prototype.
`build.py` creates one new image only when its output does not exist; it
refuses a wrong baseline hash and does not overwrite a previous artifact.

For final-image execution set AIO25_CANDIDATE to the generated image,
AIO25_REPORT to `out/on-device-credits-work/build.json`, and CREDITS_FINAL=1;
then run `test_credits.py` and the existing `test_all_in_one25.py` and
`test_all_in_one25_musical.py` suites. Do not run prototype overlays in place
of this final gate. `build.json` binds fresh linked code and source hashes to
the actual decompressed image; `linked.json` is an intermediate only.

No broader cold-boot, device audio, filesystem or hardware claim follows from
the cached-state native UI tests. Existing baseline evidence is reused only
where final executable bytes are identical; physical acceptance is pending.
