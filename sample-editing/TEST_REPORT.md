# Final Windows release package — 2026-11-05

Kári confirms that H now feels good and hardware testing passes. This explicit
acceptance applies to the existing firmware:
`880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06`.
This is USER HARDWARE-PROVEN by the user's report, not independently observed
device testing by the agent. No granular test checklist is invented.

The accepted local finalization changed `builder_gui.py`,
`HARDWARE_ACCEPTED=False` → `True`. The rendered builder no longer displays
the final-acceptance warning. Font10, Ready log, layout, attribution and `.syx`
default filename are unchanged. No firmware compilation or firmware modification.

Firmware source, all custom assets/objects, resource seal, native encoder and
`builder_core.py` compare byte-for-byte with the accepted H-fix distribution.
The firmware stays 1,882,144 bytes; no new DSP, encoder, parameter, format or
feature change. Existing candidate filename is retained for compatibility;
the file hash, not its label, identifies the accepted image.

Focused final checks pass:

- Native Windows Tk warning absent with acceptance flagTrue; candidate branch,
  font, Ready log, compact layout, notices and save-dialog defaults also checked.
- 18 builder composition/input/tamper/file-safety tests.
- Actual frozen EXE with system-only PATH produces the exact accepted image;
  nine rejection/file-safety cases pass, with no Python/developer runtime needed.
- Embedded resources read back exactly. Independent extracted source repeats
  tests, native Tk checks, seven object relinks and byte-exact stock-input output.
  Actual Syntakt firmware is rejected.
- Final ZIP CRC, complete file/source hash inventories and relative documentation
  links pass. All 36 native screenshots/enlargements and four GIFs are unchanged.
  No Elektron firmware, PCM, snapshots, keys or flash tools are distributed.

Previous 494 native checks, packaged boot/LOAD-analysis-save-pool journeys and
broader existing evidence remain valid for the identical firmware and are retained
as historical reports. Their old pending status is superseded by the dated user
acceptance record; it is not silently rewritten.

The protected H-fix firmware and previous ZIP are rehashed unchanged. Current
documentation, provenance, acceptance record and final builder/source inventories
are refreshed. Windows10/ARM64/clean VM, signing/SmartScreen/antivirus acceptance,
other DPI settings and manual interaction with this newly frozen EXE are not newly
qualified. Hardware acceptance does not imply those Windows compatibility checks.

The local release bundle is complete. Git commit/push is subsequently authorized for this public package. No flashing,
separate GitHub Release or Reddit post is part of that authorization.

## GitHub preparation

The public GitHub package preserves the accepted firmware exactly. The builder
JSON result hardware_tested field is corrected from false to true to match the
accepted image and GUI. This is status metadata only; firmware-generation logic,
assets and preferred firmware source are unchanged. The Windows EXE is repackaged
for that metadata correction. Actual frozen/source stock builds must still match
880d810f… exactly. Workstation prefixes in historical JSON/object toolchain
metadata are replaced with WORKSPACE/; historical original hash/status context
is retained, while current public inventories are regenerated. README/HOW-TO
now embed all four unchanged native GIFs and link screenshots/build instructions.
No proprietary firmware or private workstation paths are included.
