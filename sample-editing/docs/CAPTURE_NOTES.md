# Capture provenance and limits

Current candidate: `880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06`.
Preserved capture candidate: `6aac3f72381afddab50d4eac2864c32f625aaa1abee7123df1aa20efab2fe074`.
Intended baseline: `3b6386010cd228ba603dbef3e681c42ed6490f2020e61ae80012fd458c4cec76`.
The only new firmware change is shared H rotation threshold6 to2. The renderer,
H-push navigation and all actions pictured in these captures are unchanged.
All36 native/6x images and four GIFs are reused byte-for-byte; none depicts
H rotation. Original capture identities and limitations below are retained.

- `lab-*` and `drive-*`: native input captures from the preserved6aac candidate. The
  existing native emulator boots the packaged image and uses its installed
  loader/hooks. File creation/loading, H page cycle, analysis, A selection/fit,
  E/F trim and F marker undo run through native events. PCM and SamplerView
  recording data are supplied fixtures, not captured hardware audio.
- `info-*`, `settings-*`, `save`, `saved`, `pool`, `save-all`, `all-pool`:
  preserved6aac production renderer, unchanged in this update with explicit UI-state fixtures. These illustrate
  real implemented layouts, not independent operation completion evidence.
  Actual SAVE/POOL completion and exact exported PCM are separately tested in
  the packaged native workflow. Both kinds are recorded in SCREENSHOT_INDEX.json.
- `samplelab-trim-entry`: reused verified stopped-TRIM native entry capture;
  that stock-view branch is unchanged. Its recording waveform fixture is blank.
- SmartSlice screenshots/GIF: reused verified `247f4d8d…` native dialog/result
  captures. The exact SmartSlice module and relevant stock code remain unchanged.
  The result has 16 real native regions. The stock editor was opened separately;
  its waveform trace is absent in this CF-only emulator. This is an edited
  sequence, not an end-to-end recording of the SRC menu route. That physical
  route and a menu-list screenshot still require hardware confirmation.
- Universal Zoom screenshots: reused verified `247f4d8d…` renders; GIF uses
  `f239a2f6…` native C taps. Zoom code is byte-exact in the current candidate.
  Deterministic waveform/peak data and completed DSP waveform responses are
  supplied. Worker/queue/physical DSPI waveform acquisition is not exercised.
- CFADE screenshots: reused verified `247f4d8d…` native SLICE OFF/30 views.
  Native CFADE initializer/DSP and stock SRC controls remain unchanged.

Original 128×64 PNGs are in `images/native`; `images/6x` contains nearest-neighbor
768×384 copies. GIF displays use the same exact 6× pixels. All new GIF frames
are decoded and compared against their source PNGs. Titles and captions are
outside the unmodified display. Original SmartSlice/Zoom GIF bytes and hashes
are preserved. Edited pauses aid reading; loops imply neither automatic undo
nor operation speed. GIFs contain no audio. Media contain no firmware, state,
card overlay, raw requests or PCM.

No photographed hardware screenshots are represented as emulator output.
Kári reports that H feels good and hardware testing passes on2026-11-05.
The hardware acceptance record is separate from these emulator captures. No flashing or publication was performed.
