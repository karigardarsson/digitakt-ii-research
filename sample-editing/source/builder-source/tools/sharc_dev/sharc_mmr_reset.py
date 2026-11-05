"""Read concrete reset facts from the public ADSP-2156x HWR register tables.

Input is tools/devtools/refstext.py's extraction, never a DSP capture. This supplies
reset values only; it does not emulate register side effects or ROM handoff.
"""

from __future__ import annotations

import re

_ROW = re.compile(r"^(0x[0-9a-fA-F]{8})\s+(\S+)\s+.+?\s+(0x[0-9a-fA-F]{8})\s*$")

# Known single-row typos in the published manual, keyed by (address, register name).
# A matching row is dropped before the conflict check; every other conflict still raises.
HWR_ERRATA: dict[tuple[int, str], str] = {
    (0x31026108, "DDE_CFG"): (
        "Rev 1.1 lists DDE_CFG at DMA35_CFG's address; the DMA channel register wins"
    ),
    (0x31026130, "DDE_STAT"): (
        "Rev 1.1 lists DDE_STAT at DMA35_STAT's address; the DMA channel register wins "
        "(the whole DDE block 0x31026100.. repeats DMA35's, whose slot fits the "
        "0x80-stride channel layout there: DMA21 0x6000, DMA20 0x6080, DMA35 0x6100)"
    ),
}


def parse_reset_tables(text: str) -> dict[int, tuple[int, str]]:
    registers: dict[int, tuple[int, str]] = {}
    for line in text.splitlines():
        match = _ROW.match(line)
        if match is None:
            continue
        address, name, reset = int(match[1], 16), match[2], int(match[3], 16)
        if not 0x30000000 <= address < 0x32000000:
            continue
        if (address, name) in HWR_ERRATA:
            continue
        previous = registers.get(address)
        if previous is not None and previous[0] != reset:
            raise ValueError(
                f"conflicting reset values at {address:#x}: {previous[0]:#x} and {reset:#x}"
            )
        registers[address] = reset, name
    if not registers:
        raise ValueError("no ADSP-2156x peripheral reset table rows found")
    return registers
