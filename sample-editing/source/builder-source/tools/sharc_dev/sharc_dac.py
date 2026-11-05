"""The Digitakt II SHARC+ audio task's DAC-facing output format: ring A.

Static evidence (`out/sections/dt2-1.16/section_7_BLOB.bin`, sha
`0f514a12...`; see `docs/findings/06-sharc-engine-and-startup.md`'s "The
audio path from the task loop to the rings" and "Rings [V]", both already
marked verified by two agents):

* Command 3 ("render") of the audio task's block handler (`FUN_1c74cd`,
  dispatch table `DM(0x25f7b0)`) ends by calling `FUN_1c74a1` (sw
  `0x1c74a1`-`0x1c74cd`; call site sw `0x1c7734`, inside the command-3 body
  at sw `0x1c7671`-`0x1c7740`) right after `FUN_1c2b24` (call at sw
  `0x1c771e`) has filled the master-mix buffer at `DM(0x25f180)`.

* `FUN_1c74a1` itself hardcodes its own source pointer, `I3 = 0x25f180`
  (sw `0x1c74b0`) -- the master mix is NOT passed as an argument, only the
  two destination pointers (`R4`, `R8`) are. Its `DO ... UNTIL LCE` loop
  (sw `0x1c74b5`-`0x1c74c1`, trip count 32) reads `DM(I3, M6)` then
  `DM(I3 + 31)` each iteration. `I3` auto-increments by `M6` (=1 word)
  between the two reads; SHARC+'s byte address space scales a DAG modify's
  own offset/index by the access size
  (`out/refs/sharc-plus-prm/all.txt`, "Enhanced Modify Instruction for
  Address Scaling", p.6-9: a `word`-size (4-byte) modify of literal N
  advances the pointer by `N*4` bytes) -- so the second read's own
  `+31`-word (`+124`-byte) displacement, applied to `I3` AFTER its own
  post-increment, lands exactly on `0x25f180 + 0x80 + 4*k` for loop
  iteration k, i.e. on `DM(0x25f200)`'s own k'th word. `0x25f200 -
  0x25f180 == 0x80` bytes == 32 words, so the master mix is 32 PLANAR L
  floats (`DM(0x25f180)`) followed by 32 PLANAR R floats (`DM(0x25f200)`),
  plain IEEE float32, NOT interleaved -- matching
  `tools/sharc_dev/sharc_harness/`'s `read_master_mix()`, which already reads 64
  contiguous floats from `DM(0x25f180)` as one buffer.

* Each of the two samples read per iteration is converted with the ALU's
  documented scaled float-to-fixed idiom: `out/refs/sharc-plus-prm`
  (toc.md page 67, "Fixed-to-Float Conversion Instructions with Scaling"):
  "`Ry = 31; Rn = FIX Fx BY Ry; /* fixed-point 1.31 format */`".
  `FUN_1c74a1` sets `R2` (the `Ry` operand) `= 0x1f = 31` at sw `0x1c74b3`,
  matching exactly; the compute field decodes to ALU opcode `0xD9`
  (`fix_by`, PRM Table 18-5, `out/refs/sharc-plus-prm` p.425-427).
  1.31 fixed-point IS Q31: signed, full scale `+-1.0`,
  `value = raw_int32 / 2**31`.

* The two conversion results are stored through two DIFFERENT pointers
  that start only `R13 = 4` BYTES (one word) apart -- a plain register add
  (sw `0x1c74a6`/`0x1c74ac`, `R13` set to the literal `4` once at sw
  `0x1c7506`/reused at the call site `0x1c7737`; NOT a DAG modify, so NOT
  scaled by access size) -- but each pointer's own per-iteration
  post-modify (`+2`, a 'word'-size DAG modify, so `2*4 = 8` bytes = 2
  words per iteration per pointer) advances it past the OTHER pointer's
  next slot. Net effect: the two stores land at word offsets
  `{0, 2, 4, ...}` (from `I4`, the L result) and `{1, 3, 5, ...}` (from
  `I5 = I4+1 word`, the R result) of the SAME destination half -- a clean
  L,R,L,R,... Q31 interleave, one 32-bit word per channel per sample,
  32 stereo samples = 64 words = 256 bytes per half.

* The destination base (`I4`, `FUN_1c74a1`'s own `R4` argument) is
  `ring_a_base + (ring_flag_bit << 8)` (sw `0x1c7728`-`0x1c7734`; the shift
  amount `R11 = 8`, set at sw `0x1c7504`): `ring_flag = DM(0x25f780) & 1`
  selects which 256-byte half of ring A (base `DM(0x261cc8)`) the block
  handler is about to render into. `DM(0x261dc8) - DM(0x261cc8) == 0x100`
  bytes matches this exactly (`docs/findings/06`'s "Audio output buffers":
  "Ring A: head 0x2620c8, buffers 0x261cc8/0x261dc8, 256 B").

Dynamically confirmed (2026-09-25, lane C1 -- see
`docs/findings/06-sharc-engine-and-startup.md`'s dossier and the lane's own
report for the full transcript): an isolated `sharc_run.Runner.fresh_call`
at `FUN_1c74a1`'s own entry (sw `0x1c74a1`), given a FRESH master-mix
buffer poked with distinct, unambiguous per-index float values
(`L[k] = 0.1 + 0.01*k`, `R[k] = -0.2 - 0.01*k` for k in 0..31) and
`R4`/`R8` pointing at a scratch destination, reproduces EXACTLY this
format: 64 output words, word `2k == round(L[k] * 2**31)` and word
`2k+1 == round(R[k] * 2**31)`, both magnitude and sign correct to float32
rounding. This rules out a sign or stride bug in the conversion routine
itself (`tools/sharc_dev/sharc_core`'s `fix_by`/opcode-0xD9 execution, and
`FUN_1c74a1`'s own addressing) as the cause of the "not a clean tone"
finding in a full continuous render -- see that report's own "stage where
the tone breaks" section, which places the break at or before the master
mix (`DM(0x25f180)`), one stage upstream of everything this module reads.

The functions below are this project's one reader for the ring-A/DAC
format (replacing `tools/sharc_dev/sharc_harness/`'s former hand-rolled interleave
logic in `read_ring_a()`) and a stereo WAV writer to go with it.
"""

