# All-in-One panel polish + SY FANG (private, DT2 1.17)

2026-10-25 [D]. This is a separate, single-agent implementation batch after
CHIMERA `c1f36dd`. No CHIMERA source, event protocol or renderer is changed.
No flash, device access, publication or release update is performed.

## Inputs and ownership

- Immutable final FUS2 Repair4 image: `66f022bf5640afb49c2dc5b02ec9b0cb9a8b3def30c84536e92154a29a9078fd`.
- Actual imported source: `plaits-va-run02/out/spacejam-hopt2-playable/out/all-in-one-plaits25-e48/source`,
  pinned spacejam `e48d0a2a83f055c549391173247cea02c571927f`, plus the exact retained
  Repair4 staged modules and private Musical WAVE V4/Zoom/UI overlays.
- Repair4 is **offline-tested**, not newly hardware-accepted. Earlier Plaits25
  hardware and TWIN/Fusion hardware findings retain their narrower scope.
- Type15 stays reserved for CHIMERA; new permanent type16 is SY FANG.
  CHIMERA is NOT silently included in this firmware.
- Preserve existing AGPL/DigiKit notices and upstream Plaits attribution.
  No proprietary sections, compiled firmware or user audio belong in Git.

## Panels

`panel.c` wraps the existing final `fusion_widget` and keeps all native labels,
INFO formatters, lock outlines, TUNE drawing and non-target machine fallbacks.
An immutable, read-only CF segment at `0x47eb0000` owns graphics only.
Every loader/module allocation is checked for collision with it, the unchanged
Plaits UI at `0x47ea0000`, and the unchanged DV/Zoom code at `0x47ec0000`.

TWIN and legacy FUSE now use the same sample-left grouping as FUS2:

| | A | B | C | D | E | F | G | H |
|---|---|---|---|---|---|---|---|---|
| TWIN | SMP1 | TUNE | FILT1 | DEC1 | SMP2 | TUNE2 | MIX | LEV |
| FUSE | SMP1 | TUNE | SYNC | FUSE | SMP2 | TUNE2 | MIX | LEV |
| FUS2 | SMPA | TUNA | SYNC | FUSE | SMPB | TUNB/TRAN | amount | blank |

This changes only the visible TWIN/FUSE knob order through the existing SRC
parameter accessor. Original descriptor IDs, stored fields and locks remain.
FUS2's Repair4 page order and semantics stay unchanged. Samples show A/B,
slot0..1023 and the stored forward/reverse arrow, not an invented live cursor.
Mode panels show BLND/RING/DUCK/MRPH/GATE plus a small relationship schematic.
Amount/response rectangles show actual values, position rails and explicit
center references where appropriate. Response readouts use ms or seconds;
full Repair4 INFO text and REL explanation remain native.

The two owned RCVR/AUTO value formatters use at most four characters during
native pre-widget drawing (rounded0.1s above99ms). This prevents fragments of
the old wide millisecond label outside the new box. Storage and time equations
are unchanged; the custom box can show finer seconds. Executed numeric traces
also verify TWIN MIX0/63/64/127 from raw0/16256/16384/32512, with A6 preserved.

Plaits25 already has the accepted rectangular 2.1 indicators, native TUNE and
sole lower DX7 patch label. They are deliberately retained, not advertised as
new DSP/UI functionality or redrawn for novelty.

All diagrams are parameter schematics. They do not measure audio, show the
sidechain detector, display the current AUTO phase or claim playback activity.

## SY FANG

Original C99 bass engine, not a renamed Plaits VA/VCF engine:
polyBLEP saw/pulse blend, continuous triangle sub in the upper WAVE half,
four nonlinear feedback-coupled low-pass stages, dedicated filter envelope,
velocity-dependent accent and pitch slew. Per-track state44bytes,16tracks.
The native AMP, track LEVEL, FILTER/FX and output routing remain downstream.

| Encoder | Function | Stored mirror |
|---|---|---|
| A | stock TUNE | 25 |
| B | WAVE: saw toward pulse/sub | 26 |
| C | CUT: filter base cutoff | 27 |
| D | RES: feedback resonance | **29, not28** |
| E | ENV: filter-envelope depth | 31 |
| F | DEC: filter-envelope time constant,15ms..1.5s | 32 |
| G | GLID: pitch slew, OFF..400ms time constant | 33 |
| H | BITE: nonlinear drive + velocity accent strength | 34 |

New controls0..127. First note snaps to pitch; subsequent pitch changes use
GLID. This is continuous pitch slew, **not a claimed native legato detector**.
Filter envelope retriggers on the actual DSP trigger latch. Note-off leaves
stock AMP release in charge; no arbitrary fade or oscillator reset is added.
Inactive/reset clears private state. Reset cannot restart a stale ACTIVE voice
without a new trigger. Note frequency is safely bounded to7.2..8640Hz.

