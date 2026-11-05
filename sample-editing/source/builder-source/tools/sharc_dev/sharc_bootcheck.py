#!/usr/bin/env python3
"""Check a SHARC+ boot stream (firmware section 7) against the rules the boot ROM applies.

The emulator never runs the boot ROM: it flattens the block list into memory. This module is the
stand-in for what the ROM would reject. Every rule cites the public ADSP-2156x SHARC+ Hardware
Reference (docs/sharc/SOURCES.md; text in out/refs/adsp-2156x-hwr), chapter 40, page numbers
printed ("HWR 40-nn"). Results are marked as in docs/findings (finding 06, "Boot ROM
conformance"): a rule marked ROM is stated by the manual and enforced by the ROM; a rule marked
CONVENTION is not stated as a ROM check but holds in every stock stream and costs nothing to keep.

    uv run python -m tools.sharc_dev.sharc_bootcheck out/sections/dt2-1.16/section_7_BLOB.bin
    uv run python -m tools.sharc_dev.sharc_bootcheck --syx patched.syx [--stock stock.syx]

Exit status 0 when the stream has no error; --stock also checks that the stream is the stock one with
extra blocks inserted before FINAL (the only change patchkit makes).

Rules (id, kind, source):

  H1  ROM   HWR 40-39 Table 40-27, 40-60 Table 40-41: HDRSIGN of a block for core 0 is 0xAD
            (0xAC, 0xAB are other cores); a different value ends the boot (ROM_BOOT_ERR_HDRSGN 0xA).
  H2  ROM   HDRCHK: the XOR of the 16 header bytes is zero (ROM_BOOT_ERR_HDRCHK 0xC). Table 40-27
            words it as "an 8-bit XOR checksum of the other 24 bits"; every stock header satisfies
            the whole-header XOR, and the three-byte reading does not (tests/sharc/analysis/test_sharc_bootcheck.py).
  H3  ROM   HWR 40-39: TARGET_ADDRESS and BYTE_COUNT are multiples of 4 (the kernel uses 32-bit DMA).
            TARGET_ADDRESS is checked where a block loads memory: DN2's FIRST and FINAL blocks carry
            the entry point 0x1c12e2 (a short-word pc, 2-byte aligned) and the stock DN2 image boots.
  H4  ROM   HWR 40-40: the first block of the stream has BFLAG_FIRST ("always needed at the beginning").
  H5  ROM   HWR 40-40: a FIRST block cannot be a FILL block.
  H6  ROM   HWR 40-42 Table 40-33: FILL: no payload follows the header; ARGUMENT is the fill word.
  H7  ROM   HWR 40-42: IGNORE: BYTE_COUNT payload bytes are in the stream and skipped; INIT, CALLBACK,
            FINAL and AUX are cleared on an IGNORE block.
  H8  ROM   HWR 40-41/40-42: a FINAL block ends the boot; nothing after it is read, and a stream
            without FINAL fails (ROM_BOOT_ERR_NOFINAL 0xE). A payload past the end of the data never
            completes. The ColdFire sends every byte of the section (finding 06), so bytes after
            FINAL would never be accepted (the handshake would stall): trailing bytes are an error.
  H9  ROM   HWR 40-2 Table 40-1 and its note: the 8 KB at 0x200FE000..0x20100000 (boot kernel stack,
            intermediate buffers, block header copy) "is not a bootable region": a block aimed there
            ends the boot in the error handler.
  H10 ROM   HWR 40-37/40-41: INDIRECT and CALLBACK blocks need a registered callback / intermediate
            buffer; no stock block uses them. Reported as errors (not modelled), AUX and SAVE as
            warnings.
  H11 ROM   HWR 40-43: INIT calls the function at TARGET_ADDRESS after its payload (if any) is loaded.
            The target must be word aligned (H3); INIT with a payload loads it first.
  H12 CONV  HWR 40-85 (pNextDxe): the ARGUMENT of a FIRST block is the byte count from the end of its
            header to the next application's header, or to the end of the stream for the last
            application. Used by adi_rom_Boot() to walk the list after boot, not by the slave boot
            itself; every stock FIRST block satisfies it, so a patch that grows the stream keeps it.
  H13 CONV  The entry point is the TARGET_ADDRESS of the last FIRST block (HWR 40-65: the boot kernel
            saves it to RCU_SVECT0); stock FINAL blocks carry the same address. Reported as info.
  L1  ROM   HWR 8-1: L2 SRAM is 1 MB, 0x20000000..0x20100000, initialised (ECC) by the preboot before
            the first block (HWR 40-5), so a block to L2 below 0x200FE000 needs no preparation.
            Other targets (L1 aliases 0x28xxxxxx, DDR 0x8xxxxxxx) are reported as info: the ROM's
            "valid memory" test is not specified publicly and the datasheet map is not in out/refs.
  S1  CONT  HWR 40-21 (SPI slave boot): the manual asks hosts to send multiples of 1024 bytes. Stock
            sections are not (length mod 1024 is reported), so the rule is not enforced; the tail
            length of a patched stream is reported for the pre-flash review.
"""

