DIGITAKT II SAMPLE EDITING — SMARTSLICE EXCEPTION FIX — 2026-11-03

ONE HARDWARE-TEST CANDIDATE
HARDWARE_TEST/DT2_OS117_SAMPLE_EDITING_SMART_FIX.syx
SHA-256:e0867dc7367c8778da49995c8e8407c044f3ed72a2383d3bf9fcd197515b89a2

ROOT CAUSE ESTABLISHED OFFLINE
The SMART result/error notification released its raw popup object as if it
were the separate shared-owner control block. The native release decremented
the popup's encoder-interface vtable address. The next encoder event tried
to dispatch through the corrupted interface, matching the photographed
instruction0x4011B5F8. Both success and error notifications used this path.
The corrected call releases &message.control. Improved transient detection,
background filtering and dense overflow fallback remain exact.

ADDITIONAL NARROW SAFETY GUARD
Stock fixed-grid LENGTH64 creates zero-width rows if a nonempty sample has
fewer than64 frames. The mod now refuses that impossible request, displays
the existing GRID KEPT error and retains the old grid. Normal fixed grids
and stock CLEAR keep their native path. A normal sparse sample can still
use the stock fixed64 grid even when SMART finds fewer than64 attacks.

PRESERVATION
The current UI-polished pack52992b9d... and previous hardware-tested
d7cbee11... remain unchanged. Zoom, SampleLAB, Credits, DSP, file loading,
all transient algorithms and persistent formats are unchanged in this image.
Only the SMART module's notification cleanup and impossible-grid guard change,
with relocation of its existing four references. No new hooks or static RAM.

VALIDATION
- Shipped native SMART error + real non-null parent/popup + next UART encoder
  reproduces corrupted vtable4021D924->4021D923 and a native dispatch fault.
- Correct owner release preserves the interface and input continues normally.
- Final encoded-image loader readback passes without production code injection.
- Nine native-file cases: silence, two clear hits,1/63/64/143/144 frames,
  66-second stereo with seven attacks and96-attack overflow.26 attempts total.
  All native sample metadata loaded naturally; no metadata seam was used.
-32 repeated silent SMART-failure -> LENGTH64 cycles,64 attempts total,
  with actual popup owners, UART encoder and NO input: PASS.
- Every accepted grid is1..64 rows, ordered, non-overlapping and has
  0<=START<END<=sample frames. Failed SMART/impossible requests retain the
  complete old grid. Few-hit SMART remains two regions; feasible fixed64 is64.
- +Drive LOAD/Smart Chop/preview/Save As/Pool, recording, undo/fit, LOOP save,
  original preservation, failure/cancel cleanup and original16-kick SMART pass.
- Container/bootstrap and exact non-MAIN stored/plain section preservation pass.

EVIDENCE LIMITS
The photo shows vector03 at4011B5F8. The emulator reports vector02 when the
odd, corrupted jump target is unmapped. The actual dispatch instruction and
corrupting owner-release mechanism are reproduced; an identical vector label
or physical exception-frame reproduction is not claimed.
New firmware remains HARDWARE UNTESTED. No listening, ADC, electrical bus or
silicon deadline claim is made. See REPORT.txt and MANIFEST.json for bindings.

SHORT HARDWARE PROCEDURE — USE A DISPOSABLE PROJECT
1. On native SLICE, use sparse or silent material. Request SMART. After a
   GRID KEPT message, turn an encoder immediately while the popup is visible;
   dismiss it, reopen the grid selector and request LENGTH64.
2. Repeat20–30 times, alternating successful/unsuccessful SMART and fixed64.
   Check no exception, stuck popup or lost controls. Inspect/edit/audition the
   resulting grid; swap samples/tracks and repeat.
3. Check known16-hit material still gives the expected SMART result. A file
   shorter than64 frames should refuse fixed64 and retain its prior grid.
4. Briefly check Zoom/pan/reset and SampleLAB LOAD/record/edit/save/Pool, then
   save/reload the disposable Project.

PUBLIC RELEASE REMAINS ON HOLD until Kári verifies the exception fix on the
Digitakt II and explicitly authorizes release. Nothing flashed or published.
No new agents or hardware operations were used.

Modified firmware derived from digikit.
digikit by Em D - https://github.com/ElekLabs/digikit
AGPL-3.0-or-later plus original NOTICE. Not endorsed by Em D or Elektron.
