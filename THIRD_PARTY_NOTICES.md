# Third-party notices and acknowledgements

This document distinguishes conceptual inspiration, research or development tooling, and code redistributed by this repository or its Windows builder. The repository's top-level MIT license covers the original project material; it does not replace the license terms of included third-party material.

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