from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

HEADER = 16
BFLAGS = {
    4: "SAVE",
    5: "AUX",
    8: "FILL",
    10: "CALLBACK",
    11: "INIT",
    12: "IGNORE",
    13: "INDIRECT",
    14: "FIRST",
    15: "FINAL",
}
RESERVED_BITS = (6, 7, 9)
CORE0_SIGN = 0xAD
CORE_SIGNS = (0xAD, 0xAC, 0xAB)

L2_BASE, L2_END = 0x2000_0000, 0x2010_0000
# HWR 40-2 Table 40-1: boot kernel workspace, not a bootable region.
ROM_WORKSPACE = (0x200F_E000, 0x2010_0000)
# ColdFire boot routine limit (finding 06): the depacked section may not exceed this.
MAX_SECTION = 0xFFFFF


@dataclass
class Block:
    index: int
    offset: int
    code: int
    target: int
    count: int
    argument: int

    @property
    def flags(self) -> set[str]:
        return {name for bit, name in BFLAGS.items() if self.code >> bit & 1}

    @property
    def fill(self) -> bool:
        return bool(self.code & 1 << 8)

    @property
    def payload_len(self) -> int:
        return 0 if self.fill else self.count

    @property
    def end(self) -> int:
        return self.offset + HEADER + self.payload_len


@dataclass
class Finding:
    level: str  # "error" | "warn" | "info"
    rule: str
    offset: int
    text: str

    def __str__(self) -> str:
        return f"{self.level:5} {self.rule:3} @{self.offset:#08x}  {self.text}"


@dataclass
class Report:
    blocks: list[Block] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    length: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def rules(self, level: str = "error") -> set[str]:
        return {f.rule for f in self.findings if f.level == level}


def header_xor(raw: bytes) -> int:
    x = 0
    for b in raw:
        x ^= b
    return x


def classify_target(address: int, count: int) -> str:
    """'l2', 'workspace' (overlaps the boot kernel's 8 KB) or 'other' for [address, address + count)."""
    end = address + max(count, 1)
    if address < ROM_WORKSPACE[1] and end > ROM_WORKSPACE[0]:
        return "workspace"
    if address >= L2_BASE and end <= L2_END:
        return "l2"
    return "other"