from __future__ import annotations

import struct
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# --- Format constants (see module docstring for citations) -----------------

#: DM address of ring A's first half (the "flag bit == 0" half). The
#: second half is this plus RING_A_HALF_BYTES.
RING_A_BASE = 0x261CC8

#: Bytes per ring-A half: 32 stereo Q31 samples, interleaved L,R = 64 words.
RING_A_HALF_BYTES = 0x100

#: Stereo (L, R) sample pairs per ring-A half.
SAMPLES_PER_HALF = 32

#: DM address of the ring-select flag block_handler toggles (bit 0 only)
#: after every render. The half the frame that JUST returned wrote is
#: `DM(RING_FLAG_ADDR) ^ 1`, not the current value -- see
#: `ring_half_just_written()`.
RING_FLAG_ADDR = 0x25F780

#: DM address of the master-mix input to FUN_1c74a1's own conversion:
#: 32 planar L floats (IEEE float32) followed by 32 planar R floats --
#: NOT interleaved (see module docstring's second bullet).
MASTER_MIX_BASE = 0x25F180
MASTER_MIX_CHANNEL_WORDS = 32
MASTER_MIX_R_OFFSET_WORDS = 32  # DM(MASTER_MIX_BASE + 0x80) == R[0]

#: The scale-by-2**31 "Rn = FIX Fx BY Ry" idiom's own Ry value (PRM p.67;
#: see module docstring), and the resulting full-scale divisor.
FIX_BY_SCALE_EXPONENT = 31
Q31_FULL_SCALE = 1 << FIX_BY_SCALE_EXPONENT

