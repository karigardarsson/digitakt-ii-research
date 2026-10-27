# Private candidate source and provenance

digikit by Em D - https://github.com/ElekLabs/digikit

This modified firmware is derived from DigiKit. It is not the original DigiKit
and is not endorsed by Em D or Elektron. Original NOTICE and AGPL-3.0-or-later
LICENSE are preserved verbatim. These licenses grant no rights to Elektron OS
images. This package is a local experimental handoff, not a public release.

## Exact chain

- Official DT2 OS1.17 input SHA256:
  `26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`.
- Upstream DigiKit source pin:
  `e48d0a2a83f055c549391173247cea02c571927f`.
- Current integration checkout: `dv-offline-v1`, branch `dt2-dv-offline-v1`,
  checkpoint `f9db0e8b03c2142b167a50b66ae211fea46be481` plus this local UI delta.
- Immutable FANG/panel input:
  `32de4571df62b4baa6012afc33396a5d3166519cda124383866ea6acb66c92a9`.
- Credits candidate:
  `bbc534fae45fd4044625edba07c12d4be8b24d8997ccab313e6af0dfccd43626`.

`build.py` freshly compiles credits.c/hook.s with m68k-elf GCC16.2.0,
Binutils2.47; no DSP compilation occurs. It composes against the exact private
input, checks placement/alignment, preserves intro bytes, repackages MAIN and
verifies decoded sections, HMAC/checksums and bootstrap. `verify.py` recompiles
and byte-matches the packaged UI segment and executes the actual final image.
The package manifest and retained build/final-gate reports bind these inputs.

## Source contents and limits

`source/DT2-All-in-One-Corresponding-Source-r2.zip` is the previously preserved
original source/dependency/notices archive, unchanged. It documents its own
older public builder, including the old startup-credits implementation; that
builder is **not** used to build this newer private candidate.

`source/Current-Integration-Source-Overlay.zip` retains current Credits, FANG,
TWIN/FUSE/FUS2, Déjà Vu/Musical WAVE/Zoom integration sources, relevant host
composition helpers and module manifests. Source-only export excludes images,
snapshots, sample audio, generated executable payloads and vendor toolchains.
NOTICE/LICENSE and third-party notices remain with the archives. Existing
baseline build records are preserved in `evidence/`; do not relabel their old
hardware/PCM results as newly executed tests.

This UI builder intentionally consumes the preserved private input image. The
older full stock-to-current integration chain remains in its original local
build scripts/checkpoints. A fresh clean-room reconstruction of that entire
chain was not performed for this UI-only task. The current Credits payload
was rebuilt and matched to the final firmware; all older DSP/MAIN payloads
were byte-compared to the exact private input, not rebuilt speculatively.

No stock image is included in either source archive. The sole intended SYX is
under FLASH_THIS. Do not upload or redistribute Elektron firmware. Previously
disclosed speech-table/DX7-bank provenance and dependency licensing limits
remain unchanged; see the original source archive's third-party notices.

## Evidence and acceptance

Nine native YES/open → NO/back/cleanup cycles, all three guarded framebuffers,
unchanged stock System MenuItems and seven existing UI/DV/Musical WAVE/Zoom/
Sound-serialization tests pass. Every non-MAIN section, including DSP BLOB7,
is identical to the input. Existing MAIN differs only at the six-byte System
constructor tail; one1296B/BSS0 UI segment is appended. Physical testing and
full coupled cold-boot timing remain unverified. The five-second difference
is150 frames at30fps relative to the old public credits build; private input
to this candidate has zero startup-duration change.

On the instrument: SETTINGS > SYSTEM > CREDITS / ABOUT > YES. Read page1's
wrapped attribution and URL; UP/DOWN changes pages, NO returns to SYSTEM.
Repeat, check other System entries, and confirm ordinary intro without an
extra attribution screen. Smoke-test normal SRC pages and existing sounds.
No device was flashed, no repository pushed, and no public assets changed.
