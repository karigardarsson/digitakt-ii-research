# Sample Editing Pack — Windows package

Universal Zoom, SmartSlice, SampleLAB and CFADE for **Digitakt II, OS 1.17**.

**[Download the Windows package](https://github.com/karigardarsson/digitakt-ii-research/raw/refs/heads/main/sample-editing/downloads/DT2-Sample-Editing-Pack-Windows-x64.zip)** · **[HOW-TO and demonstrations](docs/HOW_TO.md)**

For macOS, see the separate **[Apple Silicon and Intel candidate packages](macos/README.md)**.
Their native app/GUI checks pass; full stock-input generation on Mac is not yet
verified. The existing Windows package is unchanged.

## Create the firmware file

1. Extract the ZIP to a writable folder on Windows x64.
2. Open **DT2-Sample-Editing-Builder.exe**.
3. Select your own official **Digitakt II OS 1.17 `.syx`** file.
4. Choose a new output `.syx` filename and click **Build Sample Editing Pack**.
5. Wait for **Verified and saved**. Use the generated file for installation.

The builder works offline. It needs no Python, compiler or administrator rights,
and it never connects to or flashes your instrument. It checks the stock file
and preserves existing output files. The EXE is unsigned; Windows may show a
publisher warning.

## Install and start editing

Back up your projects, Sounds and samples, then install the generated file with
Elektron Transfer using [Elektron's update instructions](https://support.elektron.se/support/solutions/articles/43000662890-how-to-update-your-device).
Keep your official stock firmware for recovery.

The [HOW-TO](docs/HOW_TO.md) covers loading from +Drive, opening SampleLAB after
recording, SmartSlice, trimming, saving, zoom and CFADE. Screenshots and four
short demonstrations show the controls.

## Package contents

- The Windows builder and its source.
- The HOW-TO, screenshots and four GIF demonstrations.
- Licenses, credits and technical reference material.

No Elektron firmware is included: you supply stock OS 1.17 yourself.
The Windows builder is tested on Windows 11 x64; Windows 10 and ARM64 are not
confirmed. See [technical notes](docs/TECHNICAL_NOTES.md) for checksums,
source-build instructions and compatibility details.

## Credits and licensing

Modified work by **Kári / karigardarsson**, derived from
**digikit by Em D - https://github.com/ElekLabs/digikit**.
Keep the [LICENSE](LICENSE), [NOTICE](NOTICE) and [third-party notices](THIRD_PARTY_NOTICES.txt).
The repository's root MIT license does not replace this pack's license terms.
This unofficial modification is not affiliated with Elektron.
