"""Dynamic machine-instruction count of one generated region call, via lldb.

Runs a region_bench binary (`bench` mode, two calls) under lldb, stops at the
entry of r_<ENTRY>, single-steps until the call returns, and reports the
machine instructions executed, the instructions per SHARC instruction (the
`insns_in_call` you pass, from the bench table), the distinct instruction
addresses and 64-byte cache lines touched (the hot code footprint of one
call), and the share of loads, stores, branches and stack accesses among the
executed instructions. Steps into helper calls that are not inlined.

    aot_dyntrace.py BINARY PACKED_IMAGE STATES ENTRY SHARC_INSNS [--max-steps N]

Slow (about 5k steps per second): use it on one call of a hot region. The
binary must have been built with a symbol for the region (any region_bench
variant). Needs Xcode's lldb Python module.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter


def load_lldb():
    path = subprocess.check_output(["lldb", "-P"], text=True).strip()
    sys.path.insert(0, path)
    import lldb  # type: ignore

    return lldb


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("binary")
    ap.add_argument("image")
    ap.add_argument("states")
    ap.add_argument("entry")
    ap.add_argument("sharc_insns", type=int)
    ap.add_argument("--max-steps", type=int, default=2_000_000)
    ap.add_argument(
        "--dump", metavar="FILE", help="write the executed instructions in order"
    )
    ap.add_argument("--call", type=int, default=2, help="trace this call (1 = first)")
    ap.add_argument(
        "--region",
        help="region function to trace when it differs from the entry (1C9538 for entry B81368)",
    )
    args = ap.parse_args()
    lldb = load_lldb()
    dbg = lldb.SBDebugger.Create()
    dbg.SetAsync(False)
    target = dbg.CreateTarget(args.binary)
    bp = target.BreakpointCreateByRegex("r_%s$" % re.escape(args.region or args.entry))
    if bp.GetNumLocations() != 1:
        sys.exit(
            "expected one location for r_%s, got %d"
            % (args.entry, bp.GetNumLocations())
        )
    info = lldb.SBLaunchInfo(
        ["bench", args.image, args.states, args.entry, "--reps", "1", "--calls", "8"]
    )
    err = lldb.SBError()
    proc = target.Launch(info, err)
    if not err.Success():
        sys.exit(str(err))
    for _ in range(args.call - 1):
        proc.Continue()
    thread = proc.GetSelectedThread()
    frame = thread.GetFrameAtIndex(0)
    lr = frame.FindRegister("lr").GetValueAsUnsigned()
    sp0 = frame.FindRegister("sp").GetValueAsUnsigned()
    pcs: Counter[int] = Counter()
    order: list[int] = []
    steps = 0
    while steps < args.max_steps:
        frame = thread.GetFrameAtIndex(0)
        pc = frame.GetPC()
        if pc == lr and frame.FindRegister("sp").GetValueAsUnsigned() >= sp0:
            break
        pcs[pc] += 1
        order.append(pc)
        thread.StepInstruction(False)
        steps += 1
    else:
        print("max steps reached")
    # Mnemonics of the executed addresses, from the debugger's disassembler.
    kinds: Counter[str] = Counter()
    for pc in pcs:
        insn = target.ReadInstructions(lldb.SBAddress(pc, target), 1)[0]
        mn = insn.GetMnemonic(target)
        ops = insn.GetOperands(target)
        n = pcs[pc]
        if mn.startswith("ldr") or mn.startswith("ldp") or mn.startswith("ldu"):
            kinds["load"] += n
        elif mn.startswith("str") or mn.startswith("stp") or mn.startswith("stu"):
            kinds["store"] += n
        elif (
            mn.startswith("b")
            or mn.startswith("cb")
            or mn.startswith("tb")
            or mn == "ret"
        ):
            kinds["branch"] += n
        if ("[sp" in ops) and mn[:2] in ("ld", "st"):
            kinds["stack"] += n
    if args.dump:
        with open(args.dump, "w") as fh:
            sym = target.ResolveLoadAddress(order[0]).GetSymbol()
            start = sym.GetStartAddress().GetLoadAddress(target)
            fh.write("# function-start %x\n" % start)
            for pc in order:
                insn = target.ReadInstructions(lldb.SBAddress(pc, target), 1)[0]
                fh.write(
                    "%x  %s %s\n"
                    % (pc, insn.GetMnemonic(target), insn.GetOperands(target))
                )
    lines = {pc >> 6 for pc in pcs}
    print("steps (machine insns executed): %d" % steps)
    print("per SHARC insn (%d): %.1f" % (args.sharc_insns, steps / args.sharc_insns))
    print(
        "distinct instruction addresses: %d (%d bytes), 64-byte lines: %d (%d bytes)"
        % (len(pcs), 4 * len(pcs), len(lines), 64 * len(lines))
    )
    for k in ("load", "store", "branch", "stack"):
        print("  %-7s %.1f%% of executed" % (k, 100 * kinds[k] / steps))
    proc.Kill()


if __name__ == "__main__":
    main()
