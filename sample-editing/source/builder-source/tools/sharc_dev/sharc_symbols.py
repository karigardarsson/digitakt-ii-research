#!/usr/bin/env python3
# fmt: off
"""Resolve SHARC+ program addresses from tools/sharc_dev/sharcdb/'s database, instead
of hardcoding them per firmware build -- the SHARC-side counterpart to
tools/coldfire/symbols.py, which does this for the ColdFire main OS. Read that module's
docstring first: the same idea (fixed anchors verified by content, everything
else found by what it looks like or how it is reached from something already
resolved, REQUIRED fails loudly at resolve() time, OPTIONAL degrades to None)
applies here with three SHARC-specific primitives instead of ColdFire's:

    * FuncMatch: a function identified by tools/sharc_dev/sharcdb/'s relocation-
      tolerant `func_hash` (same instruction sequence with every relocatable
      address field masked to zero) against a REFERENCE image, DT2 1.16 --
      the SHARC analogue of a masked-signature `Sig`, except the "signature"
      is a whole function's shape rather than a byte string, and the search
      is one query against an index instead of a scan.
    * LiteralAt: a literal value decoded by tools/sharc_dev/sharcdb/'s own typed
      instruction reader, at a fixed word (`sw`) offset from an
      already-resolved function's entry -- the SHARC analogue of
      tools.coldfire.symbols.Operand, except the literal is read from the `literals`
      table (already sign-extended and int-valued by the decoder) rather
      than re-parsed from raw bytes.
    * IVTSlot: a populated slot of the L1 hardware interrupt vector table,
      read from `roots` (kind='interrupt_vector') -- this needs no
      reference image at all: the IVT layout is RTOS/hardware structure,
      not application code, and it comes straight out of the DB the same
      way for every image.

Why the offset in LiteralAt is safe to carry across images: `func_hash`'s
reloc_hash is a SHA-256 over the function's instructions with every
'addr'/'reladdr'/'data' field zeroed (tools/sharc_dev/sharcdb/'s mask_relocatable),
concatenated in instruction order. Two functions can only hash equal if they
have the same instruction count AND the same per-instruction byte length in
the same order -- so once a reloc_hash match pins down a target function's
entry_sw, every instruction inside it sits at the same word offset from
entry as it does in the reference. That is what lets LiteralAt jump straight
to a computed `sw` in a brand new image and expect a literal to be sitting
there. Spot-checked directly against dn2-1.11: at every LiteralAt offset
used below, the DN2 image has a literal of the *same form* (17a/19a/14a) as
DT2 at that exact computed `sw`, carrying DN2's own (different) address.

REQUIRED_COMMON symbols exist on both known devices: the audio task/command
dispatch machinery and the ring buffers it drives, plus the shared SIMD
helper and the SECI interrupt entry. REQUIRED_DT2 adds the DT2 (Digitakt)
sample-playback voice engine -- render_frame's per-track loop, the per-slot
dispatcher, the decimator, and the data structures reached from them. DN2
(Digitone, an FM engine) genuinely does not have most of that: `resolve()`
still probes every DT2-only rule on a dn2-* image (so Profile.report() shows
what a device lacks, not just what it needed), but only fails the run when a
symbol REQUIRED for that specific device is missing. An unresolved or
ambiguous REQUIRED symbol raises SymbolResolutionError naming exactly which
symbol(s) and why.

    uv run python -m tools.sharc_symbols dt2-1.16      # print the resolution table
    uv run python -m tools.sharc_symbols dn2-1.11 --device dn2

Nothing here reads the firmware blob: every rule is a query against the
already-built out/sharcdb/<image>.sqlite (tools/sharc_dev/sharc.py's `sharc.load`), so
this module works wherever that database does.
"""
import os
import sys
from typing import Protocol

_here = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc  # noqa: E402

REFERENCE_IMAGE = 'dt2-1.16'

REQUIRED_COMMON = frozenset({'dt2', 'dn2'})
REQUIRED_DT2 = frozenset({'dt2'})
OPTIONAL: frozenset[str] = frozenset()


class _Rule(Protocol):
    """What every rule class below (FuncMatch, LiteralAt, ...) implements --
    named here only so SYMBOLS can be typed as a plain list of these,
    instead of the union mypy would otherwise infer from the list literal."""

    def resolve(
        self, img: 'sharc.Image', ref: 'sharc.Image', got: dict[str, int | None]
    ) -> tuple[int | None, str]: ...


class SymbolResolutionError(RuntimeError):
    """A symbol REQUIRED for this image's device could not be resolved, or
    resolved ambiguously. str(e) names exactly which one(s) and why."""


