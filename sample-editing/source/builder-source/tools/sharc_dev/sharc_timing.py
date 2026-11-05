"""SHARC+ stall and flush rules as data and functions, from the public SHARC+ Core Programming Reference.

Source: out/refs/sharc-plus-prm (Rev 1.4/1.5; printed pages in the comments), built by
`uv run --with pymupdf python -m tools.devtools.refstext`. docs/sharc/SOURCES.md lists what may be cited.
Only cycle numbers and conditions are taken from the manual. Every rule has three values, low / nominal / high,
because the manual does not say everything (what the sequencer assumes about the branch target buffer set index,
whether a "move" counts as a "load", the L2 and DDR latencies); the choice of each value is stated at the rule.

Stalls are counted on adjacent executed instruction pairs (N-1 -> N). The manual's tables list the adjacent case;
stalls for a longer distance are smaller and are not counted (a lower bound on stalls).

Unit tests: tests/sharc/analysis/test_sharc_timing.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A cost triple: (low, nominal, high) cycles.
Cost = tuple[float, float, float]
ZERO: Cost = (0.0, 0.0, 0.0)

CORE_HZ = 1_000_000_000  # assumed by the emulator; the part allows 400/600/800 MHz or 1 GHz CCLK (HWR CGU_CTL.MSEL)
PERIOD_INSNS = 666_667  # DSP block of 32 samples at 48 kHz, 1 GHz

# Table 4-38 "Stalls Related to DAG Operations" (printed p. 4-66), rows 1, 2 and 4.
DAG_LOAD_TO_USE = 4  # Ix = DM(..); DM(Ix..): unconditional DAG register load, then use
DAG_COND_LOAD_TO_USE = (
    5  # condition set just before a conditional DAG register load, then use
)
DAG_IMMEDIATE_TO_USE = 0  # Ix = IMM; DM(Ix..)
# Table 4-40 rows 3, 4 (p. 4-67): the same loads used as the target of an indirect jump: 4 / 5.

# Table 4-36 "Stalls Related to Compute Operations" (p. 4-65), row 1: float compute / multiply to next compute: 1.
FLOAT_FORWARD = 1
# Table 4-43 "Stalls Related to Data Move Operations" (p. 4-68): float compute or multiplier operation followed by
# a move of the result to a register outside the PE: 1.
FLOAT_TO_MOVE = 1
# Table 4-35 (p. 4-64): conflict-cache miss on a PM data access: 1; two accesses to the same L1 block in one cycle: 1
# (block arbitration, printed p. 7-14: the instruction "may take two or three core clock cycles").
PM_CONFLICT_MISS = 1
SAME_BLOCK = (
    1,
    1,
    2,
)  # Table 4-35: 1; the arbitration text allows two or three cycles for the instruction
CONFLICT_CACHE_ENTRIES = (
    32  # 16 sets x 2 ways (p. 4-49 "Instruction Data Bus Conflicts")
)

# Table 4-4 "Stalls in the Presence of Branch Target Buffer" (p. 4-13) and Tables 4-39, 4-40.
# Pairs are (non-delayed, delayed).
UNCOND_BTB_HIT = (2, 0)
UNCOND_NO_BTB = (6, 4)  # Table 4-39 row 1: unconditional branch, pipeline flush
COND_TAKEN_BTB_HIT = (2, 0)
COND_MISPREDICT_TAKEN_PREDICTED = (
    11,
    11,
)  # predicted taken, was not taken: Table 4-4, 11/11
COND_MISPREDICT_NOT_PREDICTED = (
    11,
    9,
)  # predicted not taken, was taken: Table 4-4, 11/9
COND_TAKEN_NO_BTB = (11, 9)  # Table 4-39 row 2 and Table 4-4: without a BTB hit
RTI_FLUSH = (
    7  # p. 4-67 footnote ** ("RTI (DB) and RTI cause 7 cycles of pipeline flush")
)
# Table 4-41 (p. 4-67): termination of E2-active counter loops and of arithmetic loops flushes the pipeline: 11.
# F1-active loops cost nothing (p. 4-35, "Loop Categorization", p. 4-37).
LOOP_E2_EXIT = 11
LOOP_MIN_F1_INSNS = (
    11  # a loop that unrolls to fewer than eleven instructions always runs E2-active
)

BTB_ENTRIES = 256  # 2 ways x 128 sets (p. 4-12 "BTB Function")
BTB_WAYS = 2
BTB_SETS = 128

# Caches (SHL1C_CFG, printed pp. 32-2 to 32-4, and "L1 Cache Controller", chapter 8).
ICACHE_BYTES = 16 * 1024  # SHL1C_CFG.ICASIZ = 0 selects 128 Kbit
ICACHE_WAYS = 2
ICACHE_LINE = 64  # Table 8-1 (p. 8-1): "Line size 512 bits"; the p. 8-9 five zero bits are short-word address bits (32 x 2 B)
DCACHE_BYTES = (
    16 * 1024
)  # per cache: two data caches per core, DM and PM (p. 8-7, DMCASIZ and PMCASIZ), same size assumed
DCACHE_WAYS = 2
DCACHE_LINE = 64  # Table 8-3: a six-bit offset

# The manual gives no L2 or DDR latency. These are assumptions, in core cycles per line fill:
# (low, nominal, high). Reported separately from the manual-derived stalls.
L2_FILL: Cost = (12.0, 24.0, 48.0)
EXT_FILL: Cost = (30.0, 60.0, 120.0)
MMR_ACCESS: Cost = (
    2.0,
    10.0,
    20.0,
)  # a system MMR access is a through access (p. 8-10), "0-4" for a CMMR (Table 4-43)


def add(a: Cost, b: Cost) -> Cost:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Cost, k: float) -> Cost:
    return (a[0] * k, a[1] * k, a[2] * k)


@dataclass(frozen=True)
class Ins:
    """What the stall rules need to know about one decoded instruction."""

    pc: int  # software (short-word) PC
    nbytes: int  # 2, 4 or 6
    text: str
    always: bool = True  # not conditionally executed
    defs: tuple[
        tuple[str, str], ...
    ] = ()  # (register, kind): kind in compute, mem_load, move, literal, dag_modify, swap
    uses: frozenset[str] = frozenset()
    mem: tuple[
        tuple[str, str], ...
    ] = ()  # (space DM/PM, base register or "" for direct)
    branch: str = ""  # jump, call, return, cond_jump, indirect, loop ("" for none)
    delayed: bool = False
    targets: tuple[int, ...] = ()
    loop_end: int = 0  # DO ... UNTIL: the end-of-loop address
    loop_counter: bool = False  # DO UNTIL LCE (a counter loop)
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def next_pc(self) -> int:
        return self.pc + self.nbytes // 2

    @property
    def conditional(self) -> bool:
        return not self.always

    @property
    def is_multiplier(self) -> bool:
        """A multiplier operation (fixed or floating point): `*` in the compute field."""
        return "*" in self.text

    @property
    def is_rti(self) -> bool:
        """RTI or RTI (DB): text, or an undecoded Type 11a/11c with the x bit (RTS is 0, RTI is 1)."""
        return bool(RTI_TEXT.search(self.text))

    @property
    def is_idle(self) -> bool:
        return bool(IDLE_TEXT.search(self.text))

    @property
    def is_float(self) -> bool:
        """A floating-point compute: a destination written as Fn."""
        return bool(FLOAT_DEST.search(self.text))

    def writes(self, kinds: tuple[str, ...] = ()) -> set[str]:
        return {r for r, k in self.defs if not kinds or k in kinds}


FLOAT_DEST = re.compile(r"\bF\d+\s*=")
RTI_TEXT = re.compile(r"\bRTI\b|^\[11[ac]\].*\bx=0x1\b")
IDLE_TEXT = re.compile(r"\bIDLE\b")
DAG_REG = re.compile(r"^[IMBL]\d+$")
SYS_REG_DEST = re.compile(
    r"^(?:[IMBL]\d+|USTAT\d|MODE\d|FLAGS|PX\d?|LCNTR|CCNTR|ASTAT[XY]|STKY[XY]|IMASK\w*|IRPTL)$"
)


def dag_use(prev: Ins, cur: Ins) -> Cost:
    """Table 4-38 rows 1-4 and Table 4-40 rows 3-4: a DAG register written by the previous instruction and used
    by this one as an address register or an indirect jump target.

    Low: a load from memory only (the table's example `Ix = DM(..)`). Nominal and high: also a transfer from a
    data register (`Ix = Rn`), which the table does not list. An immediate load costs nothing (row 4)."""
    loaded = {r for r, k in prev.defs if DAG_REG.match(r) and k == "mem_load"}
    moved = {r for r, k in prev.defs if DAG_REG.match(r) and k == "move"}
    if not (loaded | moved):
        return ZERO
    addr_regs = {r for r in cur.uses if DAG_REG.match(r)}
    if (
        not cur.mem
        and cur.branch != "indirect"
        and not any(k == "dag_modify" for _, k in cur.defs)
    ):
        return ZERO
    n = DAG_COND_LOAD_TO_USE if prev.conditional else DAG_LOAD_TO_USE
    lo = float(n) if loaded & addr_regs else 0.0
    nom = float(n) if (loaded | moved) & addr_regs else 0.0
    return (lo, nom, nom)


def float_forward(prev: Ins, cur: Ins) -> Cost:
    """Table 4-36 row 1: a float compute or multiply followed by a compute that reads its result: 1 stall.
    Row 3: a float multiply followed by a fixed-point ALU operation: 0 (not told apart here, so low is 0)."""
    if not prev.is_float:
        return ZERO
    dest = prev.writes(("compute",))
    if not dest & cur.uses:
        return ZERO
    if not any(k == "compute" for _, k in cur.defs):
        return ZERO
    return (0.0, float(FLOAT_FORWARD), float(FLOAT_FORWARD))


def float_to_move(prev: Ins, cur: Ins) -> Cost:
    """Table 4-43 row 1: a float compute or any multiplier operation (fixed point too) followed by a move of the
    result to a register outside the PE (a DAG, system or program-control register): 1 stall."""
    if not (prev.is_float or prev.is_multiplier):
        return ZERO
    dest = prev.writes(("compute",))
    for r, k in cur.defs:
        if k == "move" and SYS_REG_DEST.match(r) and dest & cur.uses:
            return (float(FLOAT_TO_MOVE),) * 3
    return ZERO


def branch_cost_sim(ins: Ins, r: list[int]) -> Cost:
    """Branch cost from the outcomes counted against a simulated BTB.

    r = [executions, taken, hit & predicted taken & taken, hit & not taken & predicted not taken,
         hit & predicted taken & not taken, hit & predicted not taken & taken, miss & taken, miss & not taken].
    Nominal sums Table 4-4 over those outcomes (a BTB miss is not predicted: a taken branch loses the
    without-BTB cycles). Low: every taken branch is predicted correctly (hit costs only). High: no BTB."""
    d = 1 if ins.delayed else 0
    n, taken = r[0], r[1]
    if not ins.conditional and ins.branch != "cond_jump":
        hits = r[2]
        miss = r[6] + r[7]
        return (
            float(n * UNCOND_BTB_HIT[d]),
            float(hits * UNCOND_BTB_HIT[d] + miss * UNCOND_NO_BTB[d]),
            float(n * UNCOND_NO_BTB[d]),
        )
    mis_t_to_n = COND_MISPREDICT_TAKEN_PREDICTED[d]
    mis_n_to_t = COND_MISPREDICT_NOT_PREDICTED[d]
    nom = (
        r[2] * COND_TAKEN_BTB_HIT[d]
        + r[4] * mis_t_to_n
        + r[5] * mis_n_to_t
        + r[6] * COND_TAKEN_NO_BTB[d]
    )
    return (
        float(taken * COND_TAKEN_BTB_HIT[d]),
        float(nom),
        float(taken * COND_TAKEN_NO_BTB[d]),
    )


def loop_exit_cost(
    ins: Ins, body_insns: int, has_cof_in_tail: bool, iterations: float, executions: int
) -> Cost:
    """Table 4-41 and p. 4-35, 4-37: counter loops run F1-active (no cost) unless the last eleven instructions of
    the body hold a change of flow or an IDLE, or the loop unrolls to fewer than eleven instructions; arithmetic loops and
    E2-active loops flush on termination (11). Low: every counter loop F1-active; nominal: the rule above; high:
    every loop E2-active."""
    e = executions
    hi = float(LOOP_E2_EXIT * e)
    if not ins.loop_counter:
        return (hi, hi, hi)
    short = body_insns * max(iterations, 1.0) < LOOP_MIN_F1_INSNS
    nom = hi if (has_cof_in_tail or short) else 0.0
    return (0.0, nom, hi)


def conflict_cache_misses(pm_access_pcs: int, accesses: int) -> Cost:
    """Table 4-35 row 1: a PM data access that conflicts with the instruction fetch stalls once unless the fetched
    instruction is in the 32-entry conflict cache. Low: all hit. High: all miss. Nominal: a cache of 32 entries
    holds the conflicting instructions when there are no more than 32 distinct PM-access instructions; otherwise
    the fraction (distinct - 32) / distinct of the accesses miss."""
    miss_frac = (
        max(0.0, 1.0 - CONFLICT_CACHE_ENTRIES / pm_access_pcs) if pm_access_pcs else 0.0
    )
    return (0.0, accesses * miss_frac * PM_CONFLICT_MISS, accesses * PM_CONFLICT_MISS)


def region_of_pc(pc: int) -> str:
    """Where an instruction fetch comes from, by short-word PC: L1 block 3 holds the firmware's resident code
    (byte address 2 x PC in 0x380000..0x3a0000), L2 SRAM maps at the short-word alias 0xB80000 (datasheet Rev D)."""
    if 0x1C0000 <= pc < 0x1D0000:
        return "l1"
    if 0x120000 <= pc < 0x1D0000:
        return "l1"
    if 0xB80000 <= pc < 0xC0C000:
        return "l2"
    return "other"


BUCKETS = ("other", "l1b0", "l1b1", "l1b2", "l1b3", "l2", "ext", "mmr")
