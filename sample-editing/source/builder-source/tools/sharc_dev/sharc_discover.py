#!/usr/bin/env python3
"""Deterministic SHARC discovery harness.

This is a composition layer over the existing loader, inventory, dossier and
strict tracer modules.  It adds no instruction semantics.  Given a section-7
loader stream and a checked-in hypothesis manifest, it emits one stable JSON
report joining:

* the complete static function/direct-call inventory;
* every aligned Type9a/Type9b indirect transfer;
* loader-final runs of 32-bit words that point at decoded instruction PCs;
* literal-to-pointer-table-to-indirect-transfer structural joins;
* bounded strict trace matrices declared by the manifest; and
* optional, explicitly advisory evidence from an existing Ghidra dump.

The report is discovery evidence, not proof that a candidate is an audio root,
machine-specific path or safe hook.  Generated reports belong under ``out/``
and must not be committed.

Usage:
    uv run python -m tools.sharc_dev.sharc_discover BLOB MANIFEST \
        --dump-db out/ghidra/sharc-dt2-1.16/xrefs.sqlite \
        -o out/sharc-discovery/dt2-1.16.json
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib import import_module
from multiprocessing import get_context
from pathlib import Path
from typing import Any, cast

_HERE = Path(__file__).resolve().parent

from tools.sharc_dev import sharc_trace as trace  # noqa: E402
from tools.sharc_dev import (  # noqa: E402
    sharcflow,  # noqa: E402
    sharcfn,  # noqa: E402
    sharcinv,  # noqa: E402
    sharcldr,  # noqa: E402
)
from tools.sharc_dev.sharc_disasm import decode_loaded_at  # noqa: E402

sharc_index = import_module("tools.sharc_dev.sharc_index")  # noqa: E402
from tools.sharc_dev.sharc_static import (  # noqa: E402
    _compact_functions,
    build_static_context,
    find_pointer_runs,
)  # noqa: E402

SCHEMA = "sharc-discovery/v1"
MANIFEST_SCHEMA = "sharc-discovery-manifest/v1"
EVIDENCE_CLASSES = (
    "static-decode",
    "loaded-bytes",
    "strict-trace",
    "dump-advisory",
    "manifest-hypothesis",
)
DEFAULT_LIMITS = {
    "dispatch_window_sw": 64,
    "max_pointer_tables": 256,
    "max_pointer_entries_per_table": 64,
    "max_trace_cases": 256,
}
TRACE_MAX_STEPS = 1_000
TRACE_MAX_STATES = 64
TRACE_WORK_CAP = 200_000


class _PhaseProfile:
    """Nondeterministic timing side channel; never part of report data."""

    def __init__(self) -> None:
        self._last = time.perf_counter()
        self.rows: list[tuple[str, float]] = []

    def mark(self, phase: str) -> None:
        now = time.perf_counter()
        self.rows.append((phase, now - self._last))
        self._last = now

    def write(self) -> None:
        for phase, seconds in self.rows:
            print(f"profile phase={phase} seconds={seconds:.6f}", file=sys.stderr)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as error:
            raise ValueError(f"{name} must be an integer") from error
    raise ValueError(f"{name} must be an integer")


def _positive(value: Any, name: str, maximum: int | None = None) -> int:
    result = _integer(value, name)
    if result <= 0 or maximum is not None and result > maximum:
        suffix = f" and at most {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be positive{suffix}")
    return result


def _seed_value(value: Any, name: str) -> int | str:
    if isinstance(value, str) and value.startswith("@") and len(value) > 1:
        return value
    return _integer(value, name)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read manifest: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("manifest must be a JSON object")
    if document.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
    digest = document.get("image_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("manifest image_sha256 must be a 64-character string")
    try:
        if digest != digest.lower():
            raise ValueError
        int(digest, 16)
    except ValueError as error:
        raise ValueError(
            "manifest image_sha256 must be lowercase hexadecimal"
        ) from error
    blocks = document.get("code_blocks", list(sharcinv.CODE_BLOCKS))
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("manifest code_blocks must be a non-empty array")
    document["code_blocks"] = [
        _integer(value, f"code_blocks[{index}]") for index, value in enumerate(blocks)
    ]
    document["min_depth"] = _positive(document.get("min_depth", 8), "min_depth", 64)
    limits = dict(DEFAULT_LIMITS)
    supplied_limits = document.get("limits", {})
    if not isinstance(supplied_limits, dict):
        raise ValueError("manifest limits must be an object")
    for key in limits:
        if key in supplied_limits:
            limits[key] = _positive(supplied_limits[key], f"limits.{key}")
    if limits["max_trace_cases"] > 256:
        raise ValueError("limits.max_trace_cases must be at most 256")
    document["limits"] = limits
    for key in (
        "roots",
        "trace_probes",
        "dispatch_target_probes",
        "selector_provenance_probes",
        "natural_selector_frontiers",
        "writer_targets",
        "register_effects",
        "core_vector_scans",
    ):
        value = document.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"manifest {key} must be an array")
        document[key] = value
    return document


def canonical_loader_dm_address(address: int) -> int:
    """Map a low-DM literal to its loader-memory alias.

    SHARC code commonly uses the unaliased low-DM spelling while loader blocks
    are addressed through the 0x28000000 alias.  This is an address-space join,
    not an assertion that the cell remains immutable at runtime.
    """
    return 0x28000000 + address if 0 <= address < 0x08000000 else address


def _indexed_context(
    blob: bytes, snapshot: Mapping[str, Any], blob_sha: str
) -> dict[str, Any]:
    """Rebuild cheap loader state around the index's immutable inventory."""
    blocks = sharcldr.parse_blocks(blob)
    functions = list(snapshot["function_inventory"])
    return {
        "data": blob,
        "blocks": blocks,
        "blocks_by_idx": {block["index"]: block for block in blocks},
        "mem": sharcldr.LoadedMemory.from_stream(blob, blocks),
        "functions": functions,
        "by_id": {function["id"]: function for function in functions},
        "sha256": blob_sha,
    }


def _register_effect_query(declaration: Mapping[str, Any], prefix: str) -> Any:
    calibration = declaration.get("calibration_forms", [])
    if not isinstance(calibration, list) or any(
        not isinstance(item, str) or not item for item in calibration
    ):
        raise ValueError(f"{prefix}.calibration_forms must be an array of strings")
    seed_globals = declaration.get("seed_global_constants", False)
    if not isinstance(seed_globals, bool):
        raise ValueError(f"{prefix}.seed_global_constants must be boolean")
    guard_declaration = declaration.get("finite_domain_guard")
    guard = None
    if guard_declaration is not None:
        if not isinstance(guard_declaration, Mapping):
            raise ValueError(f"{prefix}.finite_domain_guard must be an object")
        register = guard_declaration.get("register")
        if not isinstance(register, str) or not register:
            raise ValueError(f"{prefix}.finite_domain_guard.register must be a string")
        guard = sharc_index.FiniteDomainGuard(
            register,
            _integer(
                guard_declaration.get("shift_pc_sw"),
                f"{prefix}.finite_domain_guard.shift_pc_sw",
            ),
            _integer(
                guard_declaration.get("branch_pc_sw"),
                f"{prefix}.finite_domain_guard.branch_pc_sw",
            ),
            _integer(
                guard_declaration.get("frontier_pc_sw"),
                f"{prefix}.finite_domain_guard.frontier_pc_sw",
            ),
        )
    return sharc_index.RegisterEffectQuery(
        _integer(declaration.get("entry_sw"), f"{prefix}.entry_sw"),
        str(declaration.get("register", "R6")),
        calibration_forms=tuple(calibration),
        seed_global_constants=seed_globals,
        finite_domain_guard=guard,
    )


def join_dispatch_candidates(
    indirect_sites: Sequence[Mapping[str, Any]],
    literals: Sequence[Mapping[str, Any]],
    pointer_tables: Sequence[Mapping[str, Any]],
    window_sw: int,
) -> list[dict[str, Any]]:
    tables = {item["source_byte_address"]: item for item in pointer_tables}
    result = []
    for site in indirect_sites:
        if site["kind"] == "return":
            continue
        matches = []
        for literal in literals:
            distance = site["pc_sw"] - literal["pc_sw"]
            table = tables.get(canonical_loader_dm_address(literal["value"]))
            if (
                table is not None
                and literal["block"] == site["block"]
                and literal["owner"] == site["owner"]
                and 0 < distance <= window_sw
            ):
                matches.append((distance, literal, table))
        for distance, literal, table in sorted(
            matches, key=lambda item: (item[0], -item[2]["entry_count"])
        ):
            # Transparent lexicographic ranking: pointer-backed first, then
            # longer runs, nearer literals and lower site addresses.
            rank_tuple = [0, -table["entry_count"], distance, site["pc_sw"]]
            result.append(
                {
                    "site_pc_sw": site["pc_sw"],
                    "owner": site["owner"],
                    "kind": site["kind"],
                    "index_register": site["index_register"],
                    "modifier_register": site["modifier_register"],
                    "literal_pc_sw": literal["pc_sw"],
                    "literal_value": literal["value"],
                    "literal_loader_alias": canonical_loader_dm_address(
                        literal["value"]
                    ),
                    "table_byte_address": table["source_byte_address"],
                    "table_entry_count": table["entry_count"],
                    "target_pcs": [entry["target_sw"] for entry in table["entries"]],
                    "target_owners": table["target_owners"],
                    "rank_tuple": rank_tuple,
                    "evidence_classes": ["static-decode", "loaded-bytes"],
                    "assumptions": [
                        "nearby literal is the table base used by this transfer",
                        "32-bit little-endian normal-word table entries",
                    ],
                    "semantic_status": "not-proven",
                }
            )
    return sorted(result, key=lambda item: tuple(item["rank_tuple"]))