def check(blob: bytes, core_sign: int = CORE0_SIGN) -> Report:
    """Walk BLOB the way the boot kernel would and report every rule that fails."""
    rep = Report(length=len(blob))
    add = rep.findings.append
    offset = 0
    index = 0
    saw_final = False
    firsts: list[Block] = []
    while offset < len(blob):
        if offset + HEADER > len(blob):
            add(
                Finding(
                    "error",
                    "H8",
                    offset,
                    f"{len(blob) - offset} bytes left, a header needs {HEADER} (stream cut)",
                )
            )
            break
        raw = blob[offset : offset + HEADER]
        code, target, count, argument = struct.unpack("<4I", raw)
        blk = Block(index, offset, code, target, count, argument)
        sign = code >> 24
        if sign != core_sign:
            kind = "another core" if sign in CORE_SIGNS else "an invalid signature"
            add(
                Finding(
                    "error",
                    "H1",
                    offset,
                    f"HDRSIGN {sign:#04x} is {kind} (core 0 is 0xAD)",
                )
            )
            break
        if header_xor(raw):
            add(
                Finding(
                    "error",
                    "H2",
                    offset,
                    f"header XOR is {header_xor(raw):#04x}, not 0",
                )
            )
            break
        rep.blocks.append(blk)
        flags = blk.flags
        where = f"block {index} {target:#x}+{count:#x}"
        if index == 0 and "FIRST" not in flags:
            add(
                Finding(
                    "error", "H4", offset, f"{where}: the first block lacks BFLAG_FIRST"
                )
            )
        loads = "IGNORE" not in flags and count > 0
        if count % 4 or (loads and target % 4):
            add(
                Finding(
                    "error",
                    "H3",
                    offset,
                    f"{where}: count or load address not a multiple of 4",
                )
            )
        if any(code >> b & 1 for b in RESERVED_BITS):
            add(
                Finding(
                    "warn",
                    "H1",
                    offset,
                    f"{where}: reserved flag bits set ({code & 0x2C0:#x})",
                )
            )
        if "FILL" in flags and "FIRST" in flags:
            add(Finding("error", "H5", offset, f"{where}: FIRST combined with FILL"))
        if flags & {"INDIRECT", "CALLBACK"}:
            add(
                Finding(
                    "error",
                    "H10",
                    offset,
                    f"{where}: {sorted(flags & {'INDIRECT', 'CALLBACK'})} not modelled",
                )
            )
        if flags & {"AUX", "SAVE"}:
            add(
                Finding(
                    "warn",
                    "H10",
                    offset,
                    f"{where}: {sorted(flags & {'AUX', 'SAVE'})} set",
                )
            )
        effective = set(flags)
        if "IGNORE" in flags:
            effective -= {"INIT", "CALLBACK", "FINAL", "AUX"}
            if flags & {"INIT", "CALLBACK", "FINAL", "AUX"}:
                add(
                    Finding(
                        "info",
                        "H7",
                        offset,
                        f"{where}: IGNORE clears {sorted(flags & {'INIT', 'CALLBACK', 'FINAL', 'AUX'})}",
                    )
                )
        if blk.end > len(blob):
            add(
                Finding(
                    "error",
                    "H8",
                    offset,
                    f"{where}: payload runs {blk.end - len(blob)} bytes past the stream",
                )
            )
            break
        if "IGNORE" not in flags and count > 0:
            cls = classify_target(target, count)
            if cls == "workspace":
                add(
                    Finding(
                        "error",
                        "H9",
                        offset,
                        f"{where}: inside the boot kernel workspace 0x200FE000..0x20100000",
                    )
                )
            elif cls == "other":
                add(
                    Finding(
                        "info",
                        "L1",
                        offset,
                        f"{where}: outside L2 (L1 alias or DDR): validity not checkable here",
                    )
                )
        if "FIRST" in flags:
            firsts.append(blk)
        offset = blk.end
        index += 1
        if "FINAL" in effective:
            saw_final = True
            break
    if not rep.blocks and not rep.findings:
        add(Finding("error", "H8", 0, "empty stream"))
    if not saw_final and not any(f.rule in ("H1", "H2") for f in rep.findings):
        add(Finding("error", "H8", offset, "no FINAL block (ROM_BOOT_ERR_NOFINAL 0xE)"))
    if saw_final and offset < len(blob):
        add(
            Finding(
                "error",
                "H8",
                offset,
                f"{len(blob) - offset} bytes after FINAL (never read; the host would stall)",
            )
        )
    # H12: the next-application pointers.
    for n, blk in enumerate(firsts):
        want = (firsts[n + 1].offset if n + 1 < len(firsts) else rep.length) - (
            blk.offset + HEADER
        )
        if blk.argument != want:
            add(
                Finding(
                    "error",
                    "H12",
                    blk.offset,
                    f"FIRST at block {blk.index}: ARGUMENT {blk.argument:#x}, the next application is {want:#x} bytes on",
                )
            )
    if firsts and saw_final:
        entry = firsts[-1].target
        finals = [b for b in rep.blocks if "FINAL" in b.flags]
        note = (
            ""
            if finals[-1].target == entry
            else f" (FINAL carries {finals[-1].target:#x})"
        )
        add(
            Finding(
                "info",
                "H13",
                firsts[-1].offset,
                f"entry {entry:#x} from the last FIRST block{note}",
            )
        )
    if rep.length > MAX_SECTION:
        add(
            Finding(
                "error",
                "S1",
                0,
                f"{rep.length:#x} bytes: the ColdFire sends at most {MAX_SECTION:#x}",
            )
        )
    add(
        Finding(
            "info",
            "S1",
            0,
            f"{rep.length} bytes, mod 1024 = {rep.length % 1024} (stock streams are not multiples of 1024)",
        )
    )
    return rep


