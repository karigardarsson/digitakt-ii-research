# Digitakt II research

Offline, reproducible research notes and prototypes for Digitakt II OS 1.16.
This is an independent project. It does not contain Elektron firmware,
extracted binaries, snapshots, generated images, sample content, credentials,
or device-control tooling.

## Contents

- `src/dv/`: deterministic host model of Déjà Vu free-running and trigger
  reset LFO behavior, plus the exact phase-input adapter used by the tests.
- `coldfire/`: non-installable ColdFire assembly research prototypes for the
  proposed waveform path and startup allocation handoff.
- `tests/`: standard-library host tests for the model and adapter.
- `research/`: byte-backed static findings, bounded Unicorn evidence, and
  integration blockers.
- `tools/midisniff.py`: passive SysEx capture and envelope decoder. It opens
  a MIDI input only and never transmits to a device.

## Run the host tests

```sh
PYTHONPATH=src/dv python -m unittest discover -s tests -v
```

The assembly files and their associated probes were evaluated in a separate,
private Unicorn research environment using firmware supplied locally by its
owner. That environment, its dependencies, and all firmware-derived files are
intentionally absent here.

## Evidence levels

| Level | Meaning |
| --- | --- |
| `SOURCE/STATIC` | Addresses and control flow derived from byte-level inspection. |
| `HOST` | Deterministic Python model or test result. |
| `SYNTHETIC UNICORN` | Isolated guest-memory execution with synthetic inputs. |
| `NATURAL EMULATED` | Observed during bounded emulated firmware execution. |
| `HARDWARE` | Observed on a physical device. |

The published ColdFire work has SOURCE/STATIC, HOST, SYNTHETIC UNICORN, and
bounded NATURAL EMULATED evidence. It has no hardware validation. Natural
frame-tick, note-on, UI/save-load, audio, executable placement, and full
device-lifetime behavior remain open integration gates. No installable CFW is
provided or implied.

## Scope

Use this repository for study, discussion, and reproducible offline research.
Do not treat a research address, emulator trace, or assembly prototype as a
firmware patch or as authorization to flash a device.

Elektron and Digitakt are trademarks of their respective owners. This project
is unofficial and is not affiliated with Elektron.
