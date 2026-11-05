"""A small SHARC+ (VISA) assembler for hook routines, built on the repository's
own encoding table (tools/sharc_dev/sharcspec/decode_table.json) and checked by the
repository's decoder.

It is a builder, not a text assembler: a routine is Python calls that append
instructions, labels are resolved when `words()` is called, and every emitted
instruction is decoded again by `sharc_disasm.ISA` (the same decoder the
firmware analysis uses) and must come back as the form and extent that was
meant. `listing()` prints the re-decoded mnemonics.

    a = Asm(0xBD0000)
    a.ureg_imm("I4", 0x25F180)
    a.do_lcntr(64, "end")
    ...
    a.label("end")
    a.words()   # list of (pc, [parcels])

Only the forms the hook routines need exist; add a form together with a test
in tests/sharc/generation/test_sharc_asm.py. Encodings come from the public manuals via
tools/sharc_dev/sharcspec/; no vendor toolchain is used. Register codes are the
universal-register codes of tools/sharc_dev/sharc_trace.UREG_CODES.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_here = str(Path(__file__).resolve().parent)

from tools.sharc_dev import (  # noqa: E402
    sharc_disasm,  # noqa: E402
    sharcfn,  # noqa: E402
)
from tools.sharc_dev.sharc_trace import UREG_CODES  # noqa: E402

COND_TRUE = 0x1F

# compute unit field cu (PRM Figure 18-1): 0 ALU, 1 multiplier, 2 shifter
_CU_ALU = 0
_CU_MUL = 1
_MUL_FLOAT = 0x30
# ALU opcodes (PRM Table 18-5)
ALU_OPS = {
    "fadd": 0x81,  # Fn = Fx + Fy
    "fsub": 0x82,  # Fn = Fx - Fy
    "float": 0xCA,  # Fn = float Rx
    "float_by": 0xDA,  # Fn = float Rx by Ry
    "fix": 0xC9,  # Rn = fix Fx
    "fix_by": 0xD9,  # Rn = fix Fx by Ry
}


def ureg(name: str) -> int:
    return UREG_CODES[name.upper()]


def _parcels(frame: int, width: int) -> list[int]:
    """Parcels of a width-bit instruction held MSB-aligned in a 48-bit frame."""
    return [(frame >> (32 - 16 * k)) & 0xFFFF for k in range(width // 16)]


@dataclass
class _Item:
    pc: int
    width: int  # bits
    build: Callable[[dict[str, int]], int]  # labels -> 48-bit MSB-aligned frame
    want_form: str
    text: str


@dataclass
class Asm:
    base_pc: int
    items: list[_Item] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)

    @property
    def pc(self) -> int:
        return self.base_pc + sum(i.width // 16 for i in self.items)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"label {name!r} defined twice")
        self.labels[name] = self.pc

    def _add(self, width: int, form: str, text: str, build) -> None:
        self.items.append(_Item(self.pc, width, build, form, text))

    def _addr(self, target: int | str, labels: dict[str, int]) -> int:
        return labels[target] if isinstance(target, str) else target

    # -- instructions ------------------------------------------------------

    def nop(self) -> None:
        self._add(48, "21a", "NOP", lambda _l: 0)

    def ureg_imm(self, reg: str, value: int) -> None:
        """Type 17a: UREG = 32-bit immediate."""
        code = ureg(reg)
        self._add(
            48,
            "17a",
            f"{reg.upper()} = {value:#x}",
            lambda _l: (0x0F << 40) | (code << 32) | (value & 0xFFFFFFFF),
        )

    def ureg_imm16(self, reg: str, value: int) -> None:
        """Type 17b: UREG = 16-bit immediate (sign-extended by the core)."""
        code = ureg(reg)
        self._add(
            32,
            "17b",
            f"{reg.upper()} = {value:#x}",
            lambda _l: (0x0F8 << 36) | (code << 32) | ((value & 0xFFFF) << 16),
        )

    def load_abs(self, reg: str, addr: int) -> None:
        """Type 14a: UREG = DM(absolute 32-bit address)."""
        code = ureg(reg)
        self._add(
            48,
            "14a",
            f"{reg.upper()} = DM({addr:#x})",
            lambda _l: (0x10 << 40) | (code << 32) | (addr & 0xFFFFFFFF),
        )

    def store_abs(self, addr: int, reg: str) -> None:
        """Type 14a: DM(absolute 32-bit address) = UREG."""
        code = ureg(reg)
        self._add(
            48,
            "14a",
            f"DM({addr:#x}) = {reg.upper()}",
            lambda _l: (0x10 << 40) | (1 << 40) | (code << 32) | (addr & 0xFFFFFFFF),
        )

    def _mem3b(self, reg: str, i: int, m: int, write: bool, post: bool) -> None:
        code = ureg(reg)
        frame = (
            (0b010 << 45)
            | (int(post) << 44)
            | (i << 41)
            | (m << 38)
            | (COND_TRUE << 33)
            | (int(write) << 31)
            | (code << 23)
            | (0x0F << 18)
            | (1 << 17)  # w
            | (1 << 16)  # x: (l, x, w) = (0, 1, 1) is the normal-word access
        )
        arrow = (
            f"DM(I{i}, M{m}) = {reg.upper()}"
            if write
            else f"{reg.upper()} = DM(I{i}, M{m})"
        )
        self._add(32, "3b", arrow + (" u=1" if post else " u=0"), lambda _l: frame)

    def load(self, reg: str, i: int, m: int, post: bool = True) -> None:
        """Type 3b: UREG = DM(Ii, Mm); post-modifies Ii by Mm when `post`."""
        self._mem3b(reg, i, m, False, post)

    def store(self, reg: str, i: int, m: int, post: bool = True) -> None:
        """Type 3b: DM(Ii, Mm) = UREG."""
        self._mem3b(reg, i, m, True, post)

    def fmul(self, rn: int, rx: int, ry: int) -> None:
        """Type 2a: Fn = Fx * Fy, unconditional."""
        compute = (_CU_MUL << 20) | (_MUL_FLOAT << 12) | (rn << 8) | (rx << 4) | ry
        self._add(
            48,
            "2a",
            f"F{rn} = F{rx} * F{ry}",
            lambda _l: (0x01 << 40) | (COND_TRUE << 33) | compute,
        )

    def load_sw_abs(self, reg: str, addr: int, signed: bool = False) -> None:
        """Type 14d: Rn = DM(absolute address) as a short word (R0-R15 only)."""
        n = int(reg[1:])
        if not (reg.upper().startswith("R") and 0 <= n <= 15):
            raise ValueError(f"14d takes R0-R15, not {reg}")
        frame = (
            (0x1A << 40)
            | (1 << 38)
            | (int(signed) << 36)
            | (n << 32)
            | (addr & 0xFFFFFFFF)
        )
        self._add(
            48,
            "14d",
            f"R{n} = DM({addr:#x}) (sw{'se' if signed else ''})",
            lambda _l: frame,
        )

    def alu(self, op: str, rn: int, rx: int, ry: int = 0) -> None:
        """Type 2a: an unconditional ALU operation by name (see ALU_OPS)."""
        compute = (_CU_ALU << 20) | (ALU_OPS[op] << 12) | (rn << 8) | (rx << 4) | ry
        self._add(
            48,
            "2a",
            f"{op} {rn}, {rx}, {ry}",
            lambda _l: (0x01 << 40) | (COND_TRUE << 33) | compute,
        )

    def do_lcntr(self, count: int, last: str | int) -> None:
        """Type 12a: LCNTR = count, DO ... UNTIL LCE. `last` is the label of
        the loop's final instruction."""
        pc = self.pc

        def build(labels: dict[str, int]) -> int:
            rel = self._addr(last, labels) - pc
            return (0x0C << 40) | (count << 24) | (1 << 23) | (rel & 0x7FFFFF)

        self._add(48, "12a_imm", f"DO {last} UNTIL LCE (count {count})", build)

    def jump_abs(self, target: int | str) -> None:
        """Type 8a absolute: JUMP target, unconditional, not delayed."""
        self._add(
            48,
            "8a_abs",
            f"JUMP {target if isinstance(target, str) else hex(target)}",
            lambda labels: (
                (0x06 << 40) | (COND_TRUE << 33) | self._addr(target, labels)
            ),
        )

    def call_abs(self, target: int | str) -> None:
        """Type 25a direct CALL (the caller supplies the delay slots)."""
        self._add(
            48,
            "25a_direct",
            f"CALL {target if isinstance(target, str) else hex(target)}",
            lambda labels: (0x1804 << 32) | self._addr(target, labels),
        )

    # -- output ------------------------------------------------------------

    def words(self) -> list[tuple[int, list[int]]]:
        """Resolve labels, re-decode every instruction and return (pc, parcels)."""
        out = []
        for item in self.items:
            parcels = _parcels(item.build(self.labels), item.width)
            check_decode(parcels, item.want_form)
            out.append((item.pc, parcels))
        return out

    def listing(self) -> list[str]:
        lines = []
        for pc, parcels in self.words():
            lines.append(
                f"{pc:#08x}  {''.join(f'{p:04x}' for p in parcels):<12}  {decode_text(pc, parcels)}"
            )
        return lines

    def patch_lines(self, comment: bool = True) -> list[str]:
        lines = []
        for item, (pc, parcels) in zip(self.items, self.words(), strict=True):
            tail = f"  ; {item.text}" if comment else ""
            lines.append(f"PM {pc:x} {''.join(f'{p:04x}' for p in parcels)}{tail}")
        return lines


class _NullMemory:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def decode(parcels: list[int]):
    result = sharc_disasm.ISA.decode_words(parcels + [0, 0])
    if result.instruction is None:
        raise ValueError(f"does not decode: {[hex(p) for p in parcels]}")
    return result.instruction


def check_decode(parcels: list[int], want_form: str) -> None:
    insn = decode(parcels)
    if insn.form.id != want_form or insn.form.extent_words != len(parcels):
        raise ValueError(
            f"{[hex(p) for p in parcels]} decodes as {insn.form.id}/{insn.form.extent_words}w,"
            f" wanted {want_form}/{len(parcels)}w"
        )


def decode_text(pc: int, parcels: list[int]) -> str:
    insn = decode(parcels)
    form = insn.form
    rec = sharc_disasm.Instruction(
        0,
        form.extent_bytes,
        form.id,
        {x.field.label: x.value for x in insn.fields},
        raw=insn.raw,
    )
    text, _notes, _gap = sharcfn.render_instruction(
        pc, rec, _NullMemory(), set(), Counter(), []
    )
    return text
