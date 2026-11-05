DIGITAKT II OS 1.17 — STANDALONE SAMPLE EDITING
Private hardware-test candidate, 2026-11-03. Not a published release.

ONE CANDIDATE
HARDWARE_TEST/DT2_OS117_SAMPLE_EDITING.syx
SHA-256: d7cbee11101c81fa54b754880083d5130e01ffdadcac254ce07e5e4709a33f93

CONTENTS
Stock OS1.17 + Universal Zoom + actual SmartSlice + SampleLAB.
No extra synth machines, XSLICE, CFADE audio extension, Musical WAVE/DV,
GRLAB or All-in-One machine/parameter/storage framework. DSP is exact stock.
Native System > Credits/About preserves the reused code's attribution.

UNIVERSAL ZOOM
On native sample waveform SRC page2: C for SAMPLE/ONESHOT, WERP,
STRETCH, REPITCH and GRID; G for stock manual SLICE.
Short press/release cycles1x,2x,4x,8x,16x,32x,64x,1x.
Plain rotation pans when zoomed. Hold about0.8s resets1x.
A press fits the selected GRID/SLICE region; repeating matching fit restores
overview. UP returns to SRC1. Machine/sample change resets the viewport.
The native7->8 acquisition sequence and last-completed-waveform retention
are reused. A pending pan retains the completed view until matching data arrives.

SMARTSLICE
Use stock SLICE and its normal slice-grid selection workflow. SMART is the
automatic option below the fixed2/4/8/16/32/64 grids; confirm normally.
It analyzes the selected sample and writes the existing stock slice grid.
Failures retain the old grid and report an error. Dense overflow uses the
existing best-effort real-attack policy, not an invented equal grid.
It is separate from SampleLAB's AUTO/COUNT/EVEN analysis.

SAMPLELAB
RECORD: record normally, stop at TRIM, press A to open SampleLAB.
LOAD: press B while Sampling is idle/stopped TRIM to open the native +Drive
browser; select a sample and confirm YES. Inside Lab, D opens SOURCE with
RECORD(TRIM) and LOAD FROM +DRIVE. RECORD(TRIM) does not start recording.
The initial region is the whole source. H press opens/closes SETTINGS;
closing applies pending analysis once. D rotation in SETTINGS selects CHOP/LOOP.
CHOP provides AUTO, COUNT1..64 and EVEN; manual E/F edits remain available.
Main A selects a region; A push fits it; B moves it, E/F edit its boundaries,
G zooms and G push/turn pans. F push is the existing one-level marker/analysis
undo, including from SETTINGS. E push deliberately does not trim the source.
LOOP retains its fine/coarse start, BPM/beats and fade tools.
H rotation chooses PLAY / SAVE / POOL / SAVE ALL / ALL POOL, YES executes.
NO cancels/backs out. Original sources and existing filenames are preserved;
Save As creates new source-derived names, skipping collisions. ALL POOL saves
each chop and assigns distinct empty native Pool slots. Normal stock TRIM,
recording/normalization, loading and saving delegate to their original paths.

PERSISTENCE / STOCK ROLLBACK
No Sound, Kit, Pattern or Project format/version/codec/layout changes.
SmartSlice uses the stock Sound slice rows. SampleLAB writes native sample
files and native Project Sample Pool references; its UI/edit/undo state is RAM.
Native batch SAVE and fresh-runtime Project LOAD preserve the complete Pool;
unmodified stock OS1.17 also reloads that mod-saved Project with the Pool exact.
These editing results use stock formats. Physical rollback and power-cycle
acceptance remain untested. Unrelated custom machines in existing All-in-One
Projects are outside this stock-based package and are not made stock-compatible.

LIMITS
Native imported +Drive PCM:48kHz,16-bit, mono/stereo. Source cap66s, subject
to free RAM. Native audition cap approximately10.92s stereo/21.84s mono per
region; longer regions can still save fully. Region minimum144frames/3ms.
No detector promises perfect musical cuts; inspect/audition and refine them.
No physical realtime, listening, ADC or full-device stability claim is made.

HARDWARE ACCEPTANCE — USE A DISPOSABLE STOCK-COMPATIBLE PROJECT
1. Boot; load/change samples normally and use stock TRIM/save. Check
   System > Credits/About opens, pages and returns normally.
2. Try C zoom/pan/reset on ordinary sample machines and G on SLICE at1x/2x/
   4x/16x/64x; fit a GRID/SLICE region with A. Switch samples/tracks and pages.
3. Run native SMART on a known16-hit sample; inspect/edit/audition the grid.
   Check fixed grids still work and failed analysis keeps the prior grid.
4. Open SampleLAB from a fresh recording and with LOAD FROM +DRIVE. Analyze,
   select/refine/undo, audition a chop and loop; SAVE and ALL POOL. Confirm
   originals remain unchanged and Pool entries are visible.
5. Save/reload the Project, power-cycle and reopen saved samples. Return to
   normal Recording/NORMALIZING. Test normal playback while editing and watch
   for stuck jobs, missing audio, UI hangs or timing changes.
Only Kári can accept these physical results and authorize public release.
No firmware has been flashed and nothing has been published.

EVIDENCE / SOURCE
MANIFEST.json binds the exact image, tests, preservation and remaining limits.
REPORT.txt separates hardware ancestry from offline evidence.
SOURCE_AND_EVIDENCE.zip contains sources and bounded test reports; no Elektron
OS, user sample audio, card or emulator snapshots. Build scripts retain exact
workspace/toolchain prerequisites; this is not a turnkey public Windows builder.
The local firmware is private. Public distribution must use a stock-input patch
or builder, with corresponding source and preserved LICENSE/NOTICE; do not
distribute Elektron OS bytes. Publication is not authorized by this delivery.

Modified sample-editing firmware by Kari/karigardarsson, derived from digikit.
digikit by Em D - https://github.com/ElekLabs/digikit
AGPL-3.0-or-later plus the original NOTICE. Not endorsed by Em D or Elektron.
