# Sample Editing Pack — macOS builder

This builds a native, standalone app for Apple Silicon or Intel using the existing
Python/Tk GUI, custom patch assets and bundled MIT firmware-container tool source.
Windows downloads are unchanged.

## Download — macOS candidates

**These apps passed native packaging and GUI checks, but a complete build with
the correct stock OS 1.17 input has not yet been tested on macOS.** The firmware
itself is the same hardware-accepted Sample Editing Pack; these Mac builders
remain candidates until that end-to-end check is completed.

- **[Apple Silicon / M-series Mac](../downloads/DT2-Sample-Editing-Pack-macOS-arm64.zip?raw=1)** — native arm64, tested on macOS 15.8.2.
- **[Intel Mac](../downloads/DT2-Sample-Editing-Pack-macOS-x86_64.zip?raw=1)** — native x86_64, tested on macOS 15.7.9.
- [Checksums](../downloads/SHA256SUMS.txt) · [Build/validation records](releases/) · [Successful CI run](https://github.com/karigardarsson/digitakt-ii-research/actions/runs/42189170278)

Both require **macOS 15 or later** and include Python/Tk; no Python installation
is needed. Each app was tested after relocation into a path with spaces and
accented characters, using a system-only PATH. Native executable/resource checks,
Tk GUI creation/layout, signatures, dependencies, malformed-input rejection,
atomic file creation and existing-file/symlink protection pass. This is automated
CI testing, not manual desktop/Gatekeeper acceptance or a completed stock-input
firmware build. Full details are in each package's VALIDATION.json.

The required input is your own official Digitakt II OS 1.17 `.syx`:
`26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`.
The only accepted generated firmware is:
`880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06`.
The app never flashes or connects to hardware.

## Install the app

1. Choose the Apple Silicon package for an M-series Mac, or Intel for an Intel Mac.
2. Extract the ZIP and move **DT2 Sample Editing Builder.app** to Applications.
3. Open the app, select your own stock OS 1.17 file and choose a new output filename.
4. Click Build and wait for **Verified and saved**. The builder checks the exact
   firmware output before saving; existing files are preserved.

No Python installation is needed. macOS 15 is the initial supported/tested target;
older macOS versions are not qualified. The app is ad-hoc signed, not Developer ID
signed or notarized. macOS may block first launch; use the per-app **Open Anyway**
option in System Settings → Privacy & Security only after checking the package
hash and source. Follow [Apple's per-app opening instructions](https://support.apple.com/en-us/102445);
managed Macs may disallow this. Do not disable Gatekeeper globally.

## Build from source on a Mac

Use native Python 3.12 x64/arm64 with Tk 8.6, Xcode Command Line Tools and the pinned
PyInstaller 6.16.0 dependency. From the package root:

```sh
python3 -m pip install -r source/builder-source/requirements-build.txt
python3 macos/build_macos.py --output /tmp/dt2-macos-build
```

The output path must not already exist. The script compiles the unmodified MIT C
tool with the existing Makefile, creates a platform-specific resource seal, packages
the existing GUI, then checks the relocated app with a system-only PATH, Tk layout,
resource tampering, native parser, dependencies, signatures and file protections.

An optional `--stock-url` may point to an official HTTPS `.syx`/ZIP download for
exact-output testing. Its SHA must match above. Test firmware stays temporary and
is never included in the package or Actions artifacts. No stock URL means that
full firmware build is explicitly marked NOT_TESTED. Do not upload firmware to
this repository or distribute generated `.syx` files.

Keep LICENSE, NOTICE and third-party notices. This is modified DigiKit-derived
work by Kári; digikit by Em D - https://github.com/ElekLabs/digikit.
