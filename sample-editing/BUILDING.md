# Build and verify the standalone Windows builder

For native Apple Silicon/Intel packaging, see the separate [macOS build guide](macos/README.md).
Windows release files and their existing build procedure are unchanged.

The portable Windows EXE is ready to use. End users supply their own official
Digitakt II **OS 1.17** `.syx`; no Python, compiler, WSL or device connection is
needed. The exact accepted stock hash and generated firmware hash are in README.md.

## Run the preferred source on Windows

Use Python 3.12 x64 with its normal Tk 8.6 support. From `sample-editing`:

```powershell
python .\source\builder-source\builder_gui.py
```

File-only CLI:

```powershell
python .\source\builder-source\builder_gui.py --build 'C:\Firmware\Digitakt_II_OS1.17.syx' --output 'C:\Firmware\new-sample-pack.syx' --report 'C:\Firmware\build-report.json'
```

The input must match stock SHA-256
`26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`.
The output must match
`880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06`.
Existing files are never overwritten. No flash or device operation is performed.

## Package the EXE from source

From `source/builder-source`, install the pinned packaging dependency in your own
Python environment, then run the existing packaging script:

```powershell
python -m pip install -r requirements-build.txt
.\build_windows.ps1
```

This uses PyInstaller 6.16.0 and produces
`dist/DT2-Sample-Editing-Builder.exe`. Tk, assets and the included MIT encoder are
bundled by the spec. No DSP compiler or original firmware is redistributed.
The rebuilt EXE can differ in packaging metadata; its generated firmware must
still match the exact output hash above.

## Focused checks

From `sample-editing`:

```powershell
$env:DT2_TEST_STOCK = 'C:\Firmware\Digitakt_II_OS1.17.syx'
python .\source\test_builder.py
python .\source\test_gui.py
python .\source\builder-source\relink_modules.py
```

The first command runs 18 composition, tamper, input and file-safety checks.
The Tk test checks the accepted-state text, font, Ready log, layout and save
dialog defaults without using a device. The relinker reconstructs all seven
custom CF payloads from captured objects, without a compiler or stock input.
Tests write only local temporary output/validation files.

## Source and provenance limits

`firmware-source` contains the preferred C/assembly and captured link objects/maps.
The current Lab source was compiled and its H-fix baseline reproduced before
acceptance. Clean recompilation of every historical CF/DSP component is not newly
qualified; their exact existing custom assets and preferred source are retained.
Object-to-payload relinking of all seven CF modules is qualified. The independent
container encoder's pinned MIT source is under `vendor/elektron-firmware-tool`.

`source/release-preparation` records workspace-dependent qualification scripts;
it is not a second portable build system. Workstation identities in historical
reports/object toolchain metadata are replaced by `WORKSPACE/`; code, relocation
data and executable assets are unchanged. Historical reports retain their own
hash/status context; current hardware acceptance is in HARDWARE_ACCEPTANCE.md.

Keep LICENSE, NOTICE and runtime notices. The repository root MIT license does
not replace this subtree's AGPL-3.0-or-later and DigiKit attribution terms.
