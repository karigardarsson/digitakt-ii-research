# Known limitations

- Only the exact official Digitakt II OS 1.17 and OS 1.16 files documented in the release are supported.
- The Windows builder is unsigned and may trigger a SmartScreen warning.
- Broad MIDI, sequencer, transport, and synchronization regression remains incomplete.
- Real Sound/Pattern/Project save, reload, and power-cycle persistence remains incompletely tested.
- OS 1.17 has initial hardware validation for normal boot, DV-F, DV-T retrigger, and normal operation; long-duration stability has not been established across a broad project set.
- The random loop itself is runtime state; Sound conversion tests cover WAVE/SPH/MODE parameters rather than persistence of the generated random sequence.
- The project cannot guarantee recovery if the startup menu or bootloader is unavailable.

