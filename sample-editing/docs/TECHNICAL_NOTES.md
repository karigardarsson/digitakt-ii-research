# Sample Editing Pack — technical reference

For day-to-day use, see the [HOW-TO](HOW_TO.md) or [Windows package instructions](../README.md).
This page keeps release identities and engineering context separate from those guides.

## Release identities and compatibility

The package requires the exact official Digitakt II OS 1.17 input. Other versions,
corrupt inputs and already modified firmware are rejected.

| File | SHA-256 |
|---|---|
| Official stock OS 1.17 input | `26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5` |
| Generated Sample Editing firmware | `880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06` |
| Public Windows builder EXE | `593a3f6ca03c217601e660cc1693e6053d5cd42cbdd1663510572f1ec32f999c` |
| Windows download ZIP | `79679bc9c0db61857eeb0b886d435451e9fd79cd187b38e2a0d59a04b3d5aa8a` |

The builder's default output filename remains
`DT2_OS117_SAMPLE_EDITING_RELEASE_CANDIDATE.syx`. The filename was retained;
the exact firmware hash above was accepted by Kári on November 5, 2026.
See the [acceptance record](../HARDWARE_ACCEPTANCE.md) and
[public manifest](../PUBLIC_MANIFEST.json).

Windows 11 x64 is tested. Windows 10, ARM64, clean-VM behavior, other DPI settings,
and signing/SmartScreen/antivirus acceptance are not newly qualified. The EXE is
unsigned. These Windows limits are separate from the instrument's hardware acceptance.

### macOS candidates

Native Apple Silicon and Intel apps are available separately; see the
[Mac guide](../macos/README.md), [CI run](https://github.com/karigardarsson/digitakt-ii-research/actions/runs/42189170278)
and [platform validation reports](../macos/releases/). Automated app/GUI, native
tool, dependencies, signatures, resource checks and file protection passed on
macOS 15.8.2 arm64 and 15.7.9 x86_64. The apps include their Python/Tk runtime.

No official stock download URL was supplied to the workflow, so **full stock-input
generation is NOT_TESTED on macOS**. The shared core still rejects any generated
firmware whose hash differs from 880d810f…; no firmware code was changed.
These Mac builders remain candidates. They are ad-hoc signed, not Developer ID
signed/notarized; downloaded-app Gatekeeper handling and older macOS releases
are unqualified. Windows downloads and their hashes remain unchanged.

Mac builds compile the existing MIT container-tool source, generate a native
resource seal in an isolated staging directory, and reuse the existing GUI/core
and custom assets. Windows continues selecting the same sealed `.exe`.
The only shared-core change is the platform-aware native-tool filename.

| Mac ZIP | SHA-256 |
|---|---|
| Apple Silicon | `d45663fdf1d528a87cc28b15564779bcfd1311b494abe88ea0bae52ebfeb3501` |
| Intel | `6124cf2cd226ee67b195eb64f46e769929d492d2e24018e3f454e3ca9438be8d` |

## Download and repository documentation

The [download checksum file](../downloads/SHA256SUMS.txt) identifies the unchanged
Windows ZIP. Its internal `SHA256SUMS.txt` and `source/SOURCE-MANIFEST.json`
describe the files shipped in that ZIP. The [repository copy of that package
inventory](../SHA256SUMS.txt) retains the same release snapshot.

Repository README/HOW-TO pages can receive editorial updates without rebuilding
the ZIP. Their current text may therefore differ from the documentation hashes
in the frozen package inventory. The earlier documentation-only cleanup changed no download,
builder, firmware, screenshot, GIF or source asset.

That inventory describes the frozen Windows package. Subsequent native Mac
packaging has separate archives, generated seals and validation records; the
Windows archive is not rebuilt to add Mac support.

## Encoder behavior and earlier regression context

The current SampleLAB response policies are:

- A advances one region after four directional motion events.
- H advances one adjacent action after two eligible directional events, retaining
  its existing tick guard and CHOP/LOOP bounds. H-push navigation is separate.
- Settings B uses four delivered units per AUTO sensitivity step and six per
  COUNT/EVEN step. These units are not promises of physical detent counts.
- START/END use `max(48, view_span / 512)` frames per delivered unit, with held
  single-frame precision. Deep-zoom behavior is preserved; larger visible spans
  travel faster. Native acceleration remains, without the earlier additional
  magnitude-based multiplier that caused jumps.

Direction changes reset partial selection gestures. The previous six-event H
threshold was reduced to two after hardware feedback that rotation felt too
sluggish. The accepted correction leaves other encoders, H-push, rendering,
sample processing, DSP and persistent formats unchanged.

These details explain the implementation, not a new user control. Current source
is in [workflow.c](../source/builder-source/firmware-source/sample_lab/workflow.c).

## Screenshots, GIFs and validation

The images are native 128×64 emulator renders, with nearest-neighbor enlarged
copies. GIF pauses are edited for readability; they are not operation-speed
measurements. Looping restarts a demonstration and does not imply automatic undo.

The SmartSlice sequence uses genuine dialog/result captures. Its example has
16 actual native regions; the editor was opened separately and the CF-only
capture lacks its DSP waveform trace. Universal Zoom uses deterministic waveform
data and supplied completed waveform responses; those captures do not exercise
physical waveform acquisition. SampleLAB examples use supplied PCM and native
filesystem/UI fixtures. None of this material is presented as hardware video.

Full timing, pixel-preservation, capture identities, native/emulator evidence
and original limitations remain in:

- [Capture notes](CAPTURE_NOTES.md)
- [Screenshot index](SCREENSHOT_INDEX.json)
- [GIF provenance and timings](../media/reddit/MEDIA_MANIFEST.json)
- [Test report](../TEST_REPORT.md)
- [Historical release evidence](../source/release-evidence/)

Historical pending statuses belong to their original snapshots. The dated
hardware acceptance record identifies the current accepted firmware; it does
not invent a granular checklist or imply exhaustive testing of every setup.

## Source, provenance and licensing

See [build instructions](../BUILDING.md), [preferred source](../source/builder-source/),
[provenance](../source/builder-source/PROVENANCE.json) and
[public preparation notes](../PUBLIC_PREPARATION.md). Clean recompilation of every
historical CF/DSP component is not newly qualified; captured custom objects and
preferred source are retained, and all seven CF payloads relink exactly.
No proprietary Elektron firmware or restricted DSP compiler is distributed.

The pack introduces no new Sound/Project format. Native file, pool and parameter
storage paths remain in use. SampleLAB marker undo is one-level layout undo,
not file/audio history; partial exports or pool assignments can remain after
cancellation. Playback CFADE is loop-wrap blending, not cross-slice transitions.

Keep the [AGPL-3.0-or-later LICENSE](../LICENSE), [DigiKit NOTICE](../NOTICE) and
[third-party notices](../THIRD_PARTY_NOTICES.txt). The repository's root MIT
license does not replace these obligations.