### Two shared-field traps explicitly avoided

1. Mirror28 is the native sample selector. RES is an appended descriptor whose
   real mirror is29, exposed on knob D through `fang_param`. Machine `f3` MUST
   remain0: putting the RES descriptor there would make `mf_typearg` also
   return it for mirror28. `fang_typearg` handles29 only; the two existing
   bank split/reconstruction caller sites bypass their shadow handling for
   type16 only. Note allocation's shared audio predicate remains stock.
2. Native DSP frame field9 is also SRC gain. `BITE` must not vary that gain.
   The existing SW`1c2654` hook has one owner and chains through Repair4 for
   all non-FANG types. Type16 receives unity at the **native gain consumer**,
   while the unmodified raw field still reaches the FANG renderer.

The existing SDK even/odd stereo wrapper is reused: even voice computes32
48kHz floats, odd copies them. It saves I6/I7/L6/L7, switches to its private
linear4096-byte stack in the proven order, then returns through the direct
stock continuation. No final-master interception is added.

### Build/test discipline

`build.py fang` generates a private module catalog from Repair4, compiles only
the new module and performs a genuine source/object rebuild comparison.
`build.py compose` reuses existing package/composition tooling and exact
DV/Zoom/Plaits UI payloads. Existing firmware/source inputs are not overwritten.
`verify.py all` runs focused tests serially with complete per-phase logs.
Individual phases can be resumed without redoing successful cases.
`deliver.py` refuses promotion unless every final-image gate passes, then
copies one hash-verified SYX to a new single-file FLASH_THIS folder.

Use the already-installed Ubuntu WSL2 environment, HOST `.venv`, retained
GCC16.2.0/m68k-elf and Binutils2.47.20260726, and pinned Selache
`2b26d3b75c53063575bc5c820fa0d38879335187`. Build import root is HOST; the
legacy packaging dependency additionally needs DV's existing venv site-packages.
UI tests use the pinned SRC+tests import roots, as in Repair4.

Internal work is under `out/all-in-one-polish-fang-r4-work/`; earlier work
directories remain as diagnostic evidence, not additional flash candidates.
Initial drafts exposed implicit integer-literal-to-float conversion shapes
(`2a_short:18ca000`) refused by the conservative encoding gate. Explicit float
literals remove those instructions without changing the synthesis equations.
No encoding approval or allowlist was added.

## Evidence boundary

Host sound tests, synthetic-frame native DSP calls, native CF converters/UI
calls, container/bootstrap validation and physical hardware are separate tiers.
Native application of RES LFO is tested; do not turn that into an unobserved
hardware destination-menu claim. Payload round-trips are not complete Project
filesystem/power-cycle/Song Mode tests. Native frame/ACTIVE fixtures are not
natural panel/MIDI/RTOS-to-DAC playback. Silicon timing, musical feel and
full-load interaction remain hardware-untested.

Final `final-gate.json` is authoritative for completed tests, candidate identity
and delivery. No firmware is called playable or hardware-PASS here.

## Final offline checkpoint

Complete image SHA-256:
`32de4571df62b4baa6012afc33396a5d3166519cda124383866ea6acb66c92a9`.
35 native renderer blocks match the host oracle, including all seven new
controls' endpoints. Maximum28,322 interpreted instructions; private C-stack
high-water248/4096bytes. This is not silicon headroom.81 host extremes remain
finite, with peak0.87876.24 native encoder mappings, locks/modulation and
Sound/Pattern converter fixtures pass. Seven existing integration tests pass.
All25 Plaits PCM/register comparisons and stock SAMPLE PCM/record/register
comparisons pass. Stock0..6 and25 Plaits framebuffers are exact against Repair4.

Final graphics are actually executed CF captures in the work directory's
`final-screens/`, not designs/mockups. Container/HMAC/16,347 packet checks,
encoding and SHARC bootcheck pass. Existing protected sections2/4/5/8,
DV/WAVE/Zoom payload, original intro and old Plaits GUI remain exact. Existing
DSP objects are identical; final placed differences are recorded relocations.
Gain/DSP-regression evidence is explicitly reused only after full decoded DSP
section equality; the35-block FANG native test ran on this final image itself.

The separate candidate does not include CHIMERA. It preserves that source and
type15 reservation; L2 spare96,976B cannot hold the current104,442B CHIMERA
RX-only projection. No automatic capacity redesign is undertaken.

Following Kári's separately authorized installation, first check stock SAMPLE
and Drive+, then select `SY FANG` on an audio track and trigger a note. Start
with GLID0 and middle CUT/RES/ENV/DEC/BITE. Hardware must establish actual
note/AMP/DAC delivery, musical behavior, Project/Song and load limits; the
offline fixture does not establish any of those hardware results.