def check_against_stock(stock: bytes, patched: bytes) -> list[Finding]:
    """The patched stream must be STOCK with whole blocks inserted before its FINAL block. Stock
    block headers are unchanged (a FIRST block's ARGUMENT grows with the insertion, H12, and its
    HDRCHK with it); payload bytes may be edited in place (reported, P6); the FINAL block is
    untouched."""
    out: list[Finding] = []
    a, b = check(stock), check(patched)
    if not a.ok:
        out.append(
            Finding(
                "error",
                "P0",
                0,
                "the stock stream itself fails: " + "; ".join(str(f) for f in a.errors),
            )
        )
        return out
    sb = [x for x in a.blocks]
    pb = [x for x in b.blocks]
    final_at = next(i for i, x in enumerate(sb) if "FINAL" in x.flags)
    extra = len(pb) - len(sb)
    if extra < 0 or len(pb) <= final_at:
        out.append(
            Finding(
                "error",
                "P1",
                0,
                f"patched stream has {len(pb)} blocks, stock {len(sb)}",
            )
        )
        return out
    inserted = pb[final_at : final_at + extra]
    grown = sum(x.end - x.offset for x in inserted)
    for i in range(final_at):
        s, p = sb[i], pb[i]
        sraw = stock[s.offset : s.end]
        praw = patched[p.offset : p.end]
        if "FIRST" in s.flags:
            # Only ARGUMENT and HDRCHK may differ, and ARGUMENT only by the inserted bytes.
            sh, ph = (
                struct.unpack("<4I", sraw[:HEADER]),
                struct.unpack("<4I", praw[:HEADER]),
            )
            grew = grown if s.offset + HEADER + s.argument >= sb[final_at].offset else 0
            if (
                sh[1:3] != ph[1:3]
                or ph[3] != sh[3] + grew
                or sraw[HEADER:] != praw[HEADER:]
                or (sh[0] ^ ph[0]) & ~0x00FF0000
            ):
                out.append(
                    Finding(
                        "error",
                        "P2",
                        s.offset,
                        f"FIRST block {i} changed beyond its next pointer (grew {grew:#x})",
                    )
                )
        elif sraw[:HEADER] != praw[:HEADER]:
            out.append(
                Finding("error", "P2", s.offset, f"stock block {i}: header changed")
            )
        elif sraw != praw:
            n = sum(x != y for x, y in zip(sraw[HEADER:], praw[HEADER:], strict=True))
            out.append(
                Finding(
                    "info",
                    "P6",
                    s.offset,
                    f"stock block {i} {s.target:#x}+{s.count:#x}: {n} payload bytes edited in place",
                )
            )
    if patched[pb[final_at + extra].offset :] != stock[sb[final_at].offset :]:
        out.append(
            Finding("error", "P2", pb[final_at + extra].offset, "FINAL block changed")
        )
    for blk in inserted:
        if classify_target(blk.target, blk.count) != "l2":
            out.append(
                Finding(
                    "error",
                    "P3",
                    blk.offset,
                    f"inserted block {blk.target:#x}+{blk.count:#x} is not in bootable L2",
                )
            )
        if blk.flags:
            out.append(
                Finding(
                    "error",
                    "P3",
                    blk.offset,
                    f"inserted block carries flags {sorted(blk.flags)}",
                )
            )
        if blk.code & 0xF != 1:
            out.append(
                Finding(
                    "error",
                    "P3",
                    blk.offset,
                    f"inserted block BCODE {blk.code & 0xF}, stock blocks use 1",
                )
            )
        end = blk.target + blk.count
        for s in sb:
            if (
                s.target < end
                and blk.target < s.target + max(s.count, 0)
                and "INIT" not in s.flags
                and "IGNORE" not in s.flags
            ):
                out.append(
                    Finding(
                        "error",
                        "P4",
                        blk.offset,
                        f"inserted block overlaps stock block {s.index} ({s.target:#x}+{s.count:#x})",
                    )
                )
    out.append(
        Finding(
            "info",
            "P5",
            0,
            f"{extra} block(s) inserted, {grown} bytes; stream {len(stock)} -> {len(patched)}",
        )
    )
    return out


def section7(syx: Path) -> bytes:
    from tools.patchkit import dsp_patch

    return dsp_patch.section_blob(syx, 7)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stream", nargs="?", type=Path, help="a section 7 .bin")
    ap.add_argument("--syx", type=Path, help="read section 7 from this .syx")
    ap.add_argument(
        "--stock",
        type=Path,
        help="stock .syx: also check STREAM is the stock stream plus inserted blocks",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="errors only")
    a = ap.parse_args(argv)
    if a.syx is not None:
        blob = section7(a.syx)
    elif a.stream is not None:
        blob = a.stream.read_bytes()
    else:
        ap.error("give a section 7 .bin or --syx")
    rep = check(blob)
    findings = list(rep.findings)
    if a.stock is not None:
        findings += check_against_stock(section7(a.stock), blob)
    for f in findings:
        if not a.quiet or f.level == "error":
            print(f)
    bad = [f for f in findings if f.level == "error"]
    print(
        f"{len(rep.blocks)} blocks, {rep.length} bytes: {'FAIL' if bad else 'ok'} ({len(bad)} errors)"
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