# --------------------------------------------------------------------------
# Rules. Each is declarative: given the target Image, the reference Image
# (DT2 1.16), and the dict of symbols already resolved for THIS image, it
# returns (value, detail) -- value is None if the rule failed, detail is a
# one-line human explanation either way (Profile.report() prints it).
# --------------------------------------------------------------------------

class FuncMatch:
    """The function at `ref_addr` in the reference image (DT2 1.16),
    located in the target image by tools/sharc_dev/sharc.py's Image.match() --
    tools/sharc_dev/sharcdb/'s relocation-tolerant func_hash. Must be unique.

    When the target IS the reference image, this just confirms `ref_addr`
    names a real function there (the reference's own known-address table is
    the ground truth everything else is checked against)."""

    def __init__(self, ref_addr):
        self.ref_addr = ref_addr

    def resolve(self, img, ref, got):
        if img.name == ref.name:
            row = img.sql('SELECT 1 FROM functions WHERE image=? AND entry_sw=?',
                           img.name, self.ref_addr)
            if not row:
                return None, ('reference function 0x%x is missing from %s itself'
                               % (self.ref_addr, ref.name))
            return self.ref_addr, 'reference function (%s itself)' % ref.name

        matches = ref.match(img, self.ref_addr)
        if not matches:
            return None, ("no func_hash match for %s's 0x%x in %s"
                           % (ref.name, self.ref_addr, img.name))
        addrs = sorted({m['entry_sw'] for m in matches})
        if len(addrs) != 1:
            return None, ('%d distinct func_hash matches for %s 0x%x in %s: %s'
                           % (len(addrs), ref.name, self.ref_addr, img.name, ', '.join(addrs)))
        exact = any(m['exact_match'] for m in matches if m['entry_sw'] == addrs[0])
        value = int(addrs[0], 16)
        return value, ('func_hash match for %s 0x%x -> %s (%s)'
                        % (ref.name, self.ref_addr, addrs[0], 'exact' if exact else 'reloc-only'))


class LiteralAt:
    """The decoded value of the literal at `<resolved symbol> + delta`
    (word offset, i.e. `sw`) in `literals` -- see the module docstring for
    why this offset, once computed from the reference image, carries over
    to any image the base symbol resolves in. `form`, when given, must
    match the literal's own encoding form there (17a/19a/14a/...) -- a
    defensive check that catches a delta that has drifted off its intended
    instruction rather than silently returning some other literal."""

    def __init__(self, symbol, delta, form=None):
        self.symbol, self.delta, self.form = symbol, delta, form

    def resolve(self, img, ref, got):
        base = got.get(self.symbol)
        if base is None:
            return None, "depends on unresolved '%s'" % self.symbol
        sw = base + self.delta
        rows = img.sql('SELECT value, form FROM literals WHERE image=? AND sw=?', img.name, sw)
        if len(rows) != 1:
            return None, ('%d literal(s) at %s+0x%x (sw 0x%x), need exactly 1'
                           % (len(rows), self.symbol, self.delta, sw))
        value, form = rows[0]
        if self.form is not None and form != self.form:
            return None, ('literal at %s+0x%x (sw 0x%x) has form %r, expected %r'
                           % (self.symbol, self.delta, sw, form, self.form))
        # literals.value is sign-extended per tools/sharc_dev/sharcdb/'s
        # extract_literal (correct for a compute-register operand); these
        # symbols are addresses, so fold a negative (top-bit-set) reading
        # back to its unsigned 32-bit form.
        value &= 0xFFFFFFFF
        return value, ('%s+0x%x (sw 0x%x) = 0x%x, form %s' % (self.symbol, self.delta, sw, value, form))


class Offset:
    """A fixed word distance from an already-resolved symbol -- the SHARC
    analogue of tools.coldfire.symbols.Offset, for a code address (a command-table
    jump target, say) that has no literal or signature of its own, only a
    known position relative to something that does. Verified against
    `insn` so a delta that no longer lands on an aligned instruction start
    fails loudly instead of returning a mid-instruction address."""

    def __init__(self, symbol, delta, require_insn=True):
        self.symbol, self.delta, self.require_insn = symbol, delta, require_insn

    def resolve(self, img, ref, got):
        base = got.get(self.symbol)
        if base is None:
            return None, "depends on unresolved '%s'" % self.symbol
        addr = base + self.delta
        if self.require_insn:
            rows = img.sql('SELECT 1 FROM insn WHERE image=? AND sw=? AND aligned=1', img.name, addr)
            if not rows:
                return None, ('%s+0x%x (0x%x) is not an aligned instruction start'
                               % (self.symbol, self.delta, addr))
        return addr, '%s+0x%x = 0x%x' % (self.symbol, self.delta, addr)


