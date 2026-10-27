# Candidate/source validation — 2026-10-27

Candidate SHA-256:
`bbc534fae45fd4044625edba07c12d4be8b24d8997ccab313e6af0dfccd43626`.

## Identity and source binding

The actual retained candidate, packaged copy, Credits build/final-gate records
and source file hashes were checked, not inferred from filenames. Credits
`credits.c`/`hook.s` and all other current CF/DSP implementation inputs are
published without musical code edits. Original/published per-file hashes are
in SOURCE_MANIFEST.json. Only generated absolute-path manifests are normalized.

One inherited source-list discrepancy was resolved in the **published catalog**:
TWIN's retained CF object was made from generated
`twinshot_fusion_v2/build/transport.s`, but its old manifest still named the
pre-generation `twinshot/controls.s` template. The frozen generated assembly
exactly matches the object's compiler source hash and is included. The public
catalog names that real input; no firmware byte was changed. Every DT2 CF
object's concatenated input hash was checked against its object metadata.
Object bytes themselves are not published.

The Credits payload was freshly compiled and byte-matched to the final image
by the retained final gate. All existing MAIN bytes/loader segments except the
six-byte System-constructor tail are unchanged from the exact FANG input.
The appended Credits segment is1296 bytes, BSS0. All non-MAIN sections, including
DSP section7, are byte-identical. Stock intro callbacks/durations are exact;
there are no additional startup frames. This is not a wall-clock measurement.

## Executed evidence, not hardware claims

- Nine native YES/open → NO/back/cleanup cycles; three readable wrapped pages;
  retained native menu items, registers, stack and bitmap/object guards pass.
- Seven existing integration/UI/Musical WAVE/Zoom tests pass.
- FANG:35 native blocks match the host oracle,81 host extremes finite, eight
  controls/editing, modulation and Sound/Pattern converters pass. Maximum
  measured28,322 interpreted instructions/block and248/4096-byte stack usage
  are emulator measurements, not silicon utilization/headroom.
- Existing Plaits25 and stock SAMPLE PCM/ABI regressions, FUS2 Repair4 and
  graphics are preserved through recorded executable/data equality; old
  evidence is not relabeled as newly run hardware tests.
- SRC test on this exact image: five full SRC press/releases keep FANG at0;
  SAMPLE and FUS2 positive controls alternate1/0/1/0/1. Explicit page requests
  1/2/-1 clamp to0. Eight encoder routes, RES modulation and Sound/Pattern
  converter round-trips pass. FANG already had one accessible SRC page; no
  corrective firmware change was made.
- Container/HMAC, encoding and SHARC boot checks pass in retained reports;
  unchanged DSP evidence is reused only through exact section equality.

## Still untested / unchanged

Physical menu/boot timing, full filesystem Project/Song behavior, silicon load
limits and exact candidate hardware audio acceptance remain unverified. The
trigger click and ordinary updater regressions remain parked, not solved by
this work. CHIMERA is not included; type15 is a reservation only.

The public v0.3.0 builder/release assets are unchanged. This publication adds
source and provenance, not a new consumer builder or firmware release. No
clean-room stock-to-candidate rebuild was undertaken during the source push.
