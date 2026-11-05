"""List the immediate values in SHARC+ DSP code and name the peripheral registers.

    uv run python -m tools.sharc_dev.sharcimm REGION.bin [--base-sw 0x1c1338]
        [--range LO:HI ...] [--value V ...] [--min-depth N] [--json OUT]
    uv run python -m tools.sharc_dev.sharcimm --words SECTION7.bin
        [--range LO:HI ...] [--value V ...] [--json OUT]

REGION.bin is raw DSP code, 16-bit little-endian words, such as
out/sharc/dt2-1.16-main.bin; --base-sw is the short-word address of its
first word (0x1c1338 for the Digitakt II main programs).

--words scans data instead: every loaded block of a boot stream (parsed by
tools/sharc_dev/sharcldr.py) as 32-bit little-endian words at addresses divisible by
4, code blocks included. A driver can keep its peripheral base addresses in
such a table rather than in instructions. Each hit gives the loader byte
address and, inside the 0x28000000 alias, the data pointer the code uses
(the address less 0x28000000).

A linear walk over this code desyncs early, so the scan decodes one
instruction at every even offset with tools/sharc_dev/sharc_disasm.py and keeps the
hits whose value lies in a --range (default: the ADSP-2156x peripheral space
0x30000000-0x31ffffff) or equals a --value. A 32-bit value is the pair of
fields X[31:16] and X[15:0]; a --value also matches any single field of at
least 9 bits.

An instruction decoded at a misaligned offset can carry a plausible value,
so each hit carries two alignment measures:

- depth: the longest run of decoded instructions that ends at the hit, each
  one starting where the previous one ends. Runs from different start words
  merge after a few instructions, so a deep hit is on the real boundary
  unless the whole region is misaligned;
- sweep: the hit lies on the linear sweep that steps one word past a word it
  cannot decode (the sweep of tools/sharc_dev/sharccompare.py).

Peripheral names and register offsets are from the public ADSP-2156x SHARC+
Processor Hardware Reference, Rev 1.0 (Appendix A register lists; Table 27-2
for the DMA channel assignment).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys

from tools.sharc_dev import sharc_disasm  # noqa: E402
from tools.sharc_dev.sharc_disasm import disassemble  # noqa: E402

PERIPHERAL_SPACE = (0x30000000, 0x31FFFFFF)

CORE_MMR_REGS = {
    0x30024: "CMMR_SYSCTL",
    0x31400: "SHBTB_CFG",
    0x31401: "SHBTB_LOCK_START",
    0x31402: "SHBTB_LOCK_END",
    0x3E000: "SHL1C_CFG",
    0x3E002: "SHL1C_CFG2",
}

SPI_REGS = {
    0x04: "CTL",
    0x08: "RXCTL",
    0x0C: "TXCTL",
    0x10: "CLK",
    0x14: "DLY",
    0x18: "SLVSEL",
    0x1C: "RWC",
    0x20: "RWCR",
    0x24: "TWC",
    0x28: "TWCR",
    0x30: "IMSK",
    0x34: "IMSK_CLR",
    0x38: "IMSK_SET",
    0x40: "STAT",
    0x44: "ILAT",
    0x48: "ILAT_CLR",
    0x50: "RFIFO",
    0x58: "TFIFO",
    0x60: "MMRDH",
    0x64: "MMTOP",
}

DMA_REGS = {
    0x00: "DSCPTR_NXT",
    0x04: "ADDRSTART",
    0x08: "CFG",
    0x0C: "XCNT",
    0x10: "XMOD",
    0x14: "YCNT",
    0x18: "YMOD",
    0x24: "DSCPTR_CUR",
    0x28: "DSCPTR_PRV",
    0x2C: "ADDR_CUR",
    0x30: "STAT",
    0x34: "XCNT_CUR",
    0x38: "YCNT_CUR",
    0x40: "BWLCNT",
    0x44: "BWLCNT_CUR",
    0x48: "BWMCNT",
    0x4C: "BWMCNT_CUR",
}

# Offsets inside the DAI0 page 0x310C9000; DAI1 repeats them at 0x310CA000.
DAI_REGS = {
    0x0C0: "CLK0",
    0x0C4: "CLK1",
    0x0C8: "CLK2",
    0x0CC: "CLK3",
    0x0D0: "CLK4",
    0x0D4: "CLK5",
    0x100: "DAT0",
    0x104: "DAT1",
    0x108: "DAT2",
    0x10C: "DAT3",
    0x110: "DAT4",
    0x114: "DAT5",
    0x118: "DAT6",
    0x140: "FS0",
    0x144: "FS1",
    0x148: "FS2",
    0x150: "FS4",
    0x180: "PIN0",
    0x184: "PIN1",
    0x188: "PIN2",
    0x190: "PIN4",
    0x1C0: "MISC0",
    0x1C4: "MISC1",
    0x1E0: "PBEN0",
    0x1E4: "PBEN1",
    0x1E8: "PBEN2",
    0x1EC: "PBEN3",
    0x200: "IMSK_FE",
    0x204: "IMSK_RE",
    0x210: "IMSK_PRI",
    0x220: "IRPTL_H",
    0x224: "IRPTL_L",
    0x230: "IRPTL_HS",
    0x234: "IRPTL_LS",
    0x2E4: "PIN_STAT",
    0x2E8: "GBL_SP_EN",
    0x2EC: "GBL_INT_EN",
}

ASRC_REGS = {0x00: "CTL01", 0x04: "CTL23", 0x08: "MUTE", 0x20: "RAT01", 0x24: "RAT23"}

# Offsets inside the PCG0 blocks at 0x310C9300 (channels A, B) and
# 0x310CA300 (channels C, D).
PCG_REGS = {
    0x310C9300: "CTLA0",
    0x310C9304: "CTLA1",
    0x310C9308: "CTLB0",
    0x310C930C: "CTLB1",
    0x310C9310: "PW1",
    0x310C9314: "SYNC1",
    0x310CA300: "CTLC0",
    0x310CA304: "CTLC1",
    0x310CA308: "CTLD0",
    0x310CA30C: "CTLD1",
    0x310CA310: "PW2",
    0x310CA314: "SYNC2",
}

# DMA channel -> (base, peripheral it serves), Table 27-2.
DMA_CHANNELS = {
    0: (0x31022000, "SPORT0A"),
    1: (0x31022080, "SPORT0B"),
    2: (0x31022100, "SPORT1A"),
    3: (0x31022180, "SPORT1B"),
    4: (0x31022200, "SPORT2A"),
    5: (0x31022280, "SPORT2B"),
    6: (0x31022300, "SPORT3A"),
    7: (0x31022380, "SPORT3B"),
    8: (0x310A7000, "MDMA0 src"),
    9: (0x310A7080, "MDMA0 dst"),
    10: (0x31023000, "SPORT4A"),
    11: (0x31023080, "SPORT4B"),
    12: (0x31023100, "SPORT5A"),
    13: (0x31023180, "SPORT5B"),
    14: (0x31023200, "SPORT6A"),
    15: (0x31023280, "SPORT6B"),
    16: (0x31023300, "SPORT7A"),
    17: (0x31023380, "SPORT7B"),
    18: (0x310A7100, "MDMA1 src"),
    19: (0x310A7180, "MDMA1 dst"),
    20: (0x31026080, "UART0 TX"),
    21: (0x31026000, "UART0 RX"),
    22: (0x3102D000, "SPI0 TX"),
    23: (0x3102D080, "SPI0 RX"),
    24: (0x3102D100, "SPI1 TX"),
    25: (0x3102D180, "SPI1 RX"),
    26: (0x3102D200, "SPI2 TX"),
    27: (0x3102D280, "SPI2 RX"),
    30: (0x30FFF000, "LP0"),
    34: (0x31026180, "UART1 TX"),
    35: (0x31026100, "UART1 RX"),
    36: (0x30FFF080, "LP1"),
    37: (0x31026280, "UART2 TX"),
    38: (0x31026200, "UART2 RX"),
    39: (0x3109A000, "MDMA2 src"),
    40: (0x3109A080, "MDMA2 dst"),
    43: (0x3109B000, "MDMA3 src"),
    44: (0x3109B080, "MDMA3 dst"),
}


def _blocks():
    """(base, size, name, register names by offset or None)."""
    blocks = []
    for n in range(8):
        base = 0x31002000 + 0x100 * n
        blocks.append((base, 0x80, f"SPORT{n}A", None))
        blocks.append((base + 0x80, 0x80, f"SPORT{n}B", None))
    for n, base in enumerate(
        (0x31001400, 0x31001500, 0x31001600, 0x31001000, 0x31001100, 0x31001200)
    ):
        blocks.append((base, 0x100, f"TWI{n}", None))
    for n, base in enumerate((0x31003000, 0x31003400, 0x31003800)):
        blocks.append((base, 0x400, f"UART{n}", None))
    blocks += [
        (0x31004000, 0x80, "PORTA", None),
        (0x31004080, 0x80, "PORTB", None),
        (0x31004100, 0x80, "PORTC", None),
        (0x31004400, 0x100, "PADS0", None),
        (0x31005000, 0x100, "PINT0", None),
        (0x31005100, 0x100, "PINT1", None),
        (0x31005200, 0x100, "PINT2", None),
        (0x31008000, 0x800, "WDOG0", None),
        (0x31008800, 0x800, "WDOG1", None),
        (0x3100B000, 0x1000, "CNT0", None),
        (0x31016000, 0x1000, "HADC0", None),
        (0x31018000, 0x1000, "TIMER0", None),
        (0x31027000, 0x1000, "OSPI0", None),
        (0x3102E000, 0x1000, "SPI0", SPI_REGS),
        (0x3102F000, 0x1000, "SPI1", SPI_REGS),
        (0x31030000, 0x1000, "SPI2", SPI_REGS),
        (0x31070000, 0x1000, "DMC0", None),
        (0x31071000, 0x1000, "DMC0 PHY", None),
        (0x31080000, 0x1000, "L2CTL0", None),
        (0x31089000, 0x1000, "SEC0", None),
        (0x3108B000, 0x1000, "SPU0", None),
        (0x3108C000, 0x1000, "RCU0", None),
        (0x3108D000, 0x1000, "CGU0", None),
        (0x3108E000, 0x1000, "CGU1", None),
        (0x3108F000, 0x1000, "CDU0", None),
        (0x3109D000, 0x1000, "MLB0", None),
        (0x310A5000, 0x1000, "CRC0", None),
        (0x310A6000, 0x1000, "CRC1", None),
        (0x30FFE000, 0x100, "LP0", None),
        (0x30FFE100, 0x100, "LP1", None),
    ]
    for n, (base, owner) in DMA_CHANNELS.items():
        blocks.append((base, 0x80, f"DMA{n} ({owner})", DMA_REGS))
    return blocks


BLOCKS = sorted(_blocks())


def name_address(value: int) -> str | None:
    """Peripheral register name for a value, or None outside every known block."""
    if value in CORE_MMR_REGS:
        return CORE_MMR_REGS[value]
    for page, dai in ((0x310C9000, 0), (0x310CA000, 1)):
        if page <= value < page + 0x1000:
            off = value - page
            if value in PCG_REGS:
                return f"PCG0_{PCG_REGS[value]}"
            if off in DAI_REGS:
                return f"DAI{dai}_{DAI_REGS[off]}"
            if 0x240 <= off < 0x280:
                reg = ASRC_REGS.get(off - 0x240)
                return f"ASRC{dai}_{reg}" if reg else f"ASRC{dai}+{off - 0x240:#x}"
            if 0x280 <= off < 0x300:
                return f"SPDIF{dai}+{off - 0x280:#x}"
            return f"DAI{dai} page+{off:#x}"
    for base, size, name, regs in BLOCKS:
        if base <= value < base + size:
            off = value - base
            if regs and off in regs:
                return f"{name} {regs[off]}"
            return f"{name}+{off:#x}"
    return None


def values_of(fields: dict) -> list[tuple[str, int, int]]:
    """(field, value, bits) for each 32-bit pair X[31:16]/X[15:0] and each
    other field of at least 9 bits."""
    out = []
    paired = set()
    for label in fields:
        if label.endswith("[31:16]"):
            stem = label[: -len("[31:16]")]
            low = stem + "[15:0]"
            if low in fields:
                out.append((stem, (fields[label] << 16) | fields[low], 32))
                paired.update((label, low))
    for label, value in fields.items():
        if label in paired or "[" not in label:
            continue
        hi, lo = label[label.index("[") + 1 : -1].split(":")
        bits = int(hi) - int(lo) + 1
        if bits >= 9:
            out.append((label, value, bits))
    return out


# tools/sharc_dev/sharcfn/'s module docstring: "Type10a_rel (and, as measured
# there, Type10a_abs too) is absent from tools/sharc_dev/sharc_visa_tables.py's VISA
# form set entirely, so any real occurrence surfaces as a disassembler
# desync -- resynced and reported ..., not silently skipped." decode_table.
# json's Type10a_rel/10a_abs entries match real VISA bit patterns by
# coincidence, not because either is an instruction the assembler ever
# emits; decode_all() previously trusted any offset that decoded, so a
# desync landing on one of these patterns fed a real "instruction" into the
# depth/sweep alignment sweep below, instead of the "cannot decode, step
# 2 bytes and keep going" treatment desync recovery needs. Confirmed
# against DT2 1.16 sw 0x1c1338-based sharcdb: only 4 of 4808 raw Type10a_rel
# matches in the whole image ever reached "aligned" status, all four raw
# field dumps (tools/sharc_dev/sharcfn/ has no renderer for the form, again because
# it is not real); excluding both from this table lets the sweep resync
# through sw 0x1c4b99-0x1c4bb5 onto the real 64-bit voice-record store at sw
# 0x1c4ba0 (Type3b, l=1, i=I4, d=store) that a Type10a_rel "hit" at sw
# 0x1c4b9b had been corrupting. Same set tools/sharc_dev/sharc_disasm.py's
# NEVER_ALIGNED_FORMS holds; re-exported under this module's original name
# since tests and comments here already refer to it that way.
_NEVER_ALIGNED_FORMS = sharc_disasm.NEVER_ALIGNED_FORMS

# The width/form choice below -- and tools/sharc_dev/sharc_core/sequencer.decode_at()'s
# single-PC decode -- both live in one place now:
# tools/sharc_dev/sharc_disasm.resolve_confident_width() (see its docstring and
# WIDTH_LOOKAHEAD comment for the DT2 1.16 sw 0x1c4b99/sw 0x1c4e10 cases this
# catches). decode_all() below calls it directly with this module's own
# memoized `table` as raw_at, so a whole-image walk stays O(n) instead of
# re-decoding each lookahead step; tools/sharc_dev/sharc_disasm.decode_confident()/
# decode_confident_loaded() call the same function per-PC for decode_at().


def decode_all(data: bytes) -> dict:
    """offset -> Instruction for every even offset that decodes, excluding
    the decode-trap forms in _NEVER_ALIGNED_FORMS (see the comment above),
    then re-resolved by tools/sharc_dev/sharc_disasm.resolve_confident_width() wherever
    a narrower VISA form also matches and is the better-supported reading."""
    table = {}
    for offset in range(0, len(data) - 1, 2):
        insn = next(disassemble(data, offset, count=1), None)
        if (
            insn is not None
            and insn.kind != "unknown"
            and insn.type_name not in _NEVER_ALIGNED_FORMS
        ):
            table[offset] = insn
    for offset, insn in list(table.items()):
        if insn.length_bytes is None or insn.length_bytes <= 2:
            continue
        resolved = sharc_disasm.resolve_confident_width(
            offset,
            insn,
            table.get,
            lambda p, mb: sharc_disasm._narrow_candidates(data, p, mb),
        )
        if resolved is not insn:
            table[offset] = resolved
    return table


def depths(table: dict, size: int) -> dict:
    """offset -> length of the longest decoded run ending with the instruction there."""
    depth = {}
    for offset in range(0, size, 2):
        insn = table.get(offset)
        if insn is None:
            continue
        here = depth.get(offset, 0) + 1
        depth[offset] = here
        succ = offset + insn.length_bytes
        if depth.get(succ, 0) < here:
            depth[succ] = here
    # depth[offset] above holds the run length through offset; entries at
    # offsets that do not decode are successor marks only.
    return {off: d for off, d in depth.items() if off in table}


def sweep_offsets(table: dict, size: int) -> set:
    offsets = set()
    offset = 0
    while offset < size:
        insn = table.get(offset)
        if insn is None:
            offset += 2
            continue
        offsets.add(offset)
        offset += insn.length_bytes
    return offsets


def scan(
    data: bytes, base_sw: int, ranges: list, wanted: set, min_depth: int = 1
) -> list:
    table = decode_all(data)
    depth = depths(table, len(data))
    sweep = sweep_offsets(table, len(data))
    hits = []
    for offset in sorted(table):
        insn = table[offset]
        for label, value, bits in values_of(insn.fields):
            in_range = bits == 32 and any(lo <= value <= hi for lo, hi in ranges)
            if not in_range and value not in wanted:
                continue
            if depth[offset] < min_depth:
                continue
            hits.append(
                {
                    "sw": base_sw + offset // 2,
                    "offset": offset,
                    "form": insn.type_name,
                    "kind": insn.kind,
                    "field": label,
                    "value": value,
                    "bits": bits,
                    # the other fields; those holding the value are left out
                    "fields": {
                        k: v
                        for k, v in insn.fields.items()
                        if k.split("[")[0] != label.split("[")[0]
                    },
                    "depth": depth[offset],
                    "sweep": offset in sweep,
                    "name": name_address(value) if bits == 32 else None,
                }
            )
    return hits


def scan_words(stream: bytes, ranges: list, wanted: set) -> list:
    """Hits among the 32-bit words of every loaded block of a boot stream."""
    from tools.sharc_dev import sharcldr

    hits = []
    for block in sharcldr.parse_blocks(stream):
        if block["fill"] or not block["payload_len"]:
            continue
        target = block["target_address"]
        start = block["payload_offset"]
        for off in range((-target) % 4, block["payload_len"] - 3, 4):
            value = struct.unpack_from("<I", stream, start + off)[0]
            if not (any(lo <= value <= hi for lo, hi in ranges) or value in wanted):
                continue
            addr = target + off
            hits.append(
                {
                    "block": block["index"],
                    "addr": addr,
                    "data_ptr": addr - 0x28000000
                    if 0x28000000 <= addr < 0x30000000
                    else None,
                    "value": value,
                    "name": name_address(value),
                }
            )
    return hits


def _int(text: str) -> int:
    return int(text, 0)


def _range(text: str) -> tuple:
    lo, hi = text.split(":")
    return int(lo, 0), int(hi, 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("region", nargs="?")
    ap.add_argument(
        "--words",
        metavar="STREAM",
        help="scan the 32-bit words of every block of this boot stream instead",
    )
    ap.add_argument(
        "--base-sw",
        type=_int,
        default=0x1C1338,
        help="short-word address of the first word (default 0x1c1338)",
    )
    ap.add_argument(
        "--range",
        type=_range,
        action="append",
        dest="ranges",
        help="LO:HI, inclusive, for 32-bit values (repeatable; default the peripheral space)",
    )
    ap.add_argument(
        "--value",
        type=_int,
        action="append",
        default=[],
        help="exact value, any field of 9+ bits or a 32-bit pair (repeatable)",
    )
    ap.add_argument(
        "--min-depth",
        type=int,
        default=1,
        help="drop hits whose decoded run is shorter than this",
    )
    ap.add_argument("--json", help="write the hits as JSON")
    args = ap.parse_args(argv)

    if (args.region is None) == (args.words is None):
        ap.error("give either REGION.bin or --words STREAM")
    ranges = args.ranges
    if ranges is None:
        ranges = [] if args.value else [PERIPHERAL_SPACE]

    if args.words:
        with open(args.words, "rb") as f:
            hits = scan_words(f.read(), ranges, set(args.value))
        for h in hits:
            ptr = (
                f"data {h['data_ptr']:#08x}" if h["data_ptr"] is not None else " " * 13
            )
            name = f"  {h['name']}" if h["name"] else ""
            print(
                f"block {h['block']:>3}  {h['addr']:#010x}  {ptr}  {h['value']:#010x}{name}"
            )
        print(f"\n{len(hits)} hits")
        if args.json:
            with open(args.json, "w") as f:
                json.dump(
                    {
                        "stream": args.words,
                        "ranges": [list(r) for r in ranges],
                        "values": args.value,
                        "hits": hits,
                    },
                    f,
                    indent=1,
                )
        return 0

    with open(args.region, "rb") as f:
        data = f.read()
    hits = scan(data, args.base_sw, ranges, set(args.value), args.min_depth)

    for h in hits:
        others = " ".join(f"{k}={v:#x}" for k, v in h["fields"].items())
        name = f"  {h['name']}" if h["name"] else ""
        print(
            f"SW {h['sw']:#08x}  {h['form']:<12} {h['field']}={h['value']:#010x}"
            f"  depth={h['depth']:<4} {'sweep' if h['sweep'] else '-    '}  [{others}]{name}"
        )

    by_block = {}
    for h in hits:
        if h["name"]:
            block = h["name"].split(" ")[0].split("_")[0].split("+")[0]
            by_block.setdefault(block, []).append(h["sw"])
    print(f"\n{len(hits)} hits")
    for block in sorted(by_block):
        sws = by_block[block]
        print(f"{block:<10} {len(sws):>3}  SW {min(sws):#08x}-{max(sws):#08x}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(
                {
                    "region": args.region,
                    "base_sw": args.base_sw,
                    "ranges": [list(r) for r in ranges],
                    "values": args.value,
                    "hits": hits,
                },
                f,
                indent=1,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
