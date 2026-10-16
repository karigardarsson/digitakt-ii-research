# Installation

This beta is for Digitakt II using the exact official OS 1.16 source file. Back up important projects, presets, and samples first.

1. Download the Windows builder from the v0.1.0-beta release.
2. Open [Elektron Support & Downloads — Digitakt II](https://www.elektron.se/support-downloads/digitakt-ii). Under **Updates**, look for **Digitakt II OS 1.16**. Download its official distribution ZIP and extract `Digitakt_II_OS1.16.syx`.
3. Confirm that the extracted file is named `Digitakt_II_OS1.16.syx` and that its SHA-256 is `278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec`.
4. The official page currently lists OS 1.17. Do not use it, or any other version: the builder intentionally rejects them. If OS 1.16 is absent, use **Contact Support** at the bottom of the same official Digitakt II page and request the original OS 1.16 download. Do not use a mirror.
5. Run DT2-DejaVu-Builder.exe.
6. Select the verified OS 1.16 .syx, choose an output location, and click **Build Déjà Vu**.
7. Wait for the success message. The verified output SHA-256 is d9fe67b3778ce5d31aaaa807e6e43ac1929d0f36a10c393adc0a8c590c547835.
8. Connect Digitakt II directly by USB and open Elektron Transfer.
9. Select Digitakt II for MIDI IN and MIDI OUT, then drop the generated .syx on the **DROP** page.
10. Follow the prompts and confirm the update on the device.
11. Keep power and USB connected until installation finishes and the instrument reboots.

Official reference: [Elektron — How to update your device](https://support.elektron.se/support/solutions/articles/43000662890-how-to-update-your-device).

The builder does not connect to Digitakt II, invoke Elektron Transfer, or install firmware.

