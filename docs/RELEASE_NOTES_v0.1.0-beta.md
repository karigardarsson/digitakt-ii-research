# Digitakt II Déjà Vu v0.1.0 Beta

This beta adds two random-loop waveforms to all three Digitakt II LFOs:

- **DV-F**: free-running random loop.
- **DV-T**: random loop whose read position resets on the track trigger.

MODE selects 2, 4, 8, 16, or 32 steps. SPH controls refresh, lock, and reorder behavior. Stock WAVE 0–6 remain available.

## Simple Windows workflow

1. Download `DT2-DejaVu-Builder-v0.1.0-beta-Windows.zip` and extract it.
2. Run `DT2-DejaVu-Builder.exe`.
3. Select the official Digitakt II OS 1.16 `.syx` obtained from [Elektron Support & Downloads](https://elektron.se/support-downloads).
4. Click **Build Déjà Vu** and wait for the success message.
5. Install the generated file manually with Elektron Transfer.

The builder contains no Elektron firmware and performs no USB/MIDI transmission or flashing. It accepts only stock OS 1.16 SHA-256 `278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec` and writes only the exact tested V5.1 result SHA-256 `d9fe67b3778ce5d31aaaa807e6e43ac1929d0f36a10c393adc0a8c590c547835`.

## Hardware evidence

V5.1 was generated with the Windows builder and tested on Digitakt II hardware: normal boot, clean DV-F/DV-T labels, audible modulation from both waveforms, DV-T repeat/retrigger behavior, and working stock LFO waveforms were observed.

Broad MIDI, sequencer/transport, save/reload/power-cycle persistence, and long-duration regression remain incomplete. Digitakt II OS 1.16 is the only supported base.

## Read before installing

This is unsigned, unofficial firmware. Windows SmartScreen may warn about the unsigned builder. Back up important data and keep official stock OS 1.16 plus a physical MIDI interface available.

- [Installation](INSTALLATION.md)
- [Stock recovery](RECOVERY.md)
- [Known limitations](KNOWN_LIMITATIONS.md)

This project is not affiliated with or endorsed by Elektron.
