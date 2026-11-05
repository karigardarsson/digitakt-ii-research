# Digitakt II Sample Editing Pack — Windows builder

**Final local release package. Hardware accepted by Kári on 2026-11-05.**

Includes Universal Zoom, SmartSlice with the native-owner exception fix,
SampleLAB with Waveform/Settings/Info pages and current zoom-dependent START/END
and individual A/B/H response refinements, plus native SLICE CFADE. No unrelated
synth engines or Modular+ runtime is included.

[Download the complete Windows package](https://github.com/karigardarsson/digitakt-ii-research/raw/refs/heads/main/sample-editing/downloads/DT2-Sample-Editing-Pack-Windows-x64.zip) · [HOW-TO and four GIFs](docs/HOW_TO.md) · [Build from source](BUILDING.md)

## Build your own local firmware file

1. Extract this entire ZIP to a writable folder on Windows x64.
2. Open `DT2-Sample-Editing-Builder.exe`.
3. Select your own official **Digitakt II OS 1.17 `.syx`** file.
4. Choose a **new**, unused output `.syx` filename and click Build Sample Editing Pack.
5. Wait for verification and the saved-file message. Check the output SHA below.

No Python, WSL, compiler, network or device connection is needed to use the EXE.
The builder performs file operations only; it never flashes a device. It rejects
wrong/corrupt/truncated/already-modded inputs and preserves existing output files.
Unsigned Windows executable: no signing/SmartScreen acceptance is claimed.

Accepted stock SHA-256:
`26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`

Default output: `DT2_OS117_SAMPLE_EDITING_RELEASE_CANDIDATE.syx`

Expected output SHA-256:
`880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06`

PowerShell file check: `Get-FileHash -Algorithm SHA256 'your-file.syx'`.
Kári has accepted this exact firmware on hardware. This final packaging step
only removes the builder acceptance warning; firmware and generation logic are
unchanged. The existing default firmware filename is retained. The Ready log,
larger explanatory text, all four features and source/license notices remain.

Optional file-only command line:

```powershell
.\DT2-Sample-Editing-Builder.exe --build 'stock.syx' --output 'new-pack.syx' --report 'build-report.json'
```

## Contents and evidence

- [HOW-TO](docs/HOW_TO.md): controls, page navigation, editing, loading and all export actions.
- [Hardware acceptance](HARDWARE_ACCEPTANCE.md): dated user acceptance record.
- [Test report](TEST_REPORT.md): current tests and reused unchanged evidence.
- `docs/images/native` and `docs/images/6x`: screenshot originals/enlargements.
- `media/reddit`: four GIFs, timings, hashes and capture provenance.
- `source`: current corresponding builder/preferred module source, captured
  link objects, recipes, provenance, tests, encoder source and licensing.
- `SHA256SUMS.txt`: every distributed file, except the checksum list itself.

The ZIP contains no Elektron stock or integrated firmware, emulator snapshots,
sample audio, device keys or flashing tools. Users must supply stock OS themselves.
Source recompilation of the current Lab and object-to-payload reproduction of
all seven CF modules were verified. Clean recompilation of every historical
CF/DSP module is not newly qualified; their exact existing custom assets and
preferred source are retained. Source GUI packaging uses Python3.12 and the
existing PyInstaller6.16.0 environment; see `source/builder-source/build_windows.ps1`.
Windows11 x64 CLI isolation is tested; clean VM, Windows10, ARM64 and graphical
interaction with this newly frozen EXE are not newly tested.

No new Sound/Project format is introduced. Existing native parameter storage,
pool/file writers and stock workflows remain in use. Hardware acceptance is recorded separately;
no new storage format or rollback behavior was introduced during final packaging.

Modified work by Kári / karigardarsson, derived from DigiKit.
digikit by Em D - https://github.com/ElekLabs/digikit
Preserve the accompanying LICENSE, NOTICE and third-party notices. These code
licenses grant no rights to Elektron firmware. Not affiliated with Elektron.
The authorized Git commit/push publishes builder, source, documentation and media only.
No firmware, separate GitHub Release, flashing or Reddit post is part of this preparation.