#: The conversion routine itself (static + dynamic evidence above).
CONVERT_FUNC_ENTRY_SW = 0x1C74A1
CONVERT_CALL_SW = 0x1C7734  # inside the command-3 render path

#: DmRead(address) -> unsigned 32-bit int, or None if unmapped -- the one
#: adapter every function below needs, so this module never has to import
#: sharc_core/sharc_trace itself (tools/sharc_dev/sharc_harness/ supplies a small
#: closure over Runner.state instead; see its own read_ring_a()).
DmRead = Callable[[int], "int | None"]


def q31_to_float(raw: int) -> float:
    """One 32-bit word, as an unsigned int (mask with 0xFFFFFFFF first if
    it might be signed already), to its 1.31 fixed-point value in
    [-1.0, 1.0) -- the inverse of the firmware's own "Rn = FIX Fx BY 31"
    (see module docstring)."""
    raw &= 0xFFFFFFFF
    signed = raw - 0x100000000 if raw & 0x80000000 else raw
    return signed / Q31_FULL_SCALE


def float_to_q31(value: float) -> int:
    """The firmware's own forward direction ("Rn = FIX Fx BY 31"): a float
    to a signed 32-bit 1.31 integer, clipped (not wrapped) to the
    representable range at +-full-scale. This is a reference
    implementation for tests and cross-checks (the isolated-call test the
    module docstring cites is the actual ground truth for what the
    firmware does; this function does not replace it)."""
    scaled = round(value * Q31_FULL_SCALE)
    limit = Q31_FULL_SCALE - 1
    return max(-Q31_FULL_SCALE, min(limit, scaled))


def ring_half_base(flag_bit: int) -> int:
    """RING_A_BASE, or the other half when FLAG_BIT's bit 0 is 1."""
    return RING_A_BASE + (flag_bit & 1) * RING_A_HALF_BYTES


def ring_half_just_written(ring_flag_value: int | None) -> int:
    """The byte offset (0 or RING_A_HALF_BYTES) from RING_A_BASE of the
    half the frame that just returned wrote, given the CURRENT (post-
    toggle) value of DM(RING_FLAG_ADDR) -- see module docstring's "Task
    loop" bullet. `None` (never resolved -- e.g. a fresh, unmapped Runner)
    reads as half 0."""
    if ring_flag_value is None:
        return 0
    return ring_half_base(ring_flag_value ^ 1) - RING_A_BASE


@dataclass(frozen=True)
class RingAFrame:
    """One ring-A half's own 32 (L, R) Q31 samples, decoded.

    `left`/`right` are float in [-1.0, 1.0) (`q31_to_float` of the raw
    words); `left_raw`/`right_raw` are the signed 32-bit integers
    themselves, for a caller that wants to cross-check the conversion
    bit-for-bit (as the lane report's isolated test does) instead of
    round-tripping through float."""

    half_written: (
        int  # byte offset of this half from RING_A_BASE (0 or RING_A_HALF_BYTES)
    )
    left: list[float]
    right: list[float]
    left_raw: list[int]
    right_raw: list[int]

    def interleaved_q31(self) -> list[int]:
        """The 64 raw words in their own on-chip order (L0, R0, L1, R1,
        ...) -- e.g. for a byte-exact comparison against a watchpoint
        log."""
        out: list[int] = []
        for left, right in zip(self.left_raw, self.right_raw, strict=True):
            out.append(left)
            out.append(right)
        return out


