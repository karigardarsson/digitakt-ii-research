# Final builder UI and shared H rotation correction — 2026-11-05

Candidate: `880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06`.
Offline validated; final hardware acceptance remains required. No flash/publish.

## Justified changes

Hardware feedback identifies H rotation as too sluggish, not H-push navigation.
Current source traces both Waveform and Settings H to the same `knob==7` branch
in `sl_lab_encoder`: six eligible directional events, already filtered by the
existing three-tick guard. Acceleration magnitude is deliberately ignored.
The only production firmware edit changes that six-event threshold to **two**.
This gives three times the previous eligible-event responsiveness while still
requiring two events per adjacent action. CHOP actions remain PLAY/SAVE/POOL/
SAVE ALL/ALL POOL (0..4); LOOP remains PLAY/SAVE/POOL (0..2). Direction reversal
clears the partial gesture. The existing tick gate and one-step clamp are retained.

Info rotations remain read-only. H-push Waveform/Settings/Info navigation is
unchanged. The renderer already shows H's selected action above its label;
no page-specific workaround or invented H mapping was added.

Builder GUI: explanatory text is now explicit Segoe UI10, initial disabled log
says `Ready. Select your stock OS 1.17 firmware to begin.`, and current candidate
text keeps `Final hardware acceptance required.`. `HARDWARE_ACCEPTED=False` is
an explicit release-maintainer flag; change it only after Kári accepts the exact
firmware. The accepted branch removes that warning. It is not an end-user switch
or a claim that acceptance has happened.

Save dialog still suggests `DT2_OS117_SAMPLE_EDITING_RELEASE_CANDIDATE.syx`, with
`.syx` default extension/filter. Stock-file selection supplies the same sensible
output name beside the input. Layout, notices, license and attribution preserved.

## Focused verification

- Preserved6aac candidate's current Lab source reproduces byte-exact before
  editing. Only `.text.sl_lab_encoder` changes, with identical object relocations.
  Source diff is exactly the threshold expression. H-push/key, tick, renderer,
  processing, START/END formula, other encoders and state layout remain exact.
  Lab28,816B, static BSS144 unchanged; no additional heap state or DSP change.
- All six other CF modules and stored/plain non-MAIN sections byte-exact.
  Existing39 hooks/seven segments, container checksums/HMAC/bootstrap and inverse
  stock-prefix composition pass. No persistent Sound/Project format change.
- **494 focused native state checks**: existing zoom/E/F precision/held/native
  acceleration/reversal/bounds/A/B checks plus H two-event response on Waveform
  and Settings, both directions, CHOP/LOOP bounds, ignored magnitude, rapid
  packet tick filtering and reversal. Info/H/NO navigation preserved.
- Actual packaged loader verifies all modules/hooks. Native +Drive LOAD →
  analysis → PLAY → SAVE → POOL journey passes, including exact exported PCM,
  source preservation and context retirement. Supplied PCM/recording fixture;
  this is emulator proof, not physical recording or audio acceptance.
- Native Windows Tk geometry760×560: font10, initial log/disabled state, readable
  log area, ordered widget bounds/footer, candidate and accepted text branches,
  actual dialog options and input-derived default filename pass. Other DPI/OS
  layouts and manually operated final frozen GUI are not newly qualified.
- **18 builder tests**, all seven object relinks, embedded resource readback,
  isolated frozen EXE stock-input generation and nine rejection/file-safety cases.
  Independent extracted source emits the identical candidate; actual Syntakt
  input is rejected. Generation code differs only in the pinned output hash.
- Final ZIP CRC/inventories/links verified. No firmware, PCM, emulator state or
  flashing tools are distributed. Existing36 screenshots and four GIFs reused:
  renderer and pictured H-push/other control paths are unchanged; no GIF depicts
  H rotation. HOW-TO response text updated from six to two eligible events.

Test-helper corrections during this pass fixed unsigned negative-state comparison
and a one-based native knob fixture number; neither changed production firmware.
Initial Tk geometry check needed one normal event-loop update after mapping.

## Preservation and remaining hardware acceptance

6aac3f72 firmware and c0c07b74 public ZIP preserved; intended3b638601 and hardware
359b6189/e0867dc7 artifacts also rehashed unchanged. Previous broader native/DSP/
SmartSlice/recording/rollback evidence is retained as historical evidence, not
rerun or advertised as new hardware proof. Existing feature set remains Universal
Zoom + SmartSlice + SampleLAB + CFADE.

On hardware, turn H slowly/quickly on Waveform and Settings in CHOP/LOOP, check
visible adjacent action changes, reversal and bounds; verify H-push/NO navigation
and accepted START/END response. This new threshold is not yet hardware accepted.
All outstanding release checks in HARDWARE_ACCEPTANCE.md remain, including native
SmartSlice weak/failed detection then LENGTH64, audible CFADE/recording, stock
save/reload and rollback. Windows10/ARM64/clean VM/signing/SmartScreen remain
untested. No flashing, publication, new agents or unrelated changes.

Modified production files: `source/sample_lab/workflow.c` (one expression) and
`builder-source/builder_gui.py` (font/readiness/status text only). Builder manifest,
Lab asset/object/map/source, resource seal, provenance, output hash, documentation,
tests and release inventories are refreshed to match. Firmware-generation logic
and other feature source are unchanged.
