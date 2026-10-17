# Installation

This beta is for Digitakt II using an exact official OS 1.17 or OS 1.16 source file. OS 1.17 is recommended. Back up important projects, presets, and samples first.

1. Download the Windows builder from the v0.1.1-beta release.
2. Open [Elektron Support & Downloads — Digitakt II](https://www.elektron.se/support-downloads/digitakt-ii), download the official OS distribution, and extract the `.syx`.
3. Use OS 1.17 with SHA-256 `26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`, or OS 1.16 with SHA-256 `278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec`.
4. Run `DT2-DejaVu-Builder.exe`.
5. Select the stock `.syx` and confirm that the window detects the intended version.
6. Choose an output location and click **Build Déjà Vu**.
7. Wait for 100% and the success message. The verified output hash is shown in the release README.
8. Connect Digitakt II directly by USB and open Elektron Transfer.
9. Select Digitakt II for MIDI IN and MIDI OUT, then drop the generated `.syx` on the **DROP** page.
10. Follow the prompts and confirm the update on the device.
11. Keep power and USB connected until installation finishes and the instrument reboots.

Official reference: [Elektron — How to update your device](https://support.elektron.se/support/solutions/articles/43000662890-how-to-update-your-device).

The builder does not connect to Digitakt II, invoke Elektron Transfer, or install firmware.
