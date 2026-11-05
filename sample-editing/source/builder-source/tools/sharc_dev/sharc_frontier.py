#!/usr/bin/env python3
"""Bounded exploratory continuation across declared SHARC evidence gaps.

This driver never changes the strict tracer's instruction semantics.  Every
restart must name and hash the exact skipped loader bytes, and every result is
labelled exploratory/non-qualifying.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import sharc_trace as trace  # noqa: E402
from tools.sharc_dev.sharcldr import LoadedMemory  # noqa: E402

MAX_ISLANDS = 3
MAX_STEPS = 50_000
MAX_ISLAND_BYTES = 4_096
MAX_ACCESS_SAMPLES = 16
MODES = ("optimistic-preserve", "conservative-clobber")
DEFAULT_TARGETS = (
    ("SPORT4A", 0x31002400, 0x31002480),
    ("DMA10", 0x31023000, 0x31023080),
    ("PCG-C", 0x310CA300, 0x310CA318),
)


@dataclass(frozen=True)
class Restart:
    stop_pc_sw: int
    successor_pc_sw: int
    sha256: str
    assumptions: tuple[str, ...]


@dataclass(frozen=True)
class Target:
    name: str
    start: int
    end: int


@dataclass
class Path:
    state: trace.State
    used: tuple[int, ...] = ()


def _integer(value, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(name + " must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as error:
            raise ValueError(name + " must be an integer") from error
    raise ValueError(name + " must be an integer")


def _boolean(value, name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(name + " must be boolean")
    return value


def _manifest(path: str) -> tuple[dict, str]:
    try:
        with open(path, "rb") as source:
            raw = source.read()
        document = json.loads(raw)
    except OSError as error:
        raise ValueError(str(error)) from error
    except json.JSONDecodeError as error:
        raise ValueError("invalid manifest JSON: " + str(error)) from error
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("manifest must be a version-1 object")
    return document, hashlib.sha256(raw).hexdigest()


def _restarts(document: Mapping) -> dict[int, Restart]:
    records = document.get("restarts", [])
    if not isinstance(records, list):
        raise ValueError("restarts must be an array")
    result = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("restart %d must be an object" % index)
        stop = _integer(record.get("stop_pc_sw"), "stop_pc_sw")
        successor = _integer(record.get("successor_pc_sw"), "successor_pc_sw")
        digest = record.get("sha256")
        assumptions = record.get("assumptions", [])
        if stop < 0 or successor <= stop:
            raise ValueError("restart successor must follow stop PC")
        if (successor - stop) * 2 > MAX_ISLAND_BYTES:
            raise ValueError("restart extent exceeds 4096 bytes")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("restart sha256 must contain 64 hexadecimal digits")
        try:
            int(digest, 16)
        except ValueError as error:
            raise ValueError("restart sha256 must be hexadecimal") from error
        if (
            not isinstance(assumptions, list)
            or not assumptions
            or not all(isinstance(item, str) and item for item in assumptions)
        ):
            raise ValueError("restart assumptions must be nonempty strings")
        if stop in result:
            raise ValueError("duplicate restart stop PC %#x" % stop)
        result[stop] = Restart(stop, successor, digest.lower(), tuple(assumptions))
    return result


def _targets(document: Mapping) -> tuple[Target, ...]:
    records = document.get("targets")
    if records is None:
        return tuple(Target(*record) for record in DEFAULT_TARGETS)
    if not isinstance(records, list) or not records:
        raise ValueError("targets must be a nonempty array")
    result = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            raise ValueError("target %d must have a name" % index)
        start = _integer(record.get("start"), "target start")
        end = _integer(record.get("end"), "target end")
        if start < 0 or end <= start:
            raise ValueError("target end must be greater than start")
        result.append(Target(record["name"], start, end))
    return tuple(result)


def _initial_state(memory: LoadedMemory, document: Mapping, start: int) -> trace.State:
    reset = _boolean(document.get("core_reset_state"), "core_reset_state")
    uregs: dict[int, trace.Value] = (
        {
            trace.UREG_CODES[name]: trace.Const(value)
            for name, value in trace.CORE_UREG_RESET_VALUES.items()
        }
        if reset
        else {}
    )
    sets = document.get("sets", {})
    if not isinstance(sets, dict):
        raise ValueError("sets must be an object")
    for name, value in sets.items():
        if not isinstance(name, str):
            raise ValueError("set names must be strings")
        seeded = (
            value
            if isinstance(value, str) and value.startswith("@")
            else _integer(value, name)
        )
        uregs[trace._seed_code(name)] = trace._seed_value(seeded)
    mmrs: dict[int, trace.Value] = (
        {
            address: trace.Const(value)
            for address, value in trace.CORE_MMR_RESET_VALUES.items()
        }
        if reset
        else {}
    )
    follow = _boolean(document.get("follow_loaded_calls"), "follow_loaded_calls")
    skip_entries = _boolean(
        document.get("skip_provisional_entries"), "skip_provisional_entries"
    )
    if skip_entries and not follow:
        raise ValueError("skip_provisional_entries requires follow_loaded_calls")
    dossier_bytes = _integer(document.get("dossier_bytes", 0), "dossier_bytes")
    max_call_depth = _integer(document.get("max_call_depth", 8), "max_call_depth")
    if not 0 <= dossier_bytes <= 256:
        raise ValueError("dossier_bytes must be between 0 and 256")
    if not 1 <= max_call_depth <= 32:
        raise ValueError("max_call_depth must be between 1 and 32")
    return trace.State(
        start,
        uregs,
        concrete=memory,
        follow_loaded_calls=follow,
        continue_external_calls=_boolean(
            document.get("continue_external_calls"), "continue_external_calls"
        ),
        dossier_bytes=dossier_bytes,
        max_call_depth=max_call_depth,
        skip_provisional_entries=skip_entries,
        at_loaded_entry=skip_entries,
        assume_nw32=_boolean(document.get("assume_nw32"), "assume_nw32"),
        core_reset_state=reset,
        mmrs=mmrs,
    )


def _island_bytes(memory: LoadedMemory, restart: Restart) -> bytes:
    size = (restart.successor_pc_sw - restart.stop_pc_sw) * 2
    raw = memory.read_sw(restart.stop_pc_sw, size)
    if raw is None:
        raise ValueError("restart range is not fully loader-backed")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != restart.sha256:
        raise ValueError(
            "restart %#x..%#x hash mismatch: %s"
            % (restart.stop_pc_sw, restart.successor_pc_sw, digest)
        )
    return raw


def _clobber(state: trace.State) -> None:
    for code, name in enumerate(trace.UREG_NAMES):
        state.uregs[code] = trace.Unknown("frontier clobber after " + name)
    state.pending = None
    state.loops.clear()
    state.status_stack.clear()
    state.call_stack.clear()
    state.overlay.clear()
    state.mmrs.clear()
    state.special.clear()
    # Keep the loader's code map for instruction fetch/call following, but do
    # not treat any pre-island data-memory or MMR value as concrete.
    state.data_memory_tainted = True


def _restart(
    path: Path,
    memory: LoadedMemory,
    restart: Restart,
    mode: str,
) -> None:
    state = path.state
    raw = _island_bytes(memory, restart)
    state.trace.append(
        {
            "pc_sw": restart.stop_pc_sw,
            "form": None,
            "action": "exploratory-restart",
            "successor_pc_sw": restart.successor_pc_sw,
            "skipped_bytes": raw.hex(),
            "sha256": restart.sha256,
            "mode": mode,
            "assumptions": list(restart.assumptions),
            "evidence_limited": True,
            "qualifying": False,
        }
    )
    state.stopped = None
    if mode == "conservative-clobber":
        _clobber(state)
    state.pc_sw = restart.successor_pc_sw
    path.used += (restart.stop_pc_sw,)


def _target_hit(events: Sequence[dict], targets: Sequence[Target]) -> dict | None:
    for event in reversed(events):
        if event.get("action") not in ("load", "store"):
            continue
        address = event.get("address")
        if not isinstance(address, int):
            continue
        for target in targets:
            if target.start <= address < target.end:
                return {
                    "name": target.name,
                    "address": address,
                    "pc_sw": event.get("pc_sw"),
                    "source_action": event["action"],
                    "value": event.get("value", event.get("concrete_value")),
                }
    return None


def run(
    memory: LoadedMemory,
    document: Mapping,
    mode: str,
    max_islands: int = MAX_ISLANDS,
    max_steps: int = MAX_STEPS,
    max_states: int = 256,
) -> list[Path]:
    if mode not in MODES:
        raise ValueError("unknown frontier mode: " + mode)
    if not 0 <= max_islands <= MAX_ISLANDS:
        raise ValueError("max_islands must be between 0 and 3")
    if not 1 <= max_steps <= MAX_STEPS:
        raise ValueError("max_steps must be between 1 and 50000")
    if not 1 <= max_states <= 1024:
        raise ValueError("max_states must be between 1 and 1024")
    start = _integer(document.get("start_sw"), "start_sw")
    if start < 0:
        raise ValueError("start_sw must be nonnegative")
    restarts = _restarts(document)
    targets = _targets(document)
    active = [Path(_initial_state(memory, document, start))]
    done = []
    while active:
        path = active.pop(0)
        state = path.state
        if state.steps >= max_steps:
            trace._stop(state, None, "max-steps")
            done.append(path)
            continue
        previous_events = len(state.trace)
        children = trace._execute(state, trace.decode_at(memory, None, state.pc_sw))
        for child in children:
            child_path = Path(child, path.used)
            hit = _target_hit(child.trace[previous_events:], targets)
            if hit is not None:
                child.trace.append(
                    {
                        "pc_sw": child.pc_sw,
                        "form": None,
                        "action": "frontier-target",
                        **hit,
                    }
                )
                child.stopped = "frontier-target: " + hit["name"]
                done.append(child_path)
                continue
            restart = restarts.get(child.pc_sw) if child.stopped else None
            if (
                restart is not None
                and child.pc_sw not in child_path.used
                and len(child_path.used) < max_islands
            ):
                _restart(child_path, memory, restart, mode)
                active.append(child_path)
            elif child.stopped:
                done.append(child_path)
            elif len(active) + len(done) >= max_states - 1:
                trace._stop(child, None, "max-states")
                done.append(child_path)
                return done
            else:
                active.append(child_path)
    return done


def report(
    paths: Sequence[Path],
    document: Mapping,
    mode: str,
    manifest_sha256: str,
    max_islands: int,
    max_steps: int,
) -> dict:
    start = _integer(document.get("start_sw"), "start_sw")
    summaries = trace.summarize([path.state for path in paths], start)["states"]
    endpoints = {}
    for summary, path in zip(summaries, paths, strict=False):
        hits = [
            event
            for event in path.state.trace
            if event.get("action") == "frontier-target"
        ]
        key = (
            summary["stopped"],
            summary["stop_pc_sw"],
            summary["stop_form"],
            len(path.used),
            tuple(hit["name"] for hit in hits),
        )
        endpoint = endpoints.get(key)
        if endpoint is None:
            accesses = summary["peripheral_accesses"]
            half = MAX_ACCESS_SAMPLES // 2
            samples = (
                accesses
                if len(accesses) <= MAX_ACCESS_SAMPLES
                else accesses[:half] + accesses[-half:]
            )
            endpoint = {
                "count": 0,
                "stopped": summary["stopped"],
                "stop_pc_sw": summary["stop_pc_sw"],
                "stop_form": summary["stop_form"],
                "steps_min": summary["steps"],
                "steps_max": summary["steps"],
                "restart_count": len(path.used),
                "restart_pcs": list(path.used),
                "target_hits": hits,
                "peripheral_accesses": {
                    "count": len(accesses),
                    "samples": samples,
                    "truncated": len(accesses) > len(samples),
                },
                "last_events": summary["last_events"],
            }
            endpoints[key] = endpoint
        endpoint["count"] += 1
        endpoint["steps_min"] = min(endpoint["steps_min"], summary["steps"])
        endpoint["steps_max"] = max(endpoint["steps_max"], summary["steps"])
    return {
        "version": 1,
        "mode": mode,
        "qualifying": False,
        "evidence": "exploratory restart seams",
        "manifest_sha256": manifest_sha256,
        "limits": {"max_islands": max_islands, "max_steps": max_steps},
        "start_sw": start,
        "state_count": len(paths),
        "endpoints": sorted(
            endpoints.values(),
            key=lambda item: (
                item["stop_pc_sw"],
                item["stopped"] or "",
                item["restart_count"],
            ),
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="section-7 loader stream")
    parser.add_argument("manifest")
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--max-islands", type=int, default=MAX_ISLANDS)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--max-states", type=int, default=256)
    parser.add_argument("--trace-json")
    args = parser.parse_args(argv)
    try:
        document, manifest_sha256 = _manifest(args.manifest)
        with open(args.source, "rb") as source:
            memory = LoadedMemory.from_stream(source.read())
        paths = run(
            memory,
            document,
            args.mode,
            args.max_islands,
            args.max_steps,
            args.max_states,
        )
        result = report(
            paths,
            document,
            args.mode,
            manifest_sha256,
            args.max_islands,
            args.max_steps,
        )
        if args.trace_json:
            with open(args.trace_json, "w") as output:
                json.dump(
                    [
                        {
                            "stopped": path.state.stopped,
                            "steps": path.state.steps,
                            "restart_pcs": list(path.used),
                            "trace": path.state.trace,
                        }
                        for path in paths
                    ],
                    output,
                    indent=2,
                )
                output.write("\n")
    except (OSError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
