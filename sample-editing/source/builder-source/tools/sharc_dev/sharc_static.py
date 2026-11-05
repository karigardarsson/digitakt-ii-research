"""Static snapshot producers shared by discovery and AnalysisIndex."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from tools.sharc_dev import sharcfn, sharcinv, sharcldr, sharcwriters


def _owner_indexes(
    functions: Sequence[Mapping[str, Any]],
) -> dict[int, list[Mapping[str, Any]]]:
    result: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for function in functions:
        result[function["block"]].append(function)
    for items in result.values():
        items.sort(key=lambda item: (item["entry"], item["exit"], item["id"]))
    return result


def _owner_of(
    owners: Mapping[int, Sequence[Mapping[str, Any]]], block: int, pc_sw: int
) -> str | None:
    matches = [
        item for item in owners.get(block, ()) if item["entry"] <= pc_sw < item["exit"]
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: (item["exit"] - item["entry"], item["entry"]))[
        "id"
    ]


def build_static_context(ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable instruction, literal and indirect-transfer indexes."""
    owners = _owner_indexes(ctx["functions"])
    instructions: dict[int, dict[str, Any]] = {}
    literals = []
    indirect_sites = []
    for block, analyzed in sorted(ctx["analyzed"].items()):
        base_sw = analyzed["base_sw"]
        for offset, instruction in analyzed["insns"]:
            pc_sw = base_sw + offset // 2
            owner = _owner_of(owners, block, pc_sw)
            instructions[pc_sw] = {
                "block": block,
                "owner": owner,
                "form": instruction.type_name,
                "raw_hex": f"{instruction.raw:0{instruction.length_bytes * 2}x}",
            }
            fields = sharcinv.merge_fields(instruction.fields)
            literal_form = sharcinv.LITERAL_FORMS.get(instruction.type_name)
            if literal_form is not None:
                field, bits, _ = literal_form
                value = fields.get(field)
                if value is not None:
                    literals.append(
                        {
                            "pc_sw": pc_sw,
                            "block": block,
                            "owner": owner,
                            "form": instruction.type_name,
                            "value": value & ((1 << bits) - 1),
                        }
                    )
            if instruction.type_name not in ("9a_abs", "9b_abs"):
                continue
            pmi = fields.get("pmi")
            pmm = fields.get("pmm")
            if pmi is None or pmm is None:
                continue
            known_return = (
                fields.get("b", 0) == 0
                and fields.get("cond") == 0x1F
                and pmi == 4
                and pmm == 6
                and fields.get("j") == 1
            )
            indirect_sites.append(
                {
                    "pc_sw": pc_sw,
                    "block": block,
                    "owner": owner,
                    "form": instruction.type_name,
                    "kind": "return"
                    if known_return
                    else ("call" if fields.get("b", 0) else "jump"),
                    "index_register": f"I{8 + pmi}",
                    "modifier_register": f"M{8 + pmm}",
                    "condition": fields.get("cond"),
                    "delayed": bool(fields.get("j")),
                    "raw": instruction.raw,
                    "evidence_class": "static-decode",
                }
            )
    return {
        "instructions": instructions,
        "literals": sorted(literals, key=lambda item: (item["pc_sw"], item["value"])),
        "indirect_sites": sorted(indirect_sites, key=lambda item: item["pc_sw"]),
    }