class IVTSlot:
    """A populated slot of the L1 hardware IVT, read from `roots`
    (kind='interrupt_vector', tools/sharc_dev/sharcdb/'s DB_VERSION v7). Needs no
    reference image: the IVT layout is RTOS/hardware structure, present the
    same way in every image this database has ever seen. Must be unique."""

    def __init__(self, slot, name):
        self.slot, self.name = slot, name

    def resolve(self, img, ref, got):
        note = 'slot %d %s' % (self.slot, self.name)
        rows = img.sql(
            "SELECT DISTINCT sw FROM roots WHERE image=? AND kind='interrupt_vector' AND note=?",
            img.name, note)
        if len(rows) != 1:
            return None, '%d IVT root(s) for %r, need exactly 1' % (len(rows), note)
        return rows[0][0], 'IVT slot %d (%s)' % (self.slot, self.name)


class EnclosingFunction:
    """The `functions` row whose [entry_sw, end_sw) span contains an
    already-resolved symbol -- used to get from a raw IVT target (a jump
    *into* a handler, not necessarily its first instruction -- see
    docs/findings/06's slot 5..31 note) to the handler function itself."""

    def __init__(self, symbol):
        self.symbol = symbol

    def resolve(self, img, ref, got):
        addr = got.get(self.symbol)
        if addr is None:
            return None, "depends on unresolved '%s'" % self.symbol
        rows = img.sql(
            'SELECT entry_sw FROM functions WHERE image=? AND entry_sw<=? AND end_sw>?',
            img.name, addr, addr)
        if len(rows) != 1:
            return None, ('%d function(s) enclose %s (0x%x), need exactly 1'
                           % (len(rows), self.symbol, addr))
        return rows[0][0], 'function enclosing %s' % self.symbol


# --------------------------------------------------------------------------
# The symbol table. Order matters: a rule may only depend on a symbol that
# resolves earlier in this list. `required` is the set of devices ('dt2',
# 'dn2') for which this symbol is REQUIRED; REQUIRED_COMMON for both,
# REQUIRED_DT2 for DT2 only, OPTIONAL for neither (diagnostic/anchor-only).
#
# Known-good values, DT2 1.16 (docs/findings/06, cross-checked against
# out/sharcdb/dt2-1.16.sqlite while this table was built):
#   audio_task_fn=0x1c75d8 block_handler=0x1c74cd command_dispatch_fn=0x1c778a
#   simd_helper=0xb80105 seci_dispatch=0x1c0b7b seci_isr=0x1c0b1d
#   cmd_handler_{0,1,2,3}={0x1c7524,0x1c75d8,0x1c763c,0x1c7671}
#   command_table=0x25f7b0 ring_a=0x261cc8 ring_b=0x261ec8 ring_d=0x263138
#   ring_flag=0x25f780 command_word=0x264220 command_word_shift_src=0x261ca4
#   command_record_table=0x266220
#   render_frame=0x1c2b24 unpack_track=0x1c24e9 slot_dispatch=0x1c642a
#   master_mix=0x1c207b voice_render=0x1c4ecf voice_render_tail=0x1c4f81
#   init=0x1c15e3 voice_alloc_scan=0x1c149b voice_record_init_a=0x1c7442
#   voice_record_init_b=0x1c4e70 decimator=0xb80000 voice_records=0x2412cc
#   coeff_table=0x25d940
#   frame_workspace=0x2412c8 mix_table_base=0x252d78 source_words=0x24ef2c
#   float_table_a=0x8045a6c8 track_buffers=0x252df8 selector_table=0x2567c0
#   machine_type_cache=0x255970
# --------------------------------------------------------------------------

