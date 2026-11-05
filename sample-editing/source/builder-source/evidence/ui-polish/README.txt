DIGITAKT II SAMPLE EDITING — FINAL UI POLISH — 2026-11-03

One updated hardware-test candidate:
HARDWARE_TEST/DT2_OS117_SAMPLE_EDITING_UI_POLISH.syx
SHA-256:52992b9d37d854bbcf9401096ec27db256bc64d2660a362a34686b7f1331726b

Preserved USER HARDWARE-tested baseline:
../DT2_SAMPLE_EDITING_OS117/HARDWARE_TEST/DT2_OS117_SAMPLE_EDITING.syx
SHA-256:d7cbee11101c81fa54b754880083d5130e01ffdadcac254ce07e5e4709a33f93
Kári reports all tested functionality works. That report does not establish
exhaustive physical coverage; this exact UI-polished image is hardware-untested.

ONLY THREE UI CHANGES
1. Ordinary sample machines keep the ZOOM caption and the existing navigator
   zoom indicator. The duplicate numeric value above the caption is removed.
   SLICE pixels/control behavior remain identical to the tested baseline.
2. Idle RECORD screen B:LOAD moves from native baseline y56 to y46, below
   the header and above the THRES value. Native THRES and controls are intact.
3. Stopped post-recording TRIM replaces its upper-right arrow with A:LAB.
   Actual key41=A push opens Lab only in stopped TRIM. Y:SAVE, Fn+D:DEL
   and Fn+H remain pixel-exact. The hint describes the existing control.

Native128x64 renderer/font measurements: B:LOAD21px wide at x100..120;
A:LAB17px wide at x104..120. Both fit with a seven-pixel right margin.
Screenshots are native-emulator captures, supplied at original128x64 and
nearest-neighbor6x for readability. They are not photographs of the device.

VALIDATION
Only .text.sl_lab_draw and .text.zoom_cell change in unrelocated compiler
objects, plus the Lab label string section. Every other function's object
bytes are exact; relinked addresses are updated in the existing hooks.
No new hooks, state, algorithms, DSP, formats or unrelated features.
SmartSlice, both shared detectors, Credits and every non-MAIN section are
byte-exact. BSS allocation is unchanged. The baseline is unchanged.
Actual final-image loader readback and native recorder/trim pixel comparisons
pass. THRES and stock shortcut pixels outside the requested regions are exact.
Native caption comparisons at1x/2x/4x/64x, tap/hold/reset/pan, opposite input,
pending viewport retention and SLICE full-frame parity at1x/2x/4x pass.
31 GRID/SLICE fit cases pass. Actual +Drive browser/LOAD/Smart Chop/preview/
Save As/Pool, supplied-PCM recording, manual trim/LOOP save, undo/fit, original
preservation, failure/cancel cleanup and native SMART16->16 pass.
Queue/DSPI/ADC services retain the existing explicit test-fixture seams.

HARDWARE ACCEPTANCE
- Ordinary sample machine: one level indicator remains and follows C zoom;
  check pan/reset. Check SLICE G zoom/pan/reset remains unchanged.
- RECORD: B:LOAD is fully readable; THRES value remains visible while changing
  the threshold; B still opens +Drive loading.
- Stop a recording: A:LAB is clear; A opens Lab; Y:SAVE/Fn+D:DEL/Fn+H remain
  visible. Briefly exercise Lab editing, preview, save and Pool as before.

No flash, device operation, new agent or publication. Public release still
requires Kári's final acceptance and explicit publication authorization.
Original source/evidence and LICENSE/NOTICE are included locally.
