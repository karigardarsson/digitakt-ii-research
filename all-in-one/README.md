# Current All-in-One source checkpoint — DT2 OS 1.17

**Source publication, not a new release or hardware-PASS claim.** The existing
v0.3.0 Windows downloads and release assets are unchanged.

Candidate identity (the firmware itself is deliberately not included):

```text
bbc534fae45fd4044625edba07c12d4be8b24d8997ccab313e6af0dfccd43626
```

This is modified firmware derived from
**digikit by Em D - https://github.com/ElekLabs/digikit**.
It is not the original DigiKit and is not endorsed by Em D or Elektron.
Read [NOTICE](NOTICE), [AGPL-3.0-or-later LICENSE](LICENSE) and
[third-party credits](THIRD_PARTY_NOTICES.md). The repository's older root MIT
license does not replace the licenses of this subtree or its dependencies.

## Included implementation

- Native **SETTINGS > SYSTEM > CREDITS / ABOUT**, using YES, UP/DOWN pages and
  NO/back. The complete DigiKit attribution/URL is wrapped, not truncated.
- Original Elektron intro callbacks/durations, with **zero additional frames**.
  The older public builder still adds its five-second notice; it has not been
  updated by this source-only checkpoint. The private FANG input already had
  stock intro timing; adding the Settings page does not shorten that input.
- SY FANG, type16: saw/pulse/sub bass synth, nonlinear resonant filter, filter
  envelope, velocity accent and continuous pitch slew. Its single SRC page is
  TUNE/WAVE/CUT/RES/ENV/DEC/GLID/BITE. RES uses mirror29, never sample-selector28.
- TWIN/FUSE/FUS2 Repair4 and sample-left panel polish, including native TUNE,
  A/B identification, independent response controls and contextual INFO.
- Existing Plaits25/four DX7 banks, graphical UI2.1, Musical WAVE V4/Déjà Vu
  and One-Shot Zoom V11 source/configuration. No new synthesis change is
  attributed to the Credits addition.

Type15 is reserved only: **no unfinished CHIMERA implementation is included**.
No ARP, updater experiments, recordings, user projects, firmware, decoded
sections, snapshots, compiled objects or personal machine paths are included.

## Source layout and corresponding source

- `source/experiments/`: actual integration, musical assembly, machine engines,
  UI glue and related validation fixtures; historical helpers remain identified
  by their original names and are not new candidate variants.
- `source/digikit/`: current source for incorporated framework/Plaits modules
  plus relevant integration/compiler checks and tests. Upstream pin:
  `e48d0a2a83f055c549391173247cea02c571927f`.
- `source/catalog/`: the active candidate's module/port configuration, with
  absolute developer paths made relative. Compiled object outputs are omitted.
- `source/host-tools/`: retained graphical UI/DV/Zoom composition helpers.
- [SOURCE_MANIFEST.json](SOURCE_MANIFEST.json): original and published SHA-256
  for every imported file, compiled-object input identities, and explicit
  path-only/source-metadata normalization records.
- [VALIDATION.md](VALIDATION.md): tested boundaries and important limitations.

The existing complete base source/dependency/license archive is still available
without any release modification:
[DT2-All-in-One-Corresponding-Source-r2.zip](https://github.com/karigardarsson/digitakt-ii-research/releases/download/v0.3.0-beta/DT2-All-in-One-Corresponding-Source-r2.zip),
SHA-256 `52343f98bc042c859965f09be594a2be5b3926241d7844bca7b2b9f0af418621`.
Use that archive for the pinned DigiKit tooling, Rust dependencies, container
tool and full retained notices, together with the current source here. Its
older builder/boot-credit implementation is historical, not this candidate's
entry point. Raw DX7 bank `.syx` files are not duplicated in Git; incorporated
engine table sources and their notices remain available.

These are the existing build/verification sources, **not a new turnkey Windows
builder**. They retain staged build/report and hash-gated input requirements.
The Credits composer consumes the exact FANG input
`32de4571df62b4baa6012afc33396a5d3166519cda124383866ea6acb66c92a9`;
the FANG composer retains the Repair4 input identity
`66f022bf5640afb49c2dc5b02ec9b0cb9a8b3def30c84536e92154a29a9078fd`.
Those firmware inputs are not redistributed. A clean stock-to-candidate rebuild
from this Git snapshot alone was not performed or claimed for this publication.
Supply official firmware legally; do not redistribute generated firmware.

## Reuse and support

Experimental, as-is, no warranty or promised support/update schedule. The
Plaits trigger-click investigation remains parked; no fix is claimed here.
Hardware acceptance of this exact Credits/FANG/FUS2 composition remains pending.
Community source contributions are welcome; public release assets are separate.