def _expand_probe(probe: Mapping[str, Any], maximum: int) -> list[dict[str, Any]]:
    if not isinstance(probe, dict):
        raise ValueError("each trace probe must be an object")
    base = probe.get("sets", {})
    matrix = probe.get("matrix", {})
    if not isinstance(base, dict) or not isinstance(matrix, dict):
        raise ValueError("trace probe sets and matrix must be objects")
    if not all(isinstance(key, str) for key in (*base, *matrix)):
        raise ValueError("trace probe set and matrix register names must be strings")
    base_sets = {key: _seed_value(value, f"sets.{key}") for key, value in base.items()}
    keys = sorted(matrix)
    values = []
    for key in keys:
        choices = matrix[key]
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"trace matrix {key} must be a non-empty array")
        values.append([_seed_value(value, f"matrix.{key}") for value in choices])
    combinations = list(itertools.product(*values)) if keys else [()]
    if len(combinations) > maximum:
        raise ValueError(
            f"trace probe expands to {len(combinations)} cases, cap is {maximum}"
        )
    result = []
    for combination in combinations:
        sets = dict(base_sets)
        sets.update(zip(keys, combination, strict=False))
        result.append(dict(sorted(sets.items())))
    return result


def _register_name(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a register name")
    import re

    if re.fullmatch(r"[RIM][0-9]|R1[0-5]|I1[0-5]|M1[0-5]", value) is None:
        raise ValueError(f"{name} is not a supported register: {value!r}")
    return value


def resolve_dispatch_target_probes(
    declarations: Sequence[Mapping[str, Any]],
    dispatches: Sequence[Mapping[str, Any]],
    pointer_tables: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expand manifest hypotheses against in-memory structural join results."""
    plans = []
    names = set()
    for number, declaration in enumerate(declarations):
        prefix = f"dispatch_target_probes[{number}]"
        if not isinstance(declaration, Mapping):
            raise ValueError(f"{prefix} must be an object")
        name = declaration.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError(f"{prefix}.name must be non-empty and unique")
        names.add(name)
        site = _integer(declaration.get("site_pc_sw"), f"{prefix}.site_pc_sw")
        address = _integer(
            declaration.get("table_byte_address"), f"{prefix}.table_byte_address"
        )
        matches = [
            item
            for item in dispatches
            if item["site_pc_sw"] == site and item["table_byte_address"] == address
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{prefix} structural dispatch match count is {len(matches)}, expected one"
            )
        tables = [
            item for item in pointer_tables if item["source_byte_address"] == address
        ]
        if len(tables) != 1:
            raise ValueError(
                f"{prefix} pointer table match count is {len(tables)}, expected one"
            )
        table = tables[0]
        if table.get("entries_truncated"):
            raise ValueError(f"{prefix} pointer table entries are truncated")
        selector = _register_name(
            declaration.get("selector_register"), f"{prefix}.selector_register"
        )
        start = _integer(declaration.get("start_sw"), f"{prefix}.start_sw")
        seeds = declaration.get("sets", {})
        if not isinstance(seeds, Mapping):
            raise ValueError(f"{prefix}.sets must be an object")
        sets = {
            _register_name(key, f"{prefix}.sets key"): _seed_value(
                value, f"{prefix}.sets.{key}"
            )
            for key, value in seeds.items()
        }
        selected = declaration.get("entry_indices")
        entry_count = table["entry_count"]
        if selected == "all":
            indices = list(range(entry_count))
        elif isinstance(selected, list) and selected:
            indices = [_integer(value, f"{prefix}.entry_indices") for value in selected]
        else:
            raise ValueError(
                f"{prefix}.entry_indices must be 'all' or a non-empty array"
            )
        if len(set(indices)) != len(indices):
            raise ValueError(f"{prefix}.entry_indices contains duplicates")
        if any(index < 0 or index >= entry_count for index in indices):
            raise ValueError(f"{prefix}.entry_indices contains out-of-range index")
        # Preserve explicitly supplied order only where it is meaningful; the
        # emitted evidence itself is always index ordered.
        indices.sort()
        max_steps = _positive(
            declaration.get("max_steps", 200), f"{prefix}.max_steps", TRACE_MAX_STEPS
        )
        max_states = _positive(
            declaration.get("max_states", 32), f"{prefix}.max_states", TRACE_MAX_STATES
        )
        for boolean in (
            "concrete_memory",
            "assume_nw32",
            "follow_loaded_calls",
            "continue_external_calls",
            "core_reset_state",
        ):
            if boolean in declaration and not isinstance(declaration[boolean], bool):
                raise ValueError(f"{prefix}.{boolean} must be boolean")
        assumptions = declaration.get("assumptions", [])
        if not isinstance(assumptions, list) or not all(
            isinstance(item, str) for item in assumptions
        ):
            raise ValueError(f"{prefix}.assumptions must be an array of strings")
        cases = []
        for entry_index in indices:
            entry = table["entries"][entry_index]
            case_sets = dict(sets)
            case_sets[selector] = entry_index
            cases.append(
                {
                    "sets": dict(sorted(case_sets.items())),
                    "breakpoints": [entry["target_sw"]],
                    "entry_index": entry_index,
                    "selector_value": entry_index,
                    "expected_target_sw": entry["target_sw"],
                    "dispatch_site_pc_sw": site,
                    "target_owner": entry["target_owner"],
                }
            )
        plans.append(
            {
                "name": name,
                "start_sw": start,
                "selector_register": selector,
                "max_steps": max_steps,
                "max_states": max_states,
                "concrete_memory": declaration.get("concrete_memory", True),
                "assume_nw32": declaration.get("assume_nw32", False),
                "follow_loaded_calls": declaration.get("follow_loaded_calls", False),
                "continue_external_calls": declaration.get(
                    "continue_external_calls", False
                ),
                "core_reset_state": declaration.get("core_reset_state", False),
                "max_call_depth": declaration.get("max_call_depth", 8),
                "assumptions": assumptions,
                "cases": cases,
                "dispatch": matches[0],
            }
        )
    return plans


def resolve_selector_provenance_probes(
    declarations: Sequence[Mapping[str, Any]],
    dispatches: Sequence[Mapping[str, Any]],
    pointer_tables: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expand manifest-seeded R-register selector hypotheses conservatively."""
    plans = []
    names = set()
    for number, declaration in enumerate(declarations):
        prefix = f"selector_provenance_probes[{number}]"
        if not isinstance(declaration, Mapping):
            raise ValueError(f"{prefix} must be an object")
        name = declaration.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError(f"{prefix}.name must be non-empty and unique")
        names.add(name)
        site = _integer(declaration.get("site_pc_sw"), f"{prefix}.site_pc_sw")
        address = _integer(
            declaration.get("table_byte_address"), f"{prefix}.table_byte_address"
        )
        dispatch_matches = [
            item
            for item in dispatches
            if item["site_pc_sw"] == site and item["table_byte_address"] == address
        ]
        tables = [
            item for item in pointer_tables if item["source_byte_address"] == address
        ]
        if len(dispatch_matches) != 1 or len(tables) != 1:
            raise ValueError(
                f"{prefix} requires exactly one structural dispatch and pointer table"
            )
        table = tables[0]
        if table.get("entries_truncated"):
            raise ValueError(f"{prefix} pointer table entries are truncated")
        source = _register_name(
            declaration.get("source_register"), f"{prefix}.source_register"
        )
        selector = _register_name(
            declaration.get("selector_register"), f"{prefix}.selector_register"
        )
        copy_pc = _integer(declaration.get("copy_pc_sw"), f"{prefix}.copy_pc_sw")
        table_load_pc = _integer(
            declaration.get("table_load_pc_sw"), f"{prefix}.table_load_pc_sw"
        )
        start = _integer(declaration.get("start_sw"), f"{prefix}.start_sw")
        seeds = declaration.get("sets", {})
        if not isinstance(seeds, Mapping):
            raise ValueError(f"{prefix}.sets must be an object")
        sets = {
            _register_name(key, f"{prefix}.sets key"): _seed_value(
                value, f"{prefix}.sets.{key}"
            )
            for key, value in seeds.items()
        }
        values = declaration.get("seed_values")
        if values == "all":
            indices = list(range(table["entry_count"]))
        elif isinstance(values, list) and values:
            indices = [_integer(value, f"{prefix}.seed_values") for value in values]
        else:
            raise ValueError(f"{prefix}.seed_values must be 'all' or a non-empty array")
        if len(set(indices)) != len(indices) or any(
            value < 0 or value >= table["entry_count"] for value in indices
        ):
            raise ValueError(
                f"{prefix}.seed_values must be unique in-range table indices"
            )
        max_steps = _positive(
            declaration.get("max_steps", 200), f"{prefix}.max_steps", TRACE_MAX_STEPS
        )
        max_states = _positive(
            declaration.get("max_states", 32), f"{prefix}.max_states", TRACE_MAX_STATES
        )
        for boolean in (
            "concrete_memory",
            "assume_nw32",
            "follow_loaded_calls",
            "continue_external_calls",
            "core_reset_state",
        ):
            if boolean in declaration and not isinstance(declaration[boolean], bool):
                raise ValueError(f"{prefix}.{boolean} must be boolean")
        assumptions = declaration.get("assumptions", [])
        if not isinstance(assumptions, list) or not all(
            isinstance(item, str) for item in assumptions
        ):
            raise ValueError(f"{prefix}.assumptions must be an array of strings")
        cases = []
        for value in sorted(indices):
            case_sets = dict(sets)
            case_sets[source] = value
            cases.append(
                {
                    "sets": dict(sorted(case_sets.items())),
                    "breakpoints": [table["entries"][value]["target_sw"]],
                    "seed_value": value,
                    "expected_target_sw": table["entries"][value]["target_sw"],
                    "selector_provenance": {
                        "source_register": source,
                        "selector_register": selector,
                        "copy_pc_sw": copy_pc,
                        "table_load_pc_sw": table_load_pc,
                        "dispatch_site_pc_sw": site,
                    },
                }
            )
        plans.append(
            {
                "name": name,
                "start_sw": start,
                "max_steps": max_steps,
                "max_states": max_states,
                "concrete_memory": declaration.get("concrete_memory", True),
                "assume_nw32": declaration.get("assume_nw32", False),
                "follow_loaded_calls": declaration.get("follow_loaded_calls", False),
                "continue_external_calls": declaration.get(
                    "continue_external_calls", False
                ),
                "core_reset_state": declaration.get("core_reset_state", False),
                "max_call_depth": declaration.get("max_call_depth", 8),
                "assumptions": assumptions,
                "cases": cases,
                "declaration": declaration,
                "dispatch": dispatch_matches[0],
            }
        )
    return plans


def classify_dispatch_target_summary(
    summary: Mapping[str, Any], expected_target_sw: int, site_pc_sw: int
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    """Require one terminal breakpoint and its exact audited dispatch edge."""
    states = summary.get("states", [])
    terminals = [
        {
            key: state.get(key)
            for key in (
                "stopped",
                "stop_pc_sw",
                "stop_form",
                "steps",
                "events",
                "last_events",
                "dispatch_branch_audit",
                "selector_transfer_audit",
            )
        }
        for state in states
    ]
    matching = [
        state
        for state in states
        if state.get("stopped") == "breakpoint"
        and state.get("stop_pc_sw") == expected_target_sw
    ]
    if len(states) > 1:
        return "ambiguous", terminals, None
    if not matching:
        if any(state.get("stopped") == "breakpoint" for state in states):
            return "mismatch", terminals, None
        return "unreached", terminals, None
    terminal = matching[0]
    branch_events = [
        event
        for event in terminal.get("dispatch_branch_audit", [])
        if event.get("action") == "branch"
        and event.get("pc_sw") == site_pc_sw
        and event.get("target_sw") == expected_target_sw
    ]
    if len(branch_events) == 0:
        return "missing-branch-evidence", terminals, None
    if len(branch_events) != 1:
        return "ambiguous", terminals, None
    return "resolved", terminals, terminal


def classify_selector_provenance_summary(
    states: Sequence[Mapping[str, Any]], expected_target_sw: int
) -> tuple[str, str, bool]:
    """Classify forced-selector traces without turning reachability into occurrence."""
    reached = any(
        state.get("stopped") == "breakpoint"
        and state.get("stop_pc_sw") == expected_target_sw
        and state.get("selector_transfer_audit", {}).get("copies")
        and state.get("selector_transfer_audit", {}).get("table_loads")
        and not state.get("selector_transfer_audit", {}).get("later_selector_writes")
        and any(
            event.get("target_sw") == expected_target_sw
            for event in state.get("selector_transfer_audit", {}).get(
                "dispatch_branches", []
            )
        )
        for state in states
    )
    return (
        "target-reached" if reached else ("unreached" if not states else "ambiguous"),
        "unique" if len(states) == 1 else "existential",
        False,
    )


_TRACE_WORKER_MEMORY: Any | None = None


def _init_trace_worker(blob_path: str) -> None:
    """Build the worker-local, read-only loader memory adapter."""
    global _TRACE_WORKER_MEMORY
    stream = Path(blob_path).read_bytes()
    _TRACE_WORKER_MEMORY = sharcldr.LoadedMemory.from_stream(
        stream, sharcldr.parse_blocks(stream)
    )


def _audit_trace_case(
    states: Sequence[Any],
    start: int,
    case: Mapping[str, Any],
    indirect_pcs: set[int],
    executable_pcs: set[int],
) -> tuple[dict[str, Any], list[list[int]]]:
    """Summarize one trace without returning states or decoded instructions."""
    edges: set[tuple[int, int]] = set()
    for state in states:
        for event in state.trace:
            pc_sw, target = event.get("pc_sw"), event.get("target_sw")
            if (
                pc_sw in indirect_pcs
                and isinstance(target, int)
                and target in executable_pcs
            ):
                edges.add((pc_sw, target))
    summary = trace.summarize(states, start)
    if "dispatch_site_pc_sw" in case:
        audit_site = _integer(case["dispatch_site_pc_sw"], "dispatch_site_pc_sw")
        audit_target = _integer(case["expected_target_sw"], "expected_target_sw")
        for state, terminal in zip(states, summary["states"], strict=False):
            terminal["dispatch_branch_audit"] = [
                {
                    key: event[key]
                    for key in ("action", "pc_sw", "target_sw", "form", "predicate")
                    if key in event
                }
                for event in state.trace
                if event.get("action") == "branch"
                and event.get("pc_sw") == audit_site
                and event.get("target_sw") == audit_target
            ]
    provenance = case.get("selector_provenance")
    if provenance is not None:
        copy_pc = _integer(provenance["copy_pc_sw"], "copy_pc_sw")
        table_load_pc = _integer(provenance["table_load_pc_sw"], "table_load_pc_sw")
        source, selector = (
            provenance["source_register"],
            provenance["selector_register"],
        )
        site = _integer(provenance["dispatch_site_pc_sw"], "dispatch_site_pc_sw")
        for state, terminal in zip(states, summary["states"], strict=False):
            events = state.trace
            copies = [
                {
                    key: event[key]
                    for key in (
                        "pc_sw",
                        "form",
                        "action",
                        "source",
                        "destination",
                        "predicate_assumption",
                    )
                    if key in event
                }
                for event in events
                if event.get("pc_sw") == copy_pc
                and event.get("action") == "ureg-copy"
                and event.get("source") == source
                and event.get("destination") == selector
            ]
            copy_positions = [
                n
                for n, event in enumerate(events)
                if event.get("pc_sw") == copy_pc
                and event.get("action") == "ureg-copy"
                and event.get("source") == source
                and event.get("destination") == selector
            ]
            copy_end = copy_positions[-1] + 1 if copy_positions else 0
            branches_at_site = [
                n
                for n, event in enumerate(events)
                if event.get("action") == "branch" and event.get("pc_sw") == site
            ]
            dispatch_start = branches_at_site[0] if branches_at_site else len(events)
            table_loads = [
                {
                    key: event[key]
                    for key in (
                        "pc_sw",
                        "form",
                        "action",
                        "space",
                        "ureg",
                        "address",
                        "expression",
                        "concrete_value",
                    )
                    if key in event
                }
                for event in events[copy_end:dispatch_start]
                if event.get("pc_sw") == table_load_pc
                and event.get("action") == "load"
                and event.get("ureg") == "I12"
            ]
            later_writes = [
                {
                    key: event[key]
                    for key in (
                        "pc_sw",
                        "form",
                        "action",
                        "source",
                        "destination",
                        "ureg",
                        "value",
                    )
                    if key in event
                }
                for event in events[copy_end:]
                if (
                    event.get("action") == "ureg-copy"
                    and event.get("destination") == selector
                )
                or (
                    event.get("action") == "ureg-write"
                    and event.get("ureg") == selector
                )
            ]
            branches = [
                {
                    key: event[key]
                    for key in ("pc_sw", "form", "action", "target_sw", "predicate")
                    if key in event
                }
                for event in events
                if event.get("action") == "branch" and event.get("pc_sw") == site
            ]
            terminal["selector_transfer_audit"] = {
                "copies": copies,
                "table_loads": table_loads,
                "later_selector_writes": later_writes,
                "dispatch_branches": branches,
            }
    return summary, [[pc, target] for pc, target in sorted(edges)]


def _run_trace_case(memory: Any, item: Mapping[str, Any]) -> dict[str, Any]:
    """Top-level plain-data work item runner shared by serial and spawn paths."""
    case = item["case"]
    states = trace.trace(
        memory,
        None,
        item["start_sw"],
        sets=case["sets"],
        max_steps=item["max_steps"],
        max_states=item["max_states"],
        concrete_memory=item["concrete_memory"],
        follow_loaded_calls=item["follow_loaded_calls"],
        continue_external_calls=item["continue_external_calls"],
        max_call_depth=item["max_call_depth"],
        assume_nw32=item["assume_nw32"],
        core_reset_state=item["core_reset_state"],
        breakpoints=tuple(case["breakpoints"]),
    )
    summary, edges = _audit_trace_case(
        states,
        item["start_sw"],
        case,
        set(item["indirect_pcs"]),
        set(item["executable_pcs"]),
    )
    return {
        "ordinal": item["ordinal"],
        "case": case,
        "summary": summary,
        "edges": edges,
    }


def _trace_case_worker(item: Mapping[str, Any]) -> dict[str, Any]:
    if _TRACE_WORKER_MEMORY is None:
        raise RuntimeError("trace worker memory was not initialized")
    return _run_trace_case(_TRACE_WORKER_MEMORY, item)


def _prepare_trace_work(
    probes: Sequence[Mapping[str, Any]],
    maximum_cases: int,
    indirect_pcs: set[int],
    executable_pcs: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Expand and validate every case before any worker process starts."""
    if any(not isinstance(probe, Mapping) for probe in probes):
        raise ValueError("each trace probe must be an object")
    used_cases = used_work = 0
    names: set[str] = set()
    plans, work = [], []
    for probe_ordinal, (index, probe) in enumerate(
        sorted(enumerate(probes), key=lambda item: str(item[1].get("name", "")))
    ):
        name = probe.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"trace_probes[{index}].name must be non-empty")
        if name in names:
            raise ValueError(f"trace_probes[{index}].name must be unique")
        names.add(name)
        start = _integer(probe.get("start_sw"), f"trace_probes[{index}].start_sw")
        if start < 0:
            raise ValueError(f"trace_probes[{index}].start_sw must be nonnegative")
        supplied = probe.get("_cases")
        cases = (
            _expand_probe(probe, maximum_cases - used_cases)
            if supplied is None
            else [dict(case) for case in supplied]
        )
        if supplied is not None and not isinstance(supplied, list):
            raise ValueError(f"trace_probes[{index}]._cases must be an array")
        used_cases += len(cases)
        if used_cases > maximum_cases:
            raise ValueError(f"trace probes exceed total case cap {maximum_cases}")
        max_steps = _positive(probe.get("max_steps", 200), "max_steps", TRACE_MAX_STEPS)
        max_states = _positive(
            probe.get("max_states", 32), "max_states", TRACE_MAX_STATES
        )
        used_work += len(cases) * max_steps * max_states
        if used_work > TRACE_WORK_CAP:
            raise ValueError(f"trace probes exceed work cap {TRACE_WORK_CAP}")
        plan = {
            "name": name,
            "start_sw": start,
            "evidence_class": "strict-trace",
            "assumptions": probe.get("assumptions", []),
            "ordinal": probe_ordinal,
        }
        plans.append(plan)
        for case_ordinal, raw_case in enumerate(cases):
            sets = raw_case.get("sets", raw_case)
            if not isinstance(sets, Mapping):
                raise ValueError(f"trace_probes[{index}] case sets must be an object")
            case = {
                **{
                    key: value
                    for key, value in raw_case.items()
                    if key not in ("sets", "breakpoints")
                },
                "sets": dict(sets),
                "breakpoints": [
                    _integer(value, "breakpoint")
                    for value in raw_case.get(
                        "breakpoints", probe.get("breakpoints", [])
                    )
                ],
            }
            work.append(
                {
                    "ordinal": [probe_ordinal, case_ordinal],
                    "case": case,
                    "start_sw": start,
                    "max_steps": max_steps,
                    "max_states": max_states,
                    "concrete_memory": bool(probe.get("concrete_memory", True)),
                    "follow_loaded_calls": bool(
                        probe.get("follow_loaded_calls", False)
                    ),
                    "continue_external_calls": bool(
                        probe.get("continue_external_calls", False)
                    ),
                    "max_call_depth": _positive(
                        probe.get("max_call_depth", 8), "max_call_depth", 32
                    ),
                    "assume_nw32": bool(probe.get("assume_nw32", False)),
                    "core_reset_state": bool(probe.get("core_reset_state", False)),
                    "indirect_pcs": sorted(indirect_pcs),
                    "executable_pcs": sorted(executable_pcs),
                }
            )
    return plans, work


def run_declared_probes(
    memory: Any,
    probes: Sequence[Mapping[str, Any]],
    maximum_cases: int,
    indirect_pcs: set[int],
    executable_pcs: set[int],
    *,
    blob_path: Path | None = None,
    jobs: int = 1,
) -> tuple[list[dict[str, Any]], dict[int, set[int]]]:
    plans, work = _prepare_trace_work(
        probes, maximum_cases, indirect_pcs, executable_pcs
    )
    if jobs <= 0:
        raise ValueError("jobs must be positive")
    if jobs == 1:
        completed = [_run_trace_case(memory, item) for item in work]
    else:
        if blob_path is None:
            raise ValueError("parallel trace probes require blob_path")
        # Explicit spawn makes this safe on macOS and prevents inherited live
        # loader/tracer objects or SQLite connections.
        with ProcessPoolExecutor(
            max_workers=jobs,
            mp_context=get_context("spawn"),
            initializer=_init_trace_worker,
            initargs=(str(blob_path),),
        ) as pool:
            futures = [pool.submit(_trace_case_worker, item) for item in work]
            completed = [future.result() for future in as_completed(futures)]
    completed.sort(key=lambda item: tuple(item["ordinal"]))
    by_ordinal = {tuple(item["ordinal"]): item for item in completed}
    resolved: dict[int, set[int]] = defaultdict(set)
    results = []
    for plan in plans:
        cases = []
        for case_ordinal in range(
            sum(1 for item in work if item["ordinal"][0] == plan["ordinal"])
        ):
            item = by_ordinal[(plan["ordinal"], case_ordinal)]
            for pc, target in item["edges"]:
                resolved[pc].add(target)
            cases.append(
                {
                    **{
                        key: value
                        for key, value in item["case"].items()
                        if key not in ("sets", "breakpoints")
                    },
                    "sets": item["case"]["sets"],
                    "breakpoints": item["case"]["breakpoints"],
                    "summary": item["summary"],
                }
            )
        results.append(
            {key: value for key, value in plan.items() if key != "ordinal"}
            | {"cases": cases}
        )
    return results, resolved


def read_ghidradump_evidence_ro(
    path: Path | None, roots: Sequence[Mapping[str, Any]], address_scale: int
) -> dict[str, Any] | None:
    if path is None:
        return None
    if not path.is_file():
        raise ValueError(f"dump database does not exist: {path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
        required = {"functions", "calls", "data_refs"}
        if not required <= tables:
            raise ValueError("dump database lacks functions/calls/data_refs tables")
        # Fixed schema names checked above; avoid dynamic SQL identifiers.
        counts = {
            "functions": connection.execute(
                "select count(*) from functions"
            ).fetchone()[0],
            "calls": connection.execute("select count(*) from calls").fetchone()[0],
            "data_refs": connection.execute(
                "select count(*) from data_refs"
            ).fetchone()[0],
        }
        root_rows = []
        for root in roots:
            entry_sw = _integer(root.get("entry_sw"), "root.entry_sw")
            entry = entry_sw * address_scale
            function = connection.execute(
                "select name,size from functions where entry=?", (entry,)
            ).fetchone()
            incoming = connection.execute(
                "select count(*) from calls where to_func=? or to_addr=?",
                (entry, entry),
            ).fetchone()[0]
            outgoing = connection.execute(
                "select count(*) from calls where from_func=?", (entry,)
            ).fetchone()[0]
            root_rows.append(
                {
                    "name": root.get("name"),
                    "entry_sw": entry_sw,
                    "function_name": function["name"] if function else None,
                    "function_size": function["size"] if function else None,
                    "incoming_direct_edges": incoming,
                    "outgoing_direct_edges": outgoing,
                }
            )
    finally:
        connection.close()
    manifest_path = path.with_name("manifest.json")
    dump_manifest = None
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text())
            dump_manifest = {
                key: loaded.get(key)
                for key in (
                    "tool",
                    "tool_version",
                    "language",
                    "complete",
                    "image_sha256",
                )
            }
        except (OSError, json.JSONDecodeError):
            dump_manifest = {"status": "unreadable"}
    return {
        "sha256": _sha256(path.read_bytes()),
        "address_scale_to_sw": address_scale,
        "evidence_class": "dump-advisory",
        "counts": counts,
        "manifest": dump_manifest,
        "roots": sorted(root_rows, key=lambda item: item["entry_sw"]),
    }


def _root_hypotheses(
    roots: Sequence[Mapping[str, Any]], by_id: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_entry = {item["entry"]: item for item in by_id.values()}
    result = []
    for index, root in enumerate(roots):
        if not isinstance(root, dict):
            raise ValueError(f"roots[{index}] must be an object")
        name = root.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"roots[{index}].name must be non-empty")
        entry = _integer(root.get("entry_sw"), f"roots[{index}].entry_sw")
        function = by_entry.get(entry)
        result.append(
            {
                "name": name,
                "entry_sw": entry,
                "role": root.get("role", "unspecified hypothesis"),
                "function_id": function["id"] if function else None,
                "evidence_class": "manifest-hypothesis",
                "semantic_status": "not-proven",
            }
        )
    return sorted(result, key=lambda item: (item["entry_sw"], item["name"]))


def build_natural_selector_frontiers(
    ctx: Mapping[str, Any],
    static: Mapping[str, Any],
    pointer_tables: Sequence[Mapping[str, Any]],
    declarations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize bounded, loader-backed natural-selector frontiers.

    Declarations carry only review conclusions which cannot be manufactured by
    the tracer (writer coverage and recursive register effects).  The loader
    cells, decoded tail and candidate table entries are checked here.
    """
    tables = {item["source_byte_address"]: item for item in pointer_tables}
    memory: sharcldr.LoadedMemory = ctx["mem"]
    result = []
    for number, declaration in enumerate(declarations):
        prefix = f"natural_selector_frontiers[{number}]"
        if not isinstance(declaration, Mapping):
            raise ValueError(f"{prefix} must be an object")
        name = declaration.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{prefix}.name must be non-empty")
        tail_pc = _integer(declaration.get("tail_jump_sw"), f"{prefix}.tail_jump_sw")
        tail = static["instructions"].get(tail_pc)
        expected_tail = declaration.get("tail_decode", {})
        if not isinstance(expected_tail, Mapping):
            raise ValueError(f"{prefix}.tail_decode must be an object")
        if tail is None or any(
            tail.get(key) != value for key, value in expected_tail.items()
        ):
            raise ValueError(f"{prefix} tail decode does not match loaded bytes")
        cells = declaration.get("runtime_cells")
        if not isinstance(cells, list) or not cells:
            raise ValueError(f"{prefix}.runtime_cells must be a non-empty array")
        loaded_cells = []
        for cell_number, cell in enumerate(cells):
            if not isinstance(cell, Mapping):
                raise ValueError(
                    f"{prefix}.runtime_cells[{cell_number}] must be an object"
                )
            address = _integer(
                cell.get("dm_byte_address"),
                f"{prefix}.runtime_cells[{cell_number}].dm_byte_address",
            )
            width = _positive(
                cell.get("width", 4),
                f"{prefix}.runtime_cells[{cell_number}].width",
                4096,
            )
            alias = canonical_loader_dm_address(address)
            raw = memory.read(alias, width)
            sources = [memory.source_block(alias + offset) for offset in range(width)]
            if raw is None or not all(isinstance(source, int) for source in sources):
                raise ValueError(f"{prefix} runtime cell is not loader-final")
            loaded_cells.append(
                {
                    "dm_byte_address": address,
                    "loader_byte_address": alias,
                    "width": width,
                    "initial_value": int.from_bytes(raw, "little"),
                    "raw_hex": raw.hex(),
                    "source_blocks": sorted(set(cast(list[int], sources))),
                    "evidence_class": "loaded-bytes",
                }
            )
        table_address = _integer(
            declaration.get("table_dm_byte_address"), f"{prefix}.table_dm_byte_address"
        )
        table_alias = canonical_loader_dm_address(table_address)
        table = tables.get(table_alias)
        if table is None:
            raise ValueError(f"{prefix} has no loader-final decoded pointer table")
        candidates = declaration.get("loaded_target_candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(
                f"{prefix}.loaded_target_candidates must be a non-empty array"
            )
        table_targets = [entry["target_sw"] for entry in table["entries"]]
        materialized_candidates = []
        for candidate_number, candidate in enumerate(candidates):
            if not isinstance(candidate, Mapping):
                raise ValueError(
                    f"{prefix}.loaded_target_candidates[{candidate_number}] must be an object"
                )
            entry_index = _integer(
                candidate.get("entry_index"),
                f"{prefix}.loaded_target_candidates[{candidate_number}].entry_index",
            )
            target = _integer(
                candidate.get("target_sw"),
                f"{prefix}.loaded_target_candidates[{candidate_number}].target_sw",
            )
            if (
                entry_index < 0
                or entry_index >= len(table_targets)
                or table_targets[entry_index] != target
            ):
                raise ValueError(
                    f"{prefix} candidate does not match loader pointer table"
                )
            callee_entry = _integer(
                candidate.get("callee_entry_sw"),
                f"{prefix}.loaded_target_candidates[{candidate_number}].callee_entry_sw",
            )
            wrapper = static["instructions"].get(target)
            wrapper_owner = wrapper.get("owner") if wrapper else None
            owner_function = ctx["by_id"].get(wrapper_owner) if wrapper_owner else None
            if owner_function is None:
                raise ValueError(f"{prefix} candidate has no decoded wrapper owner")
            owner_callees = [
                ctx["by_id"][callee]["entry"] for callee in owner_function["callees"]
            ]
            if callee_entry not in owner_callees:
                raise ValueError(
                    f"{prefix} candidate callee is not a direct wrapper callee"
                )
            materialized_candidates.append(
                {
                    "entry_index": entry_index,
                    "target_sw": target,
                    "wrapper_owner": wrapper_owner,
                    "wrapper_entry_sw": owner_function.get("entry", target),
                    "callee_entry_sw": callee_entry,
                    "r6_disposition": "unknown",
                    "r6_disposition_evidence_class": "unknown",
                    "evidence_classes": ["loaded-bytes", "static-decode"],
                }
            )
        if [item["target_sw"] for item in materialized_candidates] != table_targets:
            raise ValueError(
                f"{prefix} candidates must cover the declared loader table in order"
            )
        writer_coverage = declaration.get("writer_coverage", {})
        if not isinstance(writer_coverage, Mapping):
            raise ValueError(f"{prefix}.writer_coverage must be an object")
        r6_effect_audit = declaration.get("r6_effect_audit", {})
        if not isinstance(r6_effect_audit, Mapping):
            raise ValueError(f"{prefix}.r6_effect_audit must be an object")
        unresolved_reasons = declaration.get("unresolved_reasons", [])
        if not isinstance(unresolved_reasons, list) or not all(
            isinstance(item, str) for item in unresolved_reasons
        ):
            raise ValueError(f"{prefix}.unresolved_reasons must be an array of strings")
        result.append(
            {
                "name": name,
                "tail_jump": {
                    "pc_sw": tail_pc,
                    **tail,
                    "evidence_class": "loaded-bytes",
                },
                "runtime_cells": loaded_cells,
                "pointer_table": {
                    "dm_byte_address": table_address,
                    "loader_byte_address": table_alias,
                    "entry_count": table["entry_count"],
                    "source_blocks": table["source_blocks"],
                },
                "loaded_target_candidates": materialized_candidates,
                "range_guard_status": declaration.get("range_guard_status", "unproven"),
                "writer_coverage": dict(writer_coverage),
                "r6_effect_audit": {
                    "status": "unknown",
                    "reason": "generated register-effect query required",
                    "evidence_class": "unknown",
                },
                "exhaustiveness": declaration.get("exhaustiveness", "unproven"),
                "runtime_occurrence": "not-observed",
                "unresolved_reasons": unresolved_reasons,
                "minimal_runtime_capture": declaration.get(
                    "minimal_runtime_capture", {}
                ),
                "evidence_classes": [
                    "loaded-bytes",
                    "static-decode",
                    "manifest-hypothesis",
                ],
            }
        )
    return result


def _vector_control(insn: Any, pc_sw: int) -> dict[str, Any] | None:
    """Extract only documented direct/indirect control facts from fields."""
    fields = sharcinv.merge_fields(insn.fields)
    form = insn.type_name
    if form in ("8a_abs", "8a_rel"):
        target = (
            fields.get("addr")
            if form == "8a_abs"
            else sharcflow.pcrel_target(pc_sw, fields.get("reladdr", 0))
        )
        return {
            "kind": "direct-call" if fields.get("b") else "direct-jump",
            "target_sw": target,
            "conditional": fields.get("cond") != 31,
            "form": form,
            "pc_sw": pc_sw,
        }
    if form in ("25a_direct", "25a_pcrel"):
        target = (
            fields.get("addr")
            if form == "25a_direct"
            else sharcflow.pcrel_target(pc_sw, fields.get("reladdr", 0))
        )
        return {
            "kind": "direct-call",
            "target_sw": target,
            "conditional": False,
            "form": form,
            "pc_sw": pc_sw,
        }
    if form in ("9a_abs", "9b_abs"):
        if insn.raw == sharcflow.RETURN_JUMP:
            return {"kind": "return-only", "form": form, "pc_sw": pc_sw}
        return {"kind": "indirect-transfer", "form": form, "pc_sw": pc_sw}
    return None


def _function_for_pc(
    functions: Sequence[Mapping[str, Any]], pc_sw: int
) -> Mapping[str, Any] | None:
    matches = [
        function
        for function in functions
        if function.get("entry_sw", function.get("entry"))
        <= pc_sw
        < function.get("exit_sw", function.get("exit"))
    ]
    return (
        min(
            matches,
            key=lambda item: (
                item.get("exit_sw", item.get("exit"))
                - item.get("entry_sw", item.get("entry")),
                item.get("entry_sw", item.get("entry")),
            ),
        )
        if matches
        else None
    )


def _vector_reachability(
    functions: Sequence[Mapping[str, Any]],
    start_sw: int | None,
    semantic_targets: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if start_sw is None:
        return {"status": "unknown", "reason": "no direct final target"}
    by_id = {function["id"]: function for function in functions}
    start = _function_for_pc(functions, start_sw)
    if start is None:
        return {
            "status": "unknown",
            "reason": "target is outside recovered function inventory",
        }
    queue = [(start["id"], [start.get("entry_sw", start.get("entry"))])]
    seen = set()
    wanted = {item["entry_sw"]: item["name"] for item in semantic_targets}
    while queue:
        function_id, path = queue.pop(0)
        if function_id in seen:
            continue
        seen.add(function_id)
        if path[-1] in wanted:
            return {
                "status": "found",
                "target_name": wanted[path[-1]],
                "path_entries_sw": path,
            }
        function = by_id.get(function_id)
        if function is None:
            return {
                "status": "unknown",
                "reason": "missing direct-callee inventory record",
            }
        for callee_id in sorted(function.get("callees", ())):
            callee = by_id.get(callee_id)
            if callee is None:
                return {"status": "unknown", "reason": "missing direct callee"}
            queue.append(
                (callee_id, [*path, callee.get("entry_sw", callee.get("entry"))])
            )
    return {"status": "not-found-in-static-direct-graph"}


def _follow_vector_trampoline(
    memory: Any, first: Mapping[str, Any], region_lo: int, region_hi: int
) -> tuple[list[dict[str, Any]], int | None, str | None]:
    """Follow raw unconditional direct jumps only; no inferred control flow."""
    hops, current, seen = [], first, set()
    for _ in range(4):
        target = current.get("target_sw")
        if not isinstance(target, int):
            return hops, None, "no direct target"
        hops.append(
            {
                key: current[key]
                for key in ("pc_sw", "form", "kind", "target_sw", "conditional")
                if key in current
            }
        )
        if current["kind"] != "direct-jump":
            return hops, target, None
        if current.get("conditional"):
            return hops, None, "conditional trampoline edge"
        if target in seen:
            return hops, None, "trampoline cycle"
        seen.add(target)
        insn = decode_loaded_at(memory, target)
        if insn.kind == "unknown" or not insn.length_bytes:
            return hops, None, "trampoline decode gap"
        current = _vector_control(insn, target)
        if current is None:
            return hops, target, None
    return hops, None, "trampoline hop limit"


def scan_core_vector_candidates(
    memory: Any,
    blob: bytes,
    declarations: Sequence[Mapping[str, Any]],
    functions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Perform a candidate-layout scan without asserting an active IVT base."""
    blocks = {block["index"]: block for block in sharcldr.parse_blocks(blob)}
    scans = []
    for number, declaration in enumerate(declarations):
        if not isinstance(declaration, Mapping):
            raise ValueError(f"core_vector_scans[{number}] must be an object")
        layout, region = declaration.get("layout"), declaration.get("candidate_region")
        if not isinstance(layout, Mapping) or not isinstance(region, Mapping):
            raise ValueError(
                f"core_vector_scans[{number}] requires layout and candidate_region"
            )
        entry_count = layout.get("entry_count")
        entries = (
            _positive(entry_count, "core vector entry_count", 256)
            if entry_count is not None
            else None
        )
        per_entry = _positive(
            layout.get("instructions_per_entry"),
            "core vector instructions_per_entry",
            32,
        )
        citation = layout.get("instructions_per_entry_evidence")
        if layout.get("offset_unit") != "architectural-instruction" or not isinstance(
            citation, Mapping
        ):
            raise ValueError(
                "core vector layout requires architectural instruction units and public-manual citation"
            )
        block = blocks.get(
            _integer(region.get("loader_block"), "core vector loader_block")
        )
        start = _integer(region.get("start_pc_sw"), "core vector start_pc_sw")
        address, size = (
            _integer(
                region.get("loader_byte_address"), "core vector loader_byte_address"
            ),
            _positive(region.get("loaded_size"), "core vector loaded_size"),
        )
        valid_region = (
            block is not None
            and block.get("target_address") == address
            and block.get("payload_len") == size
        )
        start_source = memory.source_block(sharcldr.sw_to_byte(start))
        scan = {
            "name": declaration.get("name", f"core-vector-{number}"),
            "identity": region.get("identity", "unverified-core-ivt-candidate"),
            "layout": {
                "entry_count": entries,
                "instructions_per_entry": per_entry,
                "instructions_per_entry_evidence": dict(citation),
                "offset_unit": "architectural-instruction",
                "evidence_class": "public-manual-layout",
            },
            "candidate_region": {
                "loader_byte_address": address,
                "start_pc_sw": start,
                "loader_block": region.get("loader_block"),
                "loaded_size": size,
                "evidence_class": "loaded-bytes",
            },
            "vector_identity": "unverified",
            "core_semantics": "unknown",
            "sport_semantics": "unknown",
            "audio_semantics": "unknown",
            "semantic_reason": "candidate base and vector identities are unverified",
            "slots": [],
        }
        if (
            not valid_region
            or start_source != region.get("loader_block")
            or entries is None
        ):
            failure = (
                "candidate region does not match loader-final block provenance"
                if not valid_region
                else {
                    "instruction_ordinal": 0,
                    "pc_sw": start,
                    "reason": "declared start_pc_sw maps to loader block %r, not candidate block %r"
                    % (start_source, region.get("loader_block")),
                }
                if start_source != region.get("loader_block")
                else "entry_count and processor-specific vector mapping are unverified"
            )
            scan.update(
                {
                    "layout_match": False,
                    "failure": failure,
                    "coverage": {"decoded_instructions": 0, "slots": 0},
                }
            )
            scans.append(scan)
            continue
        pc, decoded = start, []
        failure = None
        for ordinal in range(entries * per_entry):
            insn = decode_loaded_at(memory, pc)
            if (
                insn.kind == "unknown"
                or not insn.length_bytes
                or sharcldr.sw_to_byte(pc) + insn.length_bytes > address + size
            ):
                failure = {
                    "instruction_ordinal": ordinal,
                    "pc_sw": pc,
                    "reason": insn.note or "decode gap",
                }
                break
            decoded.append((pc, insn))
            pc += insn.length_bytes // 2
        scan["layout_match"] = failure is None
        if failure is not None:
            scan["failure"] = failure
        targets = [
            {
                "name": item["name"],
                "entry_sw": _integer(item["entry_sw"], "semantic target entry_sw"),
            }
            for item in declaration.get("semantic_targets", [])
        ]
        for vector in range(entries):
            group = decoded[vector * per_entry : (vector + 1) * per_entry]
            if len(group) != per_entry:
                break
            controls = [_vector_control(insn, pc_sw) for pc_sw, insn in group]
            controls = [item for item in controls if item is not None]
            kinds = {item["kind"] for item in controls}
            classification = (
                "undecodable"
                if any(insn.kind == "unknown" for _, insn in group)
                else (next(iter(kinds)) if len(kinds) == 1 else "mixed")
                if kinds
                else "no-control-transfer"
            )
            first_direct = next(
                (
                    item
                    for item in controls
                    if item["kind"] in ("direct-jump", "direct-call")
                ),
                None,
            )
            hops, final_target, stop = (
                _follow_vector_trampoline(memory, first_direct, start, pc)
                if first_direct
                else ([], None, "no direct control transfer")
            )
            target_function = (
                _function_for_pc(functions, final_target)
                if final_target is not None
                else None
            )
            slot = {
                "vector_number_hypothesis": vector,
                "vector_identity": "unverified",
                "start_pc_sw": group[0][0],
                "end_pc_sw": group[-1][0] + group[-1][1].length_bytes // 2,
                "classification": classification,
                "controls": controls,
                "trampoline_hops": hops,
                "final_target_sw": final_target,
                "trampoline_stop": stop,
                "target_function": target_function["id"]
                if target_function is not None
                else None,
                "static_direct_reachability": _vector_reachability(
                    functions, final_target, targets
                ),
                "evidence_classes": [
                    "loaded-bytes",
                    "static-decode",
                    "manifest-candidate-hypothesis",
                ],
            }
            scan["slots"].append(slot)
        scan["coverage"] = {
            "decoded_instructions": len(decoded),
            "required_instructions": entries * per_entry,
            "slots": len(scan["slots"]),
            "direct_control_slots": sum(
                bool(slot["controls"]) for slot in scan["slots"]
            ),
        }
        scans.append(scan)
    return scans


def attach_generated_frontier_evidence(
    frontiers: Sequence[dict[str, Any]], generated: Mapping[str, Any]
) -> None:
    """Join generated, conservative facts into their declared frontier scope."""
    writers = list(generated.get("writer_targets", ()))
    effects = {
        item.get("entry_sw"): item for item in generated.get("register_effects", ())
    }
    for frontier in frontiers:
        relevant = []
        for cell in frontier.get("runtime_cells", ()):
            address, width = cell["dm_byte_address"], cell["width"]
            relevant.extend(
                item
                for item in writers
                if item.get("target") == address
                and item.get("target_width", item.get("width")) >= width
            )
        frontier["writer_coverage"] = {
            "status": "unknown"
            if not relevant
            or any(item.get("coverage") != "complete" for item in relevant)
            else "complete",
            "targets": relevant,
            "reason": "unresolved stores or absent generated query prevent exclusion"
            if not relevant
            or any(item.get("coverage") != "complete" for item in relevant)
            else None,
        }
        candidate_effects = []
        for candidate in frontier.get("loaded_target_candidates", ()):
            # The wrapper is the path entered from the table; a callee-only
            # result cannot establish its calling-convention effect.
            effect = effects.get(
                candidate.get("wrapper_entry_sw", candidate["callee_entry_sw"])
            )
            callee_effect = effects.get(candidate["callee_entry_sw"])
            if effect is None:
                candidate["r6_disposition"] = "unknown"
                candidate["r6_disposition_evidence_class"] = "unknown"
            else:
                candidate["r6_disposition"] = effect["status"]
                candidate["r6_disposition_evidence_class"] = (
                    "strict-trace" if effect["status"] != "unknown" else "unknown"
                )
                candidate_effects.append({"scope": "wrapper", **effect})
                if callee_effect is not None:
                    candidate_effects.append(
                        {"scope": "direct-callee", **callee_effect}
                    )
        frontier["r6_effect_audit"] = {
            "status": "preserved"
            if candidate_effects
            and all(item["status"] == "preserved" for item in candidate_effects)
            else "unknown",
            "effects": candidate_effects,
            "evidence_class": "strict-trace"
            if candidate_effects
            and all(item["status"] != "unknown" for item in candidate_effects)
            else "unknown",
        }


def resolve_r4_frontier_probes(
    declarations: Sequence[Mapping[str, Any]], frontiers: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Turn bounded manifest R4 hypotheses into ordinary strict trace cases.

    A breakpoint only checks the declared loader table target; it does not
    prove the guard, upstream bounds, table immutability, or occurrence.
    """
    by_name = {item["name"]: item for item in frontiers}
    plans = []
    for number, declaration in enumerate(declarations):
        if not isinstance(declaration, Mapping):
            raise ValueError(f"r4_tail_probes[{number}] must be an object")
        name = declaration.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"r4_tail_probes[{number}].name must be non-empty")
        values = declaration.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError(
                f"r4_tail_probes[{number}].values must be a non-empty array"
            )
        frontier = by_name.get(
            declaration.get("frontier", "natural-r6-before-orchestrator")
        )
        if frontier is None:
            raise ValueError(f"r4_tail_probes[{number}] has no matching frontier")
        targets = {
            item["entry_index"]: item["target_sw"]
            for item in frontier["loaded_target_candidates"]
        }
        register = _register_name(
            declaration.get("register", "R4"), f"r4_tail_probes[{number}].register"
        )
        if register != "R4":
            raise ValueError(f"r4_tail_probes[{number}].register must be R4")
        seeds = declaration.get("sets", {})
        if not isinstance(seeds, Mapping):
            raise ValueError(f"r4_tail_probes[{number}].sets must be an object")
        base_sets = {
            _register_name(key, f"r4_tail_probes[{number}].sets key"): _seed_value(
                seed, f"r4_tail_probes[{number}].sets.{key}"
            )
            for key, seed in seeds.items()
        }
        if "I12" in base_sets:
            raise ValueError(f"r4_tail_probes[{number}].sets must not seed I12")
        assumptions = declaration.get("assumptions", [])
        if not isinstance(assumptions, list) or not all(
            isinstance(item, str) for item in assumptions
        ):
            raise ValueError(
                f"r4_tail_probes[{number}].assumptions must be an array of strings"
            )
        cases = []
        for value in sorted(
            _integer(item, f"r4_tail_probes[{number}].values") for item in values
        ):
            if value not in targets:
                raise ValueError(
                    f"r4_tail_probes[{number}] value has no declared loaded target"
                )
            case_sets = dict(base_sets)
            # The value sweep intentionally overrides a declaration-level R4
            # seed; no case seeds I12 directly.
            case_sets[register] = value
            cases.append(
                {
                    "sets": dict(sorted(case_sets.items())),
                    "breakpoints": [targets[value]],
                    "r4_value": value,
                    "expected_target_sw": targets[value],
                }
            )
        plans.append(
            {
                "name": name,
                "start_sw": _integer(
                    declaration.get("start_sw"), f"r4_tail_probes[{number}].start_sw"
                ),
                "max_steps": _positive(
                    declaration.get("max_steps", 16),
                    "r4_tail_probes.max_steps",
                    TRACE_MAX_STEPS,
                ),
                "max_states": _positive(
                    declaration.get("max_states", 4),
                    "r4_tail_probes.max_states",
                    TRACE_MAX_STATES,
                ),
                "concrete_memory": bool(declaration.get("concrete_memory", True)),
                "assume_nw32": bool(declaration.get("assume_nw32", True)),
                "follow_loaded_calls": False,
                "continue_external_calls": False,
                "core_reset_state": False,
                "max_call_depth": 1,
                "assumptions": list(
                    dict.fromkeys(
                        [
                            *assumptions,
                            "R4 values are manifest hypotheses, not observed runtime values",
                            "M13 values are manifest hypotheses, not observed runtime values",
                            "one breakpoint per loader table target",
                        ]
                    )
                ),
                "cases": cases,
            }
        )
    return plans


def discover(
    blob_path: Path,
    manifest: Mapping[str, Any],
    dump_db: Path | None = None,
    *,
    index_path: Path | None = None,
    jobs: int = 1,
    profile: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    mark = profile or (lambda _phase: None)
    blob = blob_path.read_bytes()
    blob_sha = _sha256(blob)
    if blob_sha != manifest["image_sha256"]:
        raise ValueError(
            f"wrong SHARC image: expected {manifest['image_sha256']}, got {blob_sha}"
        )
    if jobs <= 0:
        raise ValueError("jobs must be positive")
    mark("input")
    index = None
    if index_path is not None:
        index = sharc_index.AnalysisIndex.open_or_build(
            blob_path,
            path=index_path,
            config=sharc_index.IndexConfig(
                tuple(manifest["code_blocks"]), manifest["min_depth"]
            ),
        )
    mark("index")
    snapshot = index.snapshot() if index is not None else None
    ctx = (
        _indexed_context(blob, snapshot, blob_sha)
        if snapshot is not None
        else sharcfn.load_context(
            str(blob_path), manifest["code_blocks"], manifest["min_depth"]
        )
    )
    if not isinstance(ctx["mem"], sharcldr.LoadedMemory):
        raise ValueError("sharcfn context did not provide loader-final memory")
    final_marker = ctx["mem"].has_final_marker()
    if not final_marker:
        raise ValueError("loader stream has no final marker")
    mark("context")
    indexed_functions = None
    if snapshot is not None:
        indexed_functions = snapshot["functions"]
        static = {
            "instructions": {
                item["pc_sw"]: {
                    key: value for key, value in item.items() if key != "pc_sw"
                }
                for item in snapshot["instructions"]
            },
            "literals": snapshot["literals"],
            "indirect_sites": snapshot["indirect_sites"],
        }
        pointer_tables, pointer_hits = (
            snapshot["pointer_runs"],
            snapshot["pointer_hits"],
        )
    else:
        static = build_static_context(ctx)
        pointer_tables, pointer_hits = find_pointer_runs(
            ctx, static["instructions"], manifest["code_blocks"]
        )
    mark("static")
    limits = manifest["limits"]
    pointer_table_count = len(pointer_tables)
    pointer_tables = pointer_tables[: limits["max_pointer_tables"]]
    for table in pointer_tables:
        entries = table["entries"]
        table["entries_truncated"] = (
            len(entries) > limits["max_pointer_entries_per_table"]
        )
        table["entries"] = entries[: limits["max_pointer_entries_per_table"]]
    dispatches = join_dispatch_candidates(
        static["indirect_sites"],
        static["literals"],
        pointer_tables,
        limits["dispatch_window_sw"],
    )
    natural_selector_frontiers = build_natural_selector_frontiers(
        ctx, static, pointer_tables, manifest["natural_selector_frontiers"]
    )
    mark("joins")
    writer_targets = [
        sharc_index.WriterTarget(
            _integer(item.get("address"), "writer_targets.address"),
            _positive(item.get("width", 4), "writer_targets.width"),
        )
        for item in manifest.get("writer_targets", [])
        if isinstance(item, Mapping)
    ]
    register_effects = [
        _register_effect_query(item, f"register_effects[{number}]")
        for number, item in enumerate(manifest.get("register_effects", []))
        if isinstance(item, Mapping)
    ]
    # Query both table-entered wrappers and their declared direct callees.
    # A callee-only result is never promoted to a wrapper disposition.
    known_effects = {(item.entry_sw, item.register) for item in register_effects}
    for frontier in natural_selector_frontiers:
        for candidate in frontier["loaded_target_candidates"]:
            key = (candidate["wrapper_entry_sw"], "R6")
            if key not in known_effects:
                register_effects.append(sharc_index.RegisterEffectQuery(*key))
                known_effects.add(key)
    generated_queries = (
        index.query(
            writer_targets=writer_targets, register_effects=register_effects, jobs=jobs
        )
        if index
        else {
            "writer_targets": [
                {
                    "target": item.address,
                    "width": item.width,
                    "status": "unknown",
                    "reason": "no index requested",
                }
                for item in writer_targets
            ],
            "register_effects": [
                {
                    "entry_sw": item.entry_sw,
                    "register": item.register,
                    "status": "unknown",
                    "reasons": ["no index requested"],
                }
                for item in register_effects
            ],
        }
    )
    mark("generated-queries")
    attach_generated_frontier_evidence(natural_selector_frontiers, generated_queries)
    vector_scans = scan_core_vector_candidates(
        ctx["mem"],
        blob,
        manifest.get("core_vector_scans", []),
        indexed_functions
        if indexed_functions is not None
        else _compact_functions(ctx["functions"]),
    )
    ivt_scan = {
        "status": "unknown",
        "candidate_scans": vector_scans,
        "reason": "candidate scan does not establish the active IVT base or vector identities",
    }
    derived_plans = resolve_dispatch_target_probes(
        manifest["dispatch_target_probes"], dispatches, pointer_tables
    )
    provenance_plans = resolve_selector_provenance_probes(
        manifest["selector_provenance_probes"], dispatches, pointer_tables
    )
    r4_plans = resolve_r4_frontier_probes(
        manifest.get("r4_tail_probes", []), natural_selector_frontiers
    )
    derived_trace_probes = [
        {
            "name": plan["name"],
            "start_sw": plan["start_sw"],
            "max_steps": plan["max_steps"],
            "max_states": plan["max_states"],
            "concrete_memory": plan["concrete_memory"],
            "assume_nw32": plan["assume_nw32"],
            "follow_loaded_calls": plan["follow_loaded_calls"],
            "continue_external_calls": plan["continue_external_calls"],
            "core_reset_state": plan["core_reset_state"],
            "max_call_depth": plan["max_call_depth"],
            "assumptions": plan["assumptions"],
            "_cases": plan["cases"],
        }
        for plan in [*derived_plans, *provenance_plans, *r4_plans]
    ]
    indirect_pcs = {item["pc_sw"] for item in static["indirect_sites"]}
    probes, resolved = run_declared_probes(
        ctx["mem"],
        [*manifest["trace_probes"], *derived_trace_probes],
        limits["max_trace_cases"],
        indirect_pcs,
        set(static["instructions"]),
        blob_path=blob_path,
        jobs=jobs,
    )
    mark("trace-probes")
    plan_by_name = {plan["name"]: plan for plan in derived_plans}
    provenance_by_name = {plan["name"]: plan for plan in provenance_plans}
    r4_by_name = {plan["name"]: plan for plan in r4_plans}
    dispatch_target_contexts = []
    selector_provenance = []
    for probe in probes:
        plan = plan_by_name.get(probe["name"])
        if plan is None:
            continue
        dispatch = plan["dispatch"]
        for case in probe["cases"]:
            outcome, terminals, resolved_state = classify_dispatch_target_summary(
                case["summary"], case["expected_target_sw"], dispatch["site_pc_sw"]
            )
            dispatch_target_contexts.append(
                {
                    "probe_name": probe["name"],
                    "site_pc_sw": dispatch["site_pc_sw"],
                    "table_byte_address": dispatch["table_byte_address"],
                    "entry_index": case["entry_index"],
                    "selector_register": plan["selector_register"],
                    "selector_value": case["selector_value"],
                    "expected_target_sw": case["expected_target_sw"],
                    "target_owner": case["target_owner"],
                    "outcome": outcome,
                    "trace_stop_facts": terminals,
                    "registers": resolved_state.get("registers")
                    if resolved_state
                    else None,
                    "branch_evidence": resolved_state.get("dispatch_branch_audit", [])
                    if resolved_state
                    else [],
                    "evidence_classes": ["strict-trace", "manifest-hypothesis"],
                    "assumptions": plan["assumptions"],
                }
            )
    for probe in probes:
        plan = provenance_by_name.get(probe["name"])
        if plan is None:
            continue
        declaration = plan["declaration"]
        for case in probe["cases"]:
            terminals = case["summary"]["states"]
            trace_outcome, path_quantifier, natural_runtime_observed = (
                classify_selector_provenance_summary(
                    terminals, case["expected_target_sw"]
                )
            )
            static_pcs = [
                ("copy", _integer(declaration["copy_pc_sw"], "copy_pc_sw")),
                (
                    "table-load",
                    _integer(declaration["table_load_pc_sw"], "table_load_pc_sw"),
                ),
                ("dispatch", _integer(declaration["site_pc_sw"], "site_pc_sw")),
            ]
            static_chain = []
            for role, pc_sw in static_pcs:
                instruction = static["instructions"].get(pc_sw)
                if instruction is None:
                    raise ValueError(
                        f"selector provenance {plan['name']} static {role} PC is not decoded"
                    )
                static_chain.append(
                    {
                        "role": role,
                        "pc_sw": pc_sw,
                        "form": instruction["form"],
                        "raw_hex": instruction["raw_hex"],
                        "evidence_class": "loaded-bytes",
                    }
                )
            selector_provenance.append(
                {
                    "probe_name": plan["name"],
                    "seed_value": case["seed_value"],
                    "source_register": declaration["source_register"],
                    "selector_register": declaration["selector_register"],
                    "expected_target_sw": case["expected_target_sw"],
                    "trace_outcome": trace_outcome,
                    "path_quantifier": path_quantifier,
                    "selector_origin": "manifest-seed",
                    "seed_taint": {
                        "registers": sorted(case["sets"]),
                        "memory": [
                            f"loader-final DM table 0x{_integer(declaration['table_byte_address'], 'table_byte_address'):08x}"
                        ],
                    },
                    "runtime_occurrence": "not-observed",
                    "natural_runtime_observed": natural_runtime_observed,
                    "static_chain": static_chain,
                    "trace_terminal_states": terminals,
                    "natural_frontier": declaration.get("natural_frontier", {}),
                    "assumptions": plan["assumptions"],
                    "evidence_classes": [
                        "loaded-bytes",
                        "strict-trace",
                        "manifest-hypothesis",
                    ],
                }
            )
    dispatch_target_contexts.sort(
        key=lambda item: (
            item["site_pc_sw"],
            item["table_byte_address"],
            item["entry_index"],
            item["probe_name"],
        )
    )
    selector_provenance.sort(key=lambda item: (item["probe_name"], item["seed_value"]))
    dispatch_target_outcomes = Counter(
        item["outcome"] for item in dispatch_target_contexts
    )
    for site in static["indirect_sites"]:
        site["strict_trace_targets"] = sorted(resolved.get(site["pc_sw"], ()))
    roots = _root_hypotheses(manifest["roots"], ctx["by_id"])
    dump = read_ghidradump_evidence_ro(
        dump_db,
        manifest["roots"],
        _positive(manifest.get("dump_address_scale", 2), "dump_address_scale"),
    )
    stop_reasons = Counter()
    trace_states = 0
    for probe in probes:
        for case in probe["cases"]:
            states = case["summary"]["states"]
            trace_states += len(states)
            stop_reasons.update(state["stopped"] or "completed" for state in states)
    label_counts = Counter(function["label"] for function in ctx["functions"])
    report = {
        "schema": SCHEMA,
        "provenance": {
            "blob": {
                "sha256": blob_sha,
                "size": len(blob),
                "final_marker": final_marker,
            },
            "manifest": {
                "sha256": _sha256(_canonical_bytes(manifest)),
                "schema": manifest["schema"],
            },
            "tools": {
                path.name: _sha256(path.read_bytes())
                for path in sorted(
                    (
                        _HERE / "sharc_discover.py",
                        _HERE / "sharc_static.py",
                        _HERE / "sharc_trace.py",
                        _HERE / "sharcinv.py",
                        *(_HERE / "sharcfn").rglob("*.py"),
                    )
                )
            },
            "dump": dump,
            "index": {
                "used": index is not None,
                "fingerprint": index._metadata["blob"]["sha256"] if index else None,
            },
        },
        "limits": limits,
        "evidence_classes": list(EVIDENCE_CLASSES),
        "semantic_status": "discovery-only",
        "functions": indexed_functions
        if indexed_functions is not None
        else _compact_functions(ctx["functions"]),
        "function_summary": {
            "count": len(ctx["functions"]),
            "label_counts": dict(sorted(label_counts.items())),
            "no_static_caller_count": sum(
                function["has_no_static_caller"] for function in ctx["functions"]
            ),
            "warning": "no-static-caller is a structural signal, not dead-code evidence",
        },
        "engine_queue": sharcfn.build_engine_queue(
            ctx,
            str(_HERE.parent.parent / "docs" / "findings" / "functions"),
            None,
            (
                snapshot["engine_target_opcode_coverage"]
                if snapshot is not None
                else None
            ),
        ),
        "root_hypotheses": roots,
        "indirect_sites": static["indirect_sites"],
        "pointer_tables": pointer_tables,
        "dispatch_candidates": dispatches,
        "dispatch_target_contexts": dispatch_target_contexts,
        "selector_provenance": selector_provenance,
        "natural_selector_frontiers": natural_selector_frontiers,
        "writer_investigation": generated_queries["writer_targets"],
        "register_effects": generated_queries["register_effects"],
        "r4_tail_investigation": {
            "status": "unknown",
            "declarations": manifest.get("r4_tail_probes", []),
            "strict_trace_probes": [
                probe for probe in probes if probe["name"] in r4_by_name
            ],
            "reason": "tail range guard, upstream R4 bounds, and runtime table mutation remain unproven",
        },
        "ivt_audio_root_scan": ivt_scan,
        "probes": probes,
        "coverage": {
            "decoded_instruction_pcs": len(static["instructions"]),
            "functions": len(ctx["functions"]),
            "indirect_sites": len(static["indirect_sites"]),
            "strict_trace_resolved_indirect_sites": len(resolved),
            "dispatch_target_contexts": len(dispatch_target_contexts),
            "selector_provenance_contexts": len(selector_provenance),
            "natural_selector_frontiers": len(natural_selector_frontiers),
            "dispatch_target_context_outcomes": dict(
                sorted(dispatch_target_outcomes.items())
            ),
            "pointer_hits": pointer_hits,
            "pointer_table_runs": pointer_table_count,
            "pointer_table_runs_emitted": len(pointer_tables),
            "trace_states": trace_states,
            "stop_reasons": dict(sorted(stop_reasons.items())),
        },
        "hook_candidates": dispatches,
        "ranking_policy": {
            "kind": "lexicographic structural facts",
            "rank_tuple": [
                "has pointer-table join (0 first)",
                "negative pointer-run length",
                "literal-to-transfer short-word distance",
                "transfer PC",
            ],
            "warning": "rank is structural and does not establish audio semantics or hook safety",
        },
    }
    mark("report")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blob", type=Path, help="section-7 loader stream")
    parser.add_argument("manifest", type=Path, help="checked-in discovery manifest")
    parser.add_argument("--dump-db", type=Path, help="existing ghidradump xrefs.sqlite")
    parser.add_argument("--index", type=Path, help="persistent static index cache")
    parser.add_argument(
        "--jobs", type=int, default=1, help="bounded trace worker count"
    )
    parser.add_argument(
        "--profile", action="store_true", help="write phase timings to stderr"
    )
    parser.add_argument("-o", "--output", type=Path, help="write canonical JSON here")
    args = parser.parse_args(argv)
    profiler = _PhaseProfile() if args.profile else None
    try:
        manifest = load_manifest(args.manifest)
        if profiler is not None:
            profiler.mark("manifest")
        report = discover(
            args.blob,
            manifest,
            args.dump_db,
            index_path=args.index,
            jobs=args.jobs,
            profile=profiler.mark if profiler is not None else None,
        )
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(str(error))
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
        print(
            f"wrote {args.output}: {report['coverage']['functions']} functions, "
            f"{report['coverage']['indirect_sites']} indirect sites, "
            f"{report['coverage']['pointer_table_runs']} pointer runs"
        )
    else:
        print(encoded, end="")
    if profiler is not None:
        profiler.mark("serialize")
        profiler.write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