SYMBOLS: list[tuple[str, _Rule, frozenset[str]]] = [
    # ---- platform: audio task / command dispatch, shared by both devices.
    # docs/findings/06 "Task loop": the Audio Task at 0x1c7749 (inside this
    # function's body) runs block_handler when a notify-take returns 1;
    # block_handler dispatches through command_table's 4 entries.
    ('audio_task_fn', FuncMatch(0x1c75d8), REQUIRED_COMMON),
    ('block_handler', FuncMatch(0x1c74cd), REQUIRED_COMMON),
    # The routine that folds the pending command word into ring selection
    # (docs/findings/06 "0x1c7578..0x1c7586"): DM(0x261ca4) shifted by 11,
    # added to a ring base, giving DM(0x264220 + (shift<<12)).
    ('command_dispatch_fn', FuncMatch(0x1c778a), REQUIRED_COMMON),
    # The decimator's low-level SIMD array helper (0xb80105) -- matches
    # exact-byte in DN2 1.11 even though decimator itself (0xb80000, DT2
    # sample-engine only) does not: a shared platform math primitive.
    ('simd_helper', FuncMatch(0xb80105), REQUIRED_COMMON),

    # SECI (slot 15) IVT entry and its enclosing ISR function. Needs no
    # reference image -- see IVTSlot.
    ('seci_dispatch', IVTSlot(15, 'SECI'), REQUIRED_COMMON),
    ('seci_isr', EnclosingFunction('seci_dispatch'), REQUIRED_COMMON),

    # Command table 0x25f7b0 and its 4 entries. Entry 0 is a jump target
    # inside block_handler; entries 1-3 are jump targets inside
    # audio_task_fn (entry 1 being audio_task_fn's own start).
    ('command_table', LiteralAt('block_handler', 0x34, form='17a'), REQUIRED_COMMON),
    ('cmd_handler_0', Offset('block_handler', 0x57), REQUIRED_COMMON),
    ('cmd_handler_1', Offset('audio_task_fn', 0x00), REQUIRED_COMMON),
    ('cmd_handler_2', Offset('audio_task_fn', 0x64), REQUIRED_COMMON),
    ('cmd_handler_3', Offset('audio_task_fn', 0x99), REQUIRED_COMMON),

    # The three DMA rings and the flag selecting between ring A/B halves.
    ('ring_a', LiteralAt('block_handler', 0x5f, form='19a'), REQUIRED_COMMON),
    ('ring_flag', LiteralAt('block_handler', 0x49, form='14a'), REQUIRED_COMMON),
    ('ring_b', LiteralAt('audio_task_fn', 0x78, form='19a'), REQUIRED_COMMON),
    ('ring_d', LiteralAt('audio_task_fn', 0x6f, form='19a'), REQUIRED_COMMON),

    # The pending command word and the DM word command_dispatch_fn shifts
    # to fold into it (docs/findings/06 "0x1c7578..0x1c7586").
    ('command_word', LiteralAt('command_dispatch_fn', 0x9, form='17a'), REQUIRED_COMMON),
    ('command_word_shift_src', LiteralAt('command_dispatch_fn', 0x6, form='14a'), REQUIRED_COMMON),
    # command_dispatch_fn's OTHER by-reference output (docs/findings/06
    # "Frame call path": R1 = DM(I6-2), the record render_frame's own M9
    # points at) -- the same shift folded into command_word, added to a
    # DIFFERENT base (lane F2, 2026-09-26). See sharc_harness's
    # companding_record_address().
    ('command_record_table', LiteralAt('command_dispatch_fn', 0x1D, form='17a'), REQUIRED_COMMON),

    # ---- DT2 (Digitakt) sample-playback voice engine. DN2 is FM (finding
    # 11): its analogous functions were found by call-graph inspection, not
    # func_hash, so FuncMatch is expected to come back empty there -- that
    # is a result (see the module docstring), not a resolver bug.
    ('render_frame', FuncMatch(0x1c2b24), REQUIRED_DT2),
    ('unpack_track', FuncMatch(0x1c24e9), REQUIRED_DT2),
    ('slot_dispatch', FuncMatch(0x1c642a), REQUIRED_DT2),
    ('master_mix', FuncMatch(0x1c207b), REQUIRED_DT2),
    ('voice_render', FuncMatch(0x1c4ecf), REQUIRED_DT2),
    ('voice_render_tail', FuncMatch(0x1c4f81), REQUIRED_DT2),
    ('init', FuncMatch(0x1c15e3), REQUIRED_DT2),
    # Anchor-only: called once per frame from render_frame (0x1c3133);
    # exposed because voice_records is found through it.
    ('voice_alloc_scan', FuncMatch(0x1c149b), REQUIRED_DT2),
    ('voice_record_init_a', FuncMatch(0x1c7442), REQUIRED_DT2),
    ('voice_record_init_b', FuncMatch(0x1c4e70), REQUIRED_DT2),
    ('decimator', FuncMatch(0xb80000), REQUIRED_DT2),

    ('voice_records', LiteralAt('voice_alloc_scan', 0x10, form='19a'), REQUIRED_DT2),
    # The 128-phase, 6-tap polyphase coefficient table (docs/findings/06's
    # voice record contract, "Render and declick"): voice_render_tail loads
    # its base once, sw 0x1c50aa ("I5 = 0x25d940"), just before the DO 64
    # interpolation loop.
    ('coeff_table', LiteralAt('voice_render_tail', 0x129, form='17a'), REQUIRED_DT2),
    ('frame_workspace', LiteralAt('init', 0x5d, form='17a'), REQUIRED_DT2),
    ('mix_table_base', LiteralAt('init', 0x85, form='17a'), REQUIRED_DT2),
    ('source_words', LiteralAt('init', 0x8d, form='17a'), REQUIRED_DT2),
    ('float_table_a', LiteralAt('init', 0xa3, form='17a'), REQUIRED_DT2),
    ('track_buffers', LiteralAt('init', 0xb6, form='19a'), REQUIRED_DT2),
    ('selector_table', LiteralAt('unpack_track', 0x248, form='17a'), REQUIRED_DT2),
    ('machine_type_cache', LiteralAt('unpack_track', 0x1e5, form='17a'), REQUIRED_DT2),
]

