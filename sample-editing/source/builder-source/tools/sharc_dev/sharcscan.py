#!/usr/bin/env python3
"""Recover a call graph and find candidate dispatch tables in a SHARC blob.

The DSP (container section 7) is where audio actually lives; the ColdFire
MAIN OS only RPCs parameter changes to it (see sharcldr.py). Whether DSP
algorithms are table-dispatched decides whether a new machine can be added
without touching the algorithm code, so recovering a call graph and finding
candidate dispatch tables is the route to answering that.

48-bit SHARC+ VISA instructions assemble from three little-endian 16-bit
words, MSB word FIRST: w0,w1,w2 = struct.unpack_from('<3H', data, off);
insn = (w0<<32)|(w1<<16)|w2. Candidate instructions occur only at EVEN byte
offsets. Two forms matter here:

  cjump absolute,  type 25a_direct: (insn>>24)&0xFFFFFF == 0x180400,
      target = insn & 0xFFFFFF. This is how ADI's compiler emits a call,
      so decoding it recovers the call graph.
  cjump PC-relative, type 25a_pcrel: (insn>>24)&0xFFFFFF == 0x184400,
      displacement = insn & 0xFFFFFF as a signed 24-bit value.

Code address spaces are identified by the top 8 bits of a target
(target >> 16). Measured: the shared second-stage loader lives in
0x12xxxx; Digitakt's and Digitone's product code both use 0x1cxxxx and
0xb8xxxx. Those numbers are NOT hardcoded here -- `code_spaces` discovers
them by histogramming target>>16 over all cjump-absolute targets and
keeping the buckets with at least --min-space-hits hits (default 20).

The decode is validated by a negative control: scanning 49,152 bytes of
float-coefficient data from the same blob yields ZERO cjump hits, while
Digitakt's 224 KB code region yields 1,656 with targets in two tight
address clusters, and the shared loader yields 65 whose targets are ALL
in 0x12xxxx matching its own declared load address 0x00120230. A decoder
matching noise cannot produce that split.

Measured on Digitakt 1.15C: 1,656 call sites, 506 distinct functions (245
in 0x1cxxxx, 261 in 0xb8xxxx), 265 called exactly once, most-called
0x1cb18e at 162 calls.

Candidate tables found: Digitakt 7, Digitone 9 (>= 6 entries). Several are
shared runtime, identifiable because they appear in BOTH images with the
same shape -- e.g. an 18-entry table at file offset 0x0028e4 is
byte-identical in both, and both carry a 10-entry uniform-stride-17
table. Product-specific tables are the interesting ones.

A LEAD, explicitly not a conclusion: Digitakt has an 11-entry ascending,
100%-indirect-only table at file offset 0x0034fc, and Digitakt exposes
exactly 5 source machines plus 6 filters = 11. Digitone's
structurally-parallel table at 0x003a94 has 6 entries. This is suggestive
and unproven -- confirming it requires disassembling the code that
indexes the table, which needs a full VISA decoder this tool does not
have. Do not present it as established.

`find_tables` is a heuristic over bit patterns: a run of words that look
like code addresses may be data that happens to look that way, which is
exactly why `indirect_only_pct` is reported.

Usage:
    uv run python -m tools.sharc_dev.sharcscan section_7_digitakt.bin --json out.json
    uv run python -m tools.sharc_dev.sharcscan section_7_digitakt.bin --region 11248:236000
"""

import argparse
import hashlib
import json
import struct
import sys
from collections import defaultdict


def _cjump_prefix(type_name, expected):
    """Take the bits-47:24 cjump prefix from sharc_visa_tables, not a literal.

    That file is the transcription of the vendor figures and is still being
    revised, so a constant copied out of it here would drift silently and the
    scan would quietly return nothing. Importing keeps one source of truth;
    `expected` is the value measured and validated against the negative
    control, so a mismatch means the table changed under us and should be
    loud rather than silent.
    """
    try:
        from tools.sharc_dev import sharc_visa_tables as _t

        entry = _t.get_type(type_name)
        got = (entry["opcode_value"] >> 24) & 0xFFFFFF
    except Exception:
        return expected
    if got != expected:
        raise SystemExit(
            "sharc_visa_tables %s prefix is 0x%06x, this tool was validated "
            "against 0x%06x. Re-validate the scan before trusting it."
            % (type_name, got, expected)
        )
    return got


CJUMP_ABS_MASK = _cjump_prefix("25a_direct", 0x180400)
CJUMP_PCREL_MASK = _cjump_prefix("25a_pcrel", 0x184400)


def instructions(data):
    """Yield (offset, insn) for every even offset that has a full 6-byte
    window available."""
    n = len(data)
    for off in range(0, n - 5, 2):
        w0, w1, w2 = struct.unpack_from("<3H", data, off)
        insn = (w0 << 32) | (w1 << 16) | w2
        yield off, insn


