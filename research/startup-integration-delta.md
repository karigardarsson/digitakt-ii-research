# OS 1.16 DV startup integration: bounded delta

## Gates

- **Startup hook: PASS in natural emulated startup.** Stock bytes at
  `0x40133646` are `4e56ff6448d73cfc` (`link.w A6,#-156` and
  `movem.l D2-D7/A2-A5,(SP)`). Guest patch is
  `4ef9100050004e71`, trampoline linked at scratch `0x10005000`, and
  rejoin is `0x4013364e`. At that PC the full D0-D7/A0-A7/SR set,
  160 stack bytes, and eight EMAC fields matched stock. Entry, hook,
  and rejoin each hit once. Original source and firmware files were not edited.
- **Allocation and pointer lifetime: PASS for the measured window.** A
  natural priority-6 entry after allocator initialization made exactly one
  extra `malloc(8192)` call; returned `0x45057c40`, 16-byte aligned and
  8 KiB aligned relative to the 32 MiB buddy pool. Startup zeroed the block,
  wrote seed `0x51a6d20f` at pointer+`0x1ff0`, and published the pointer
  at guest-only `0x40311ff0`. The first write to that word was a failure
  marker; the second was the initialized pointer. For **at least 20,936,076**
  instructions after entry there were 0 later writes to the block, 0 frees
  of that pointer, and 0 TCD pointer writes into it. A saved longrun
  checkpoint restores the pointer and state. Synthetic repeat-entry made
  0 new allocations; synthetic NULL malloc returned to stock startup with
  marker `0xffffffff` and unchanged live count.
- **Native DV pointer path: PASS for synthetic function-level execution;
  natural DV frame execution remains BLOCKED.** The separate
  `native_dv_alloc.s` changes only state-base/seed loads and adds a safe
  zero-Q31 result for absent state. The known-good `native_dv.s` stays
  untouched. Relocated tests passed 96/96 phase bins, 70/70 stock WAVE
  0-6 differentials, and 432/432 Python-ColdFire voice ticks. A synthetic
  WAVE 7 tick **inside the same guest after natural startup** entered the
  native generator once, reached stock apply 48 times, initialized only
  voice 0 in the allocated block, and preserved the global seed. A
  failure-marker tick returned `0x00000000` Q31 and left DV-state unchanged.

## Ownership and divergence

The pointer word sits in the candidate persistent image cave
`0x403117c4..0x40312000`, before reset zeroing at `0x40312000`.
Byte-level raw 4-byte pointer scan found zero image literals into that
cave; decoded static reference scan found zero hits with **96.72%**
coverage. Those scans do not exclude computed or undecoded references.
Only the two DV-hook writes hit the pointer word in the measured window.
The 110-byte startup trampoline and 874-byte pointer-fed generator were
run from ephemeral scratch code; executable cave placement is not proven.

At rejoin, stock and hooked PIT0/PIT2 counts and timer clock matched.
Allocator live count differed by exactly +1, and one bitmap byte differed
at offset **9215**; registers, stack and EMAC were equal. The same single
bitmap byte differed at the end of the 20M window. Firmware RNG, primary
and secondary LFO arrays, and priority-6 TCB hashes were equal. At equal
total instruction count (23,556,876 from the pre-entry snapshot), both
ended at PC `0x40002a18` with two tasks, but PIT0 was **91 stock / 92
hooked**, and final CPU registers differed. The first PIT0 timeline
separation occurred between approximately 16.07M and 16.47M instructions
from the pre-entry checkpoint. This later scheduler phase difference is
not yet explained; it is not waived as stock equivalence.

**Next discriminating test:** replay stock and hooked from the same
pre-entry checkpoint with a narrow PIT0 rearm/ISR and task-switch trace
around 15.44M-16.48M instructions. Compare timer register writes,
interrupt delivery and the first different task PC to determine whether
the one-tick separation is the expected one-shot instruction cost or an
unintended firmware-owned state change. Then test executable placement
in the persistent cave before any integrated image work.

Evidence: static/byte scan, natural emulated startup, synthetic Unicorn
function-level DV, and host oracle. Hardware, natural LFO frame tick,
audio, flashed executable lifetime and full device lifetime are untested.
This remains offline work toward one future integrated build and one
planned flash.

New isolated sources: `startup_alloc.s`, `startup_hook_probe.py`,
`startup_edge_probe.py`, `native_dv_alloc.s`,
`native_alloc_regression.py`, `startup_native_link_smoke.py`, and this
report. Machine-readable results and the pre/post startup snapshots are
under `out/dt2-dv-offline/`; the primary files are
`startup-hook-20M-deep.json`, `startup-rejoin-diagnostic.json`,
`startup-edge.json`, `native-alloc-regression.json`,
`startup-native-link-smoke.json`, and `post-startup-hook-20M.snap`.
