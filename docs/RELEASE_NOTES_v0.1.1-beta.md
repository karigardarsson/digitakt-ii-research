# Digitakt II Déjà Vu v0.1.1 Beta — OS 1.17 Support

This release adds a separate, strictly hash-gated OS 1.17 profile to the standalone Windows builder while keeping the OS 1.16 path and exact output unchanged.

## What changed

- Added official Digitakt II OS 1.17 support.
- OS 1.17 is now the recommended stock version; OS 1.16 remains supported.
- The GUI detects and displays the selected stock OS version.
- Each version has its own frozen patch manifest, patch addresses, packaging prerequisites, and golden output hash.
- Updated installation, stock recovery, hardware status, and license documentation.

No Déjà Vu feature or firmware behavior changed.

## Hardware status

The exact OS 1.17 output was flashed and tested on a physical Digitakt II: normal boot, DV-F modulation, DV-T modulation and retrigger, and normal operation passed with no problems observed. The exact OS 1.16 V5.1 output remains supported and hardware tested. This is initial beta validation, not exhaustive long-term regression.

## Simple workflow

**Download builder → Select official stock OS → Build → Install via Elektron Transfer**

Download official Digitakt II firmware from [Elektron Support & Downloads](https://www.elektron.se/support-downloads/digitakt-ii). Official and patched Elektron firmware are not included in this release.

The executable is unsigned and may trigger Windows SmartScreen. Read the installation and recovery documents before use. This project is unofficial and is not affiliated with or endorsed by Elektron.