def decode_ring_a_words(words: Sequence[int | None]) -> RingAFrame:
    """WORDS: the 64 raw (unsigned 32-bit, or None where unmapped) values
    already read from one ring-A half, in on-chip order (index 2k = L[k],
    index 2k+1 = R[k] -- see module docstring). A None word decodes as 0
    in both the float and raw lists (matching a real SHARC+ SRAM read of
    never-written memory; see `sharc_core.memory._dm_read`'s own
    `explicit_memory_model` default of 0). Returns a RingAFrame with
    `half_written=0`; a caller that knows which half this is should set
    that field itself (`read_ring_a_pcm()` does)."""
    if len(words) != 2 * SAMPLES_PER_HALF:
        raise ValueError(
            "decode_ring_a_words: expected %d words, got %d"
            % (2 * SAMPLES_PER_HALF, len(words))
        )
    left_raw = []
    right_raw = []
    for i in range(SAMPLES_PER_HALF):
        l_word = words[2 * i] or 0
        r_word = words[2 * i + 1] or 0
        l_signed = l_word - 0x100000000 if l_word & 0x80000000 else l_word
        r_signed = r_word - 0x100000000 if r_word & 0x80000000 else r_word
        left_raw.append(l_signed)
        right_raw.append(r_signed)
    return RingAFrame(
        half_written=0,
        left=[v / Q31_FULL_SCALE for v in left_raw],
        right=[v / Q31_FULL_SCALE for v in right_raw],
        left_raw=left_raw,
        right_raw=right_raw,
    )


def read_ring_a_pcm(dm_read: DmRead, ring_flag_value: int | None) -> RingAFrame:
    """The DAC-facing ring-A half the last completed frame wrote, read
    through DM_READ (an `address -> unsigned 32-bit int | None` callable --
    `tools/sharc_dev/sharc_harness/`'s `read_ring_a()` passes a closure over
    `sharc_trace._dm_read`/`Runner.state`, so this module never needs to
    import `sharc_core` itself).

    RING_FLAG_VALUE is the CURRENT (post-toggle) value of
    `DM(RING_FLAG_ADDR)` -- see `ring_half_just_written()`'s own
    docstring for why that needs XORing with 1 first."""
    half_offset = ring_half_just_written(ring_flag_value)
    base = RING_A_BASE + half_offset
    words = [dm_read(base + i * 4) for i in range(2 * SAMPLES_PER_HALF)]
    frame = decode_ring_a_words(words)
    return RingAFrame(
        half_written=half_offset,
        left=frame.left,
        right=frame.right,
        left_raw=frame.left_raw,
        right_raw=frame.right_raw,
    )


def read_master_mix_pcm(dm_read: DmRead) -> tuple[list[float], list[float]]:
    """The 32+32 planar float32 (L, R) samples `DM(MASTER_MIX_BASE)` holds
    right before `FUN_1c74a1` converts them (module docstring's second
    bullet) -- for comparing the DAC's own input against its output stage
    by stage, the way the lane report's own "stage where the tone breaks"
    section does. DM_READ: same convention as `read_ring_a_pcm()`."""

    def read_float(addr: int) -> float:
        raw = dm_read(addr)
        if raw is None:
            return 0.0
        return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]

    left = [
        read_float(MASTER_MIX_BASE + i * 4) for i in range(MASTER_MIX_CHANNEL_WORDS)
    ]
    right = [
        read_float(MASTER_MIX_BASE + (MASTER_MIX_R_OFFSET_WORDS + i) * 4)
        for i in range(MASTER_MIX_CHANNEL_WORDS)
    ]
    return left, right


def write_wav_stereo(
    path: str,
    left: Sequence[float],
    right: Sequence[float],
    sample_rate: int = 48000,
) -> None:
    """A plain 16-bit PCM stereo WAV of LEFT/RIGHT (both in [-1.0, 1.0],
    clipped) -- `tools/sharc_dev/sharc_harness/`'s own `write_wav()` is mono-only,
    and ring A is stereo by construction (module docstring); this is the
    ring-A counterpart it delegates to (see its own `write_ring_a_wav()`)."""
    if len(left) != len(right):
        raise ValueError(
            "write_wav_stereo: left/right length mismatch (%d vs %d)"
            % (len(left), len(right))
        )
    with wave.open(path, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for l_sample, r_sample in zip(left, right, strict=True):
            l_clipped = max(-1.0, min(1.0, l_sample))
            r_clipped = max(-1.0, min(1.0, r_sample))
            frames += struct.pack(
                "<hh",
                int(round(l_clipped * 32767)),
                int(round(r_clipped * 32767)),
            )
        wav.writeframes(bytes(frames))
