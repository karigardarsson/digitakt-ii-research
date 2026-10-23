# Third-party notices and acknowledgements

This document distinguishes conceptual inspiration, research or development tooling, and code redistributed by this repository or its Windows builder. The repository's top-level MIT license covers the original project material; it does not replace the license terms of included third-party material.

## All-in-One v0.3.0 scope

Unlike the earlier v0.1/v0.2 builders, All-in-One v0.3.0 **does include DigiKit tooling/framework code** and Tyler Neely/spacejam's optimized Plaits port. This edition is AGPL-3.0-or-later with DigiKit NOTICE and applicable per-file MIT/runtime licenses retained. Its complete corresponding source, licenses and exact provenance disclosures accompany the [v0.3.0 release](https://github.com/karigardarsson/digitakt-ii-research/releases/tag/v0.3.0-beta). The root MIT license does not replace those obligations. Original Plaits: Emilie Gillet/Mutable Instruments; Kári supplies the integration, graphics, Musical WAVE and Zoom/Pan. Read the release's THIRD_PARTY_NOTICES.md for the complete edition-specific inventory.

The historical tooling statements below describe v0.1/v0.2 only, not All-in-One.

## Conceptual inspiration

- **Mutable Instruments — [Marbles](https://pichenettes.github.io/mutable-instruments-documentation/modules/marbles/):** the original inspiration for the Déjà Vu random-loop concept. No Marbles code is included in this repository or the Windows application.

## Behavioral reference

- **Bezronczek — [Syntakt Firmware Workbench](https://github.com/Bezronczek/syntakt-firmware-workbench) (MIT):** the DV-F/DV-T implementation was used as a behavioral reference. The Digitakt II implementation is native ColdFire code adapted to the Digitakt II LFO architecture; Syntakt Firmware Workbench source is not redistributed here or in the Windows application.

## Research and development tooling

- **ElekLabs — [digikit](https://github.com/ElekLabs/digikit) (AGPL-3.0):** used for Digitakt II reverse-engineering and emulation infrastructure during development. It is not bundled with the Windows application and no digikit source is redistributed by this repository.

## Redistributed code

- **mischa85 — [elektron-firmware-tool](https://github.com/mischa85/elektron-firmware-tool) (MIT):** provides firmware-container handling, compression, and repacking code used by the builder. Its source is pinned at commit `a5bce9a6af644386d900924082993a805e812874` under [`windows-builder/vendor/elektron-firmware-tool/`](windows-builder/vendor/elektron-firmware-tool/). The complete upstream MIT notice is retained in [`windows-builder/vendor/elektron-firmware-tool/LICENSE`](windows-builder/vendor/elektron-firmware-tool/LICENSE).

## Elektron

Elektron manufactures Digitakt II and the original firmware. This project is independent and unofficial; it is not affiliated with or endorsed by Elektron. Official Elektron firmware is neither included nor redistributed.

These acknowledgements do not imply collaboration, approval, or endorsement by any named project, author, or company.
