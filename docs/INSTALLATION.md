# Installation — choose the correct edition

## All-in-One v0.3.0 Community Beta (OS 1.17 only)

[Download v0.3.0 and checksums](https://github.com/karigardarsson/digitakt-ii-research/releases/tag/v0.3.0-beta) · [Practical HOW TO](DIGITAKT_II_ALL_IN_ONE_USER_GUIDE.md) · [Stock recovery](RECOVERY.md) · [Known issues](ALL_IN_ONE_v0.3.0.md)

Back up projects, Sounds and samples. Keep official stock firmware and a working physical MIDI-DIN recovery connection ready. This exact release image is not hardware-tested; the recurring Plaits trigger click remains. No guaranteed support, maintenance schedule or future OS compatibility.

1. Download and extract **DT2-All-in-One-v0.3.0-beta-Windows-r2.zip** from the existing v0.3.0 release. Keep the included source, documentation and licenses with the application. The unsigned executable may produce a Windows SmartScreen warning; do not disable security software.
2. Obtain your own official Digitakt II OS 1.17 `.syx` from [Elektron](https://www.elektron.se/support-downloads/digitakt-ii). Required SHA-256: `26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`. No stock or modified Elektron firmware is included in the download.
3. Run **DT2-All-in-One-Builder.exe**, select that official file, and confirm **Detected OS: Digitakt II 1.17**. Choose a new output filename and click **Build All-in-One**. No Python/Rust/WSL is needed; building is offline and never connects to or flashes a device.
4. Wait for the success message. Expected generated firmware SHA-256: `426dd1be5be3d356b5fa26900c729baf555cc16393611c4e0544ebd2d95eec76`. Builder presentation revision r2 does not change this firmware.
5. For a deliberate installation, follow [Elektron's update instructions](https://support.elektron.se/support/solutions/articles/43000662890-how-to-update-your-device) in Elektron Transfer. Keep power connected until the update completes and the instrument restarts. Earlier Plaits builds have failed normal Transfer updates at 0%/PREPARING; do not assume that route is reliable on every installation. Review the separate [physical MIDI-DIN recovery procedure](RECOVERY.md) beforehand; building a file does not perform any update.
6. After installation, use the [HOW TO](DIGITAKT_II_ALL_IN_ONE_USER_GUIDE.md) for the verified controls, including the non-stock MODE meanings on Musical WAVE and SAMPLE TUNE-offset quantization. Do not redistribute the generated Elektron-containing `.syx`.

The original Elektron intro is retained, followed by DigiKit legal credits. See the [release](https://github.com/karigardarsson/digitakt-ii-research/releases/tag/v0.3.0-beta) for validation status and corresponding source; GitHub's automatic research-repository source archive is not the complete All-in-One corresponding source.

## Older editions (not All-in-One v0.3.0)

[v0.2.0](https://github.com/karigardarsson/digitakt-ii-research/releases/tag/v0.2.0-beta) is the earlier Déjà Vu/Zoom builder, with OS1.16 Déjà Vu-only and OS1.17 Déjà Vu/Zoom editions. Its executable, buttons and output hashes differ. Use its own versioned instructions/README; do not use its output hashes to validate v0.3.0. Earlier release assets remain available as separate editions.