def call_graph(data, region=None):
    """Return {target: count} recovered from cjump-absolute instructions.

    `region`, if given, is a (lo, hi) byte range restricting the scan.
    """
    lo, hi = region if region else (0, len(data))
    calls = defaultdict(int)
    for off, insn in instructions(data):
        if off < lo or off >= hi:
            continue
        if (insn >> 24) & 0xFFFFFF == CJUMP_ABS_MASK:
            target = insn & 0xFFFFFF
            calls[target] += 1
    return dict(calls)


def code_spaces(calls, min_hits=20):
    """Discover code address spaces as the high-16 bucket (target>>16) of
    cjump-absolute targets, keeping buckets with at least `min_hits` hits."""
    hist = defaultdict(int)
    for target, count in calls.items():
        hist[target >> 16] += count
    return sorted(bucket for bucket, hits in hist.items() if hits >= min_hits)


def find_tables(data, spaces, min_entries=6):
    """Scan the whole file as little-endian uint32 for runs of >= min_entries
    consecutive words whose (value >> 16) is in `spaces`."""
    space_set = set(spaces)
    n = len(data)
    words = []
    for off in range(0, n - 3, 4):
        value = struct.unpack_from("<I", data, off)[0]
        words.append((off, value))

    tables = []
    run = []
    for off, value in words:
        if (value >> 16) in space_set:
            run.append((off, value))
        else:
            if len(run) >= min_entries:
                tables.append(_make_table(run))
            run = []
    if len(run) >= min_entries:
        tables.append(_make_table(run))
    return tables


def _make_table(run):
    offset = run[0][0]
    values = [v for _, v in run]
    ascending = all(values[i] < values[i + 1] for i in range(len(values) - 1))
    strides = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    uniform_stride = (
        strides[0] if strides and all(s == strides[0] for s in strides) else None
    )
    return {
        "offset": offset,
        "count": len(values),
        "values": values,
        "ascending": ascending,
        "uniform_stride": uniform_stride,
    }


def classify_tables(tables, calls):
    """Annotate each table with indirect_only_count / indirect_only_pct:
    entries that never appear as a direct cjump target."""
    for t in tables:
        indirect_only = sum(1 for v in t["values"] if v not in calls)
        t["indirect_only_count"] = indirect_only
        t["indirect_only_pct"] = round(100.0 * indirect_only / t["count"], 1)
    return tables


def _parse_region(s):
    lo, hi = s.split(":")
    return int(lo, 0), int(hi, 0)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob", help="path to a section_7_*.bin")
    ap.add_argument(
        "--region",
        type=_parse_region,
        help="LO:HI byte range restricting the CALL-GRAPH scan "
        "(int(s,0)); table scanning always covers the "
        "whole file",
    )
    ap.add_argument(
        "--min-entries",
        type=int,
        default=6,
        help="minimum consecutive entries to call a run a table",
    )
    ap.add_argument(
        "--min-space-hits",
        type=int,
        default=20,
        help="minimum hits for a target>>16 bucket to count as a code space",
    )
    ap.add_argument("--json", help="write the full result dict as JSON")
    ap.add_argument(
        "--top", type=int, default=20, help="how many most-called targets to print"
    )
    args = ap.parse_args()

    data = open(args.blob, "rb").read()  # noqa: SIM115
    calls = call_graph(data, args.region)
    spaces = code_spaces(calls, args.min_space_hits)
    tables = find_tables(data, spaces, args.min_entries)
    classify_tables(tables, calls)

    space_hits = defaultdict(int)
    for target, count in calls.items():
        space_hits[target >> 16] += count

    tables_sorted = sorted(tables, key=lambda t: -t["count"])
    top_targets = sorted(calls.items(), key=lambda kv: -kv[1])[: args.top]

    result = {
        "path": args.blob,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "call_sites": sum(calls.values()),
        "distinct_targets": len(calls),
        "code_spaces": spaces,
        "space_hits": {hex(k): v for k, v in space_hits.items()},
        "top_targets": [{"target": t, "count": c} for t, c in top_targets],
        "tables": tables_sorted,
    }

    if args.json:
        json.dump(result, open(args.json, "w"), indent=2)  # noqa: SIM115

    print("=== %s ===" % args.blob)
    print("size=%d  sha256=%s" % (len(data), result["sha256"]))
    print(
        "call_sites=%d  distinct_targets=%d"
        % (result["call_sites"], result["distinct_targets"])
    )
    print(
        "code_spaces=%s"
        % (", ".join("0x%02xxxxx(%d)" % (s, space_hits[s]) for s in spaces) or "-")
    )

    print("\n--- most-called targets ---")
    for target, count in top_targets:
        print("0x%06x  calls=%d" % (target, count))

    print("\n--- candidate dispatch tables ---")
    for t in tables_sorted:
        preview = ",".join("0x%06x" % v for v in t["values"][:4])
        print(
            "off=0x%06x  entries=%-4d  ascending=%-5s  stride=%-6s  "
            "indirect_only=%5.1f%%  [%s...]"
            % (
                t["offset"],
                t["count"],
                t["ascending"],
                t["uniform_stride"],
                t["indirect_only_pct"],
                preview,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