def find_pointer_runs(
    ctx: Mapping[str, Any],
    instructions: Mapping[int, Mapping[str, Any]],
    code_blocks: Sequence[int],
    *,
    minimum_run: int = 2,
) -> tuple[list[dict[str, Any]], int]:
    """Find final-memory normal-word runs that point at decoded PCs.

    Addresses are enumerated only from non-fill payload blocks, then read via
    LoadedMemory so later overlapping blocks and fills retain last-write-wins
    semantics.
    """
    candidates: set[int] = set()
    for block in ctx["blocks"]:
        if block["fill"] or not block["payload_len"] or block["index"] in code_blocks:
            continue
        start = block["target_address"]
        end = start + block["payload_len"]
        first = start + (-start % 4)
        candidates.update(range(first, end - 3, 4))
    hits = []
    memory: sharcldr.LoadedMemory = ctx["mem"]
    blocks_by_index = {block["index"]: block for block in ctx["blocks"]}
    for address in sorted(candidates):
        # A normal word can straddle later overlapping writes.  Retain every
        # byte's final source rather than attributing the slot to its first
        # byte, and reject a word if any final byte comes from a FILL block.
        source_byte_blocks: list[int] = []
        for offset in range(4):
            source = memory.source_block(address + offset)
            if not isinstance(source, int) or source not in blocks_by_index:
                break
            source_byte_blocks.append(source)
        if len(source_byte_blocks) != 4 or any(
            blocks_by_index[source]["fill"] for source in source_byte_blocks
        ):
            continue
        raw = memory.read(address, 4)
        if raw is None:
            continue
        target = int.from_bytes(raw, "little")
        decoded = instructions.get(target)
        if decoded is None:
            continue
        hits.append(
            {
                "source_byte_address": address,
                "source_byte_blocks": source_byte_blocks,
                "source_blocks": sorted(set(source_byte_blocks)),
                "target_sw": target,
                "target_block": decoded["block"],
                "target_owner": decoded["owner"],
            }
        )
    runs = []
    current = []
    for hit in hits:
        if (
            current
            and hit["source_byte_address"] != current[-1]["source_byte_address"] + 4
        ):
            if len(current) >= minimum_run:
                runs.append(current)
            current = []
        current.append(hit)
    if len(current) >= minimum_run:
        runs.append(current)
    result = []
    for run in runs:
        result.append(
            {
                "source_byte_address": run[0]["source_byte_address"],
                "source_blocks": sorted(
                    {source for item in run for source in item["source_byte_blocks"]}
                ),
                "entry_count": len(run),
                "entries": run,
                "target_owners": sorted(
                    {item["target_owner"] for item in run if item["target_owner"]}
                ),
                "evidence_class": "loaded-bytes",
                "semantic_status": "not-proven",
            }
        )
    result.sort(key=lambda item: (-item["entry_count"], item["source_byte_address"]))
    return result, len(hits)


def build_snapshot(
    blob_path: str, code_blocks: Sequence[int], min_depth: int
) -> Mapping[str, Any]:
    """Build the immutable snapshot payload from loader-final semantic facts."""
    ctx = sharcfn.load_context(blob_path, code_blocks, min_depth)
    if not ctx["mem"].has_final_marker():
        raise ValueError("loader stream has no final marker")
    static = build_static_context(ctx)
    tables, pointer_hits = find_pointer_runs(ctx, static["instructions"], code_blocks)
    census, orphan = sharcwriters.full_project_census(ctx)
    memory_sites = [
        {"function_id": fn_id, **row}
        for fn_id, rows in census.items()
        for row in rows
        if row["is_dm"]
    ]
    memory_sites.extend({"function_id": None, **row} for row in orphan if row["is_dm"])
    inventory = sorted(ctx["functions"], key=lambda fn: (fn["block"], fn["entry"]))
    engine_candidates = sharcfn.engine_candidates(inventory)
    return {
        "functions": _compact_functions(ctx["functions"]),
        "function_inventory": inventory,
        "engine_target_opcode_coverage": sharcfn.target_opcode_coverage(
            ctx, engine_candidates
        ),
        "instructions": [
            {"pc_sw": pc, **record}
            for pc, record in sorted(static["instructions"].items())
        ],
        "instruction_pcs": sorted(static["instructions"]),
        "literals": static["literals"],
        "indirect_sites": static["indirect_sites"],
        "pointer_runs": tables,
        "pointer_hits": pointer_hits,
        "memory_sites": sorted(
            memory_sites, key=lambda item: (item["pc"], item.get("function_id") or "")
        ),
    }


def _compact_functions(functions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for function in functions:
        vector = function["vector"]
        result.append(
            {
                "id": function["id"],
                "block": function["block"],
                "entry_sw": function["entry"],
                "exit_sw": function["exit"],
                "n_insns": function["n_insns"],
                "label": function["label"],
                "confidence": function["confidence"],
                "callers": function["callers"],
                "callees": function["callees"],
                "unresolved_callees": function["unresolved_callees"],
                "has_no_static_caller": function["has_no_static_caller"],
                "features": {
                    key: vector[key]
                    for key in (
                        "compute_total",
                        "float_alu",
                        "float_mul",
                        "mac",
                        "mem_load",
                        "mem_store",
                        "calls",
                        "indirect_calls",
                        "named_tables_touched",
                    )
                },
            }
        )
    return sorted(result, key=lambda item: (item["block"], item["entry_sw"]))