_NAMES = frozenset(name for name, _, _ in SYMBOLS)


def device_of(image_name):
    """'dt2' or 'dn2' from an image name like 'dt2-1.16' / 'dn2-1.10E', or
    None if it matches neither -- resolve() then falls back to
    REQUIRED_COMMON only, resolving every DT2-only rule as diagnostic."""
    prefix = image_name.split('-', 1)[0].lower()
    return prefix if prefix in ('dt2', 'dn2') else None


class Profile:
    """Symbols by attribute or item access: `profile.audio_task_fn` or
    `profile['audio_task_fn']`. A symbol not REQUIRED for this image's
    device that failed to resolve reads as None either way -- callers must
    check, not assume."""

    def __init__(self, image_name, device, values, detail):
        self.image_name = image_name
        self.device = device
        self._values = values
        self._detail = detail
        self.unresolved = [n for n, v in values.items() if v is None]

    def __getattr__(self, name):
        if name in _NAMES:
            return self._values.get(name)
        raise AttributeError(name)

    def __getitem__(self, name):
        return self._values[name]

    def get(self, name, default=None):
        return self._values.get(name, default)

    def report(self):
        lines = ['profile image=%s device=%s' % (self.image_name, self.device or '?')]
        for name, _, required in SYMBOLS:
            val = self._values.get(name)
            tag = 'REQUIRED' if self.device in required else 'optional'
            detail = self._detail.get(name, '')
            if val is None:
                lines.append('  %-24s %-8s UNRESOLVED -- %s' % (name, tag, detail))
            else:
                lines.append('  %-24s %-8s 0x%08x  -- %s' % (name, tag, val, detail))
        if self.unresolved:
            lines.append('unresolved: %s' % ', '.join(self.unresolved))
        return '\n'.join(lines)


def resolve(img, device=None, reference=None):
    """-> Profile for `img` (a tools/sharc_dev/sharc.py Image, already loaded).
    `device` overrides the 'dt2'/'dn2' guess from img.name (device_of()).
    `reference` overrides the REFERENCE_IMAGE Image (mainly for tests) --
    pass the same Image as `img` when resolving the reference itself, so it
    is not opened twice.

    Raises SymbolResolutionError if any symbol REQUIRED for `device` is
    unresolved or ambiguous, naming exactly which one(s) and why.
    """
    device = device if device is not None else device_of(img.name)
    if reference is not None:
        ref = reference
    elif img.name == REFERENCE_IMAGE:
        ref = img
    else:
        ref = sharc.load(REFERENCE_IMAGE)

    got: dict[str, int | None] = {}
    detail: dict[str, str] = {}
    for name, rule, _required in SYMBOLS:
        val, why = rule.resolve(img, ref, got)
        got[name] = val
        detail[name] = why

    missing = [name for name, _, required in SYMBOLS if device in required and got[name] is None]
    if missing:
        lines = ['%d REQUIRED symbol(s) failed to resolve for %s (device=%s):'
                 % (len(missing), img.name, device or '?')]
        for name in missing:
            lines.append('  %-24s %s' % (name, detail[name]))
        raise SymbolResolutionError('\n'.join(lines))

    return Profile(img.name, device, got, detail)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print('usage: sharc_symbols.py IMAGE [--device dt2|dn2]', file=sys.stderr)
        return 1
    image_name = argv[0]
    device = None
    if '--device' in argv:
        device = argv[argv.index('--device') + 1]

    img = sharc.load(image_name)
    try:
        profile = resolve(img, device=device)
    except SymbolResolutionError as e:
        print(str(e))
        return 1
    print(profile.report())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
