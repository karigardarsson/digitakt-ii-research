"""Small, conservative delay-aware tracer for SHARC+ main-program code.

This intentionally follows exact short-word PCs, rather than discovering
functions or linearly sweeping unrelated bytes.  It is a first semantic slice:
unhandled forms stop a state instead of pretending to understand them.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
from tools.sharc_dev.sharc_core.compute import (  # noqa: E402, F401
    MR_DATAMOVE_REGISTERS,
    _apply_compute,
    _apply_compute_pey,
    _apply_compute_simd,
    _compute,
    _compute_pey,
    _compute_pey_values,
    _compute_simd,
    _field_deposit_or,
    _mr_data_move,
    _shift_immediate,
)

# The SHARC+ semantics live in tools/sharc_dev/sharc_core/. Every name is re-exported
# here so existing `sharc_trace.X` callers keep working.
from tools.sharc_dev.sharc_core.encoding import (  # noqa: E402, F401
    AC_BIT,
    ACCESS_WIDTHS,
    AF_BIT,
    AI_BIT,
    ALU_FLAGS_MASK,
    ALUSAT_BIT,
    AN_BIT,
    AS_BIT,
    AV_BIT,
    AZ_BIT,
    BTF_BIT,
    CORE_MMR_RESET_VALUES,
    CORE_UREG_RESET_VALUES,
    L1_BLOCK3_NW_BASE,
    L1_BLOCK3_NW_LIMIT,
    L1_BLOCK3_SW_BASE,
    MI_BIT,
    MN_BIT,
    MU_BIT,
    MULT_FLAGS_MASK,
    MV_BIT,
    SF_BIT,
    SHIFT_FLAGS_MASK,
    SIMPLE_COND_BITS,
    SS_BIT,
    SV_BIT,
    SZ_BIT,
    TRUNCATE_BIT,
    UREG_CODES,
    UREG_NAMES,
    _field,
    _wide,
)
from tools.sharc_dev.sharc_core.flags import (  # noqa: E402, F401
    MULT_FLAGS_CLEAR,
    MULT_FLAGS_FIXED,
    MULT_FLAGS_FORGET,
    MULT_FLAGS_SAT,
    FlagUpdate,
    _alu_arith_updates,
    _alu_result_bits,
    _arith_flag_bits,
    _arith_flag_bits_ci,
    _astatx_abs,
    _astatx_alu_arith,
    _astatx_alu_arith_ci,
    _astatx_alu_logical,
    _astatx_bit_field,
    _astatx_bit_test,
    _astatx_btst,
    _astatx_compare,
    _astatx_compare_float,
    _astatx_define,
    _astatx_fext,
    _astatx_forget,
    _astatx_lefto,
    _astatx_leftz,
    _astatx_shift,
    _bits_to_updates,
    _compare_flags,
    _compare_flags_float,
    _float_alu_updates,
    _or_updates,
)
from tools.sharc_dev.sharc_core.floats import (  # noqa: E402, F401
    _FLOAT_ALL_ONES,
    _approx_recips,
    _fixed_to_float,
    _fixed_to_float_scaled,
    _float32,
    _float32_bits,
    _float_binary,
    _float_clip,
    _float_copysign,
    _float_logb,
    _float_mantissa,
    _float_max,
    _float_min,
    _float_round32,
    _float_scalb,
    _float_to_fixed,
    _float_to_fixed_trunc,
    _float_unary,
    _scale_fixed_input,
)
from tools.sharc_dev.sharc_core.forms import (  # noqa: E402, F401
    _execute,
)
from tools.sharc_dev.sharc_core.memory import (  # noqa: E402, F401
    _SIMD_COMPANION_WIDTHS,
    _access_modifier_scale,
    _canonical_dm_address,
    _circular_wrap_const,
    _concrete_address,
    _dm_read,
    _dm_write,
    _dossier,
    _load_normal_ureg,
    _read_px48,
    _simd_ureg_mem_companion,
    dm_write_range,
)
from tools.sharc_dev.sharc_core.sequencer import (  # noqa: E402, F401
    _advance,
    _check_return_target,
    _immediate_transfer,
    _lt_ge_le_gt,
    _lt_ge_le_gt_pe,
    _predicate,
    _predicate_and,
    _predicate_pe,
    _predicate_simd_branch,
    _return_transfer,
    _start_counted_loop,
    _transfer,
    decode_at,
)
from tools.sharc_dev.sharc_core.state import (  # noqa: E402, F401
    _CUREG_PAIRS,
    AFTER_DELAY_SLOTS,
    MR,
    Loop,
    Pending,
    State,
    _copy,
    _cureg_code,
    _event,
    _json_value,
    _mr_from_signed,
    _mr_read_word,
    _mr_write_word,
    _render,
    _simd_active,
    _stop,
    _sync_pc_stack,
    _sync_status_stack,
    _ureg,
    _ureg_raw,
)
from tools.sharc_dev.sharc_core.values import (  # noqa: E402, F401
    _BOUNDED_SYMBOL_RE,
    _SYMBOL_RE,
    CIRC_SYMBOL_PREFIX,
    Affine,
    Const,
    PartialConst,
    Unknown,
    Value,
    _aconv,
    _aconv_symbol,
    _add,
    _affine,
    _astatx_known_bit,
    _bitwise,
    _multiply,
    _multiply_fractional,
    _negate,
    _not,
    _signed,
    _signed32,
    _stack_bounded_symbol,
    _subtract,
    _terms,
    symbol,
)
from tools.sharc_dev.sharc_disasm import Instruction  # noqa: E402, F401  (re-exported)
from tools.sharc_dev.sharcimm import name_address  # noqa: E402
from tools.sharc_dev.sharcldr import (  # noqa: E402
    LoadedMemory,  # noqa: E402
    sw_to_byte,  # noqa: E402, F401  (re-exported)
)


def _seed_value(value: int | Value | str) -> Value:
    if isinstance(value, (Const, Affine, Unknown)):
        return value
    if isinstance(value, int):
        return Const(value)
    if isinstance(value, str) and value.startswith("@"):
        return symbol(value[1:])
    raise ValueError("seed value must be an integer, Value, or @symbol")


def _seed_code(key: str | int) -> int:
    if isinstance(key, str):
        try:
            return UREG_CODES[key.upper()]
        except KeyError as error:
            raise ValueError("unknown UREG: " + key) from error
    if not 0 <= key < len(UREG_NAMES):
        raise ValueError("UREG code out of range: %d" % key)
    return key


def _dedupe_key(state: State) -> tuple:
    """Everything that decides a state's future; history (trace, steps) and the
    run-wide settings shared by every state are left out."""
    return (
        state.pc_sw,
        state.pending,
        tuple(state.call_stack),
        tuple(state.loops),
        tuple(sorted(state.uregs.items())),
        tuple(sorted(state.special.items())),
        tuple(sorted(state.overlay.items())),
        tuple(sorted(state.mmrs.items())),
        tuple(state.status_stack),
        state.data_memory_tainted,
        state.at_loaded_entry,
    )


def trace(
    data: bytes | LoadedMemory,
    base_sw: int | None,
    start: int,
    sets: Mapping[str | int, int | Value | str] | None = None,
    max_steps: int = 100,
    max_states: int = 32,
    *,
    concrete_memory: bool = False,
    follow_loaded_calls: bool = False,
    continue_external_calls: bool = False,
    dossier_bytes: int = 0,
    max_call_depth: int = 8,
    skip_provisional_entries: bool = False,
    assume_nw32: bool = False,
    core_reset_state: bool = False,
    breakpoints: Sequence[int] = (),
    provisional_forms: Sequence[str] = (),
    pokes: Mapping[int, int] | None = None,
    approx_recips: bool = False,
) -> list[State]:
    uregs: dict[int, Value] = (
        {
            UREG_CODES[name]: Const(value)
            for name, value in CORE_UREG_RESET_VALUES.items()
        }
        if core_reset_state
        else {}
    )
    for key, value in (sets or {}).items():
        uregs[_seed_code(key)] = _seed_value(value)
    if concrete_memory and not isinstance(data, LoadedMemory):
        raise ValueError("concrete memory requires LoadedMemory")
    if pokes and not concrete_memory:
        raise ValueError("--poke-dm requires --concrete-memory")
    if any(not 0 <= address <= 0xFFFFFFFF for address in (pokes or {})):
        raise ValueError("--poke-dm address must be a 32-bit address")
    if not 0 <= max_steps <= 100_000:
        raise ValueError("max_steps must be between 0 and 100000")
    if not 1 <= max_states <= 1_024:
        raise ValueError("max_states must be between 1 and 1024")
    if dossier_bytes < 0 or dossier_bytes > 256:
        raise ValueError("dossier_bytes must be between 0 and 256")
    if max_call_depth < 1 or max_call_depth > 32:
        raise ValueError("max_call_depth must be between 1 and 32")
    if any(not isinstance(pc, int) or not 0 <= pc <= 0xFFFFFF for pc in breakpoints):
        raise ValueError("breakpoints must be 24-bit short-word addresses")
    breakpoint_set = frozenset(breakpoints)
    concrete = data if isinstance(data, LoadedMemory) and concrete_memory else None
    mmrs: dict[int, Value] = (
        {address: Const(value) for address, value in CORE_MMR_RESET_VALUES.items()}
        if core_reset_state
        else {}
    )
    start_state = State(
        start,
        uregs,
        concrete=concrete,
        base_sw=base_sw,
        follow_loaded_calls=follow_loaded_calls,
        continue_external_calls=continue_external_calls,
        dossier_bytes=dossier_bytes,
        max_call_depth=max_call_depth,
        skip_provisional_entries=skip_provisional_entries,
        at_loaded_entry=skip_provisional_entries,
        assume_nw32=assume_nw32,
        core_reset_state=core_reset_state,
        mmrs=mmrs,
        provisional_forms=tuple(provisional_forms),
        approx_recips=approx_recips,
    )
    # Seed the per-path write overlay before the first instruction executes,
    # through the same _dm_write() a real store instruction uses, so a poked
    # word is canonicalized (loader alias, MMR, width gating) exactly like a
    # concrete write the trace itself would perform.
    for address, value in sorted((pokes or {}).items()):
        if not _dm_write(start_state, address, 4, Const(value & 0xFFFFFFFF)):
            raise ValueError(
                "--poke-dm at %#x did not take effect (add "
                "--assume-32bit-normal-words, or use an address in "
                "0x30000000-0x40000000)" % address
            )
    # FIFO of distinct live states. Paths that reconverge on an identical state
    # behave identically from there, so only one is kept.
    active: dict[tuple, State] = {_dedupe_key(start_state): start_state}
    done: list[State] = []
    while active:
        state = active.pop(next(iter(active)))
        if state.pc_sw in breakpoint_set:
            done.append(
                _stop(state, decode_at(data, base_sw, state.pc_sw), "breakpoint")
            )
            continue
        if state.steps >= max_steps:
            done.append(_stop(state, None, "max-steps"))
            continue
        out = _execute(state, decode_at(data, base_sw, state.pc_sw))
        for child in out:
            if child.stopped:
                done.append(child)
                continue
            dedupe_key = _dedupe_key(child)
            existing = active.get(dedupe_key)
            if existing is not None:
                # Keep the copy that has used less of --max-steps.
                if child.steps < existing.steps:
                    active[dedupe_key] = child
            elif len(active) + len(done) >= max_states:
                done.append(_stop(child, None, "max-states"))
            else:
                active[dedupe_key] = child
    return done


def _register_snapshot(state: State) -> dict[str, Any]:
    return {
        UREG_NAMES[code]: _json_value(value)
        for code, value in sorted(state.uregs.items())
    }


def _watched_dm_snapshot(state: State, addresses: Sequence[int]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for address in addresses:
        value = _dm_read(state, address, 4)
        snapshot[f"{address:#x}"] = (
            _json_value(value) if value is not None else {"unavailable": True}
        )
    return snapshot


def summarize(
    states: Sequence[State], start_sw: int, watch_dm: Sequence[int] = ()
) -> dict:
    """Return a bounded machine-readable runtime-probe summary."""
    summaries = []
    for state in states:
        peripheral_accesses = []
        loop_setups = []
        for event in state.trace:
            if event.get("action") == "loop-setup":
                loop_setups.append(
                    {
                        key: event[key]
                        for key in (
                            "pc_sw",
                            "start_sw",
                            "end_sw",
                            "count",
                            "mode",
                        )
                    }
                )
            if event.get("action") not in ("load", "store"):
                continue
            address = event.get("address")
            if not isinstance(address, int):
                continue
            peripheral = name_address(address)
            if peripheral is None:
                continue
            access = {
                "pc_sw": event["pc_sw"],
                "action": event["action"],
                "address": address,
                "peripheral": peripheral,
            }
            for key in ("value", "concrete_value", "access_width"):
                if key in event:
                    access[key] = event[key]
            peripheral_accesses.append(access)
        stop_event = state.trace[-1] if state.trace else {}
        summary = {
            "stopped": state.stopped,
            "stop_pc_sw": stop_event.get("pc_sw", state.pc_sw),
            "stop_form": stop_event.get("form"),
            "steps": state.steps,
            "events": len(state.trace),
            "loaded_calls": sum(
                event.get("action") == "loaded-call-enter" for event in state.trace
            ),
            "opaque_calls": sum(
                event.get("action") == "opaque-external-call" for event in state.trace
            ),
            "loop_setups": loop_setups,
            "peripheral_accesses": peripheral_accesses,
            "last_events": state.trace[-5:],
        }
        if state.stopped == "breakpoint":
            summary["registers"] = _register_snapshot(state)
            summary["watched_dm"] = _watched_dm_snapshot(state, watch_dm)
        if state.provisional_used:
            summary["provisional_forms_used"] = list(state.provisional_used)
        if state.approx_recips_used:
            summary["approx_recips_used"] = True
        summaries.append(summary)
    return {"start_sw": start_sw, "states": summaries}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source")
    p.add_argument("--blob", action="store_true")
    p.add_argument("--base-sw", type=lambda x: int(x, 0))
    p.add_argument("--start", required=True, type=lambda x: int(x, 0))
    p.add_argument("--set", dest="sets", action="append", default=[])
    p.add_argument("--max-steps", type=int, default=100)
    p.add_argument("--max-states", type=int, default=32)
    p.add_argument(
        "--break-pc",
        action="append",
        default=[],
        type=lambda x: int(x, 0),
        help="stop before executing this short-word PC (repeatable)",
    )
    p.add_argument(
        "--watch-dm",
        action="append",
        default=[],
        type=lambda x: int(x, 0),
        help="include this 32-bit DM value in breakpoint snapshots (repeatable)",
    )
    p.add_argument(
        "--concrete-memory",
        action="store_true",
        help="read loader-backed DM bytes and keep a per-path write overlay",
    )
    p.add_argument(
        "--poke-dm",
        dest="pokes",
        action="append",
        default=[],
        metavar="ADDR=VALUE",
        help="seed a 32-bit DM word before tracing starts (repeatable; "
        "requires --concrete-memory)",
    )
    p.add_argument(
        "--poke-dm-file",
        metavar="PATH",
        help='JSON {"addr": value} (or {"addr": [v0, v1, ...]} for '
        "consecutive 32-bit words) to seed before tracing starts "
        "(requires --concrete-memory)",
    )
    p.add_argument("--follow-loaded-calls", action="store_true")
    p.add_argument(
        "--continue-external-calls",
        action="store_true",
        help="record dossier, clobber result registers, then continue",
    )
    p.add_argument("--dossier-bytes", type=int, default=0)
    p.add_argument("--max-call-depth", type=int, default=8)
    p.add_argument(
        "--skip-provisional-entries",
        action="store_true",
        help=(
            "legacy artifact-replay option; currently no-op because the former provisional "
            "Type19 entry is now documented"
        ),
    )
    p.add_argument(
        "--assume-32bit-normal-words",
        action="store_true",
        help="opt in to four-byte internal normal-word DM accesses (runtime IMDWx is otherwise unknown)",
    )
    p.add_argument(
        "--core-reset-state",
        action="store_true",
        help="seed only documented core-register and core-MMR reset values",
    )
    p.add_argument(
        "--allow-provisional-form",
        action="append",
        default=[],
        metavar="NAME",
        help="execute this form (e.g. 14d) although the table marks it "
        "unconfirmed; any run that uses one is calibration, not qualification",
    )
    p.add_argument(
        "--approx-recips",
        action="store_true",
        help="opt in to a documented-formula, undocumented-ROM approximation "
        "of recips's seed (PRM p.19-16/19-17); every value it produces is "
        "tagged with an 'approximate-recips' event and is calibration, not "
        "qualification",
    )
    output = p.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument(
        "--summary",
        action="store_true",
        help="print compact stop, loop and named-peripheral details",
    )
    p.add_argument(
        "--trace-json",
        metavar="PATH",
        help="also write the full JSON trace to PATH",
    )
    a = p.parse_args(argv)
    values = {}
    for item in a.sets:
        try:
            name, value = item.split("=", 1)
            values[name] = value if value.startswith("@") else int(value, 0)
            _seed_code(name)
            _seed_value(values[name])
        except ValueError:
            p.error("--set must be NAME=VALUE or NAME=@symbol")
    poke_values: dict[int, int] = {}
    if a.poke_dm_file:
        try:
            with open(a.poke_dm_file) as fh:
                poke_raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as error:
            p.error("cannot read --poke-dm-file: %s" % error)
        if not isinstance(poke_raw, dict):
            p.error("--poke-dm-file must contain a JSON object")
        for key, value in poke_raw.items():
            try:
                address = int(key, 0)
            except (TypeError, ValueError):
                p.error("--poke-dm-file keys must be integers: %r" % (key,))
            if isinstance(value, list):
                for offset, word in enumerate(value):
                    poke_values[address + 4 * offset] = word
            else:
                poke_values[address] = value
    for item in a.pokes:
        try:
            addr_text, value_text = item.split("=", 1)
            poke_values[int(addr_text, 0)] = int(value_text, 0)
        except ValueError:
            p.error("--poke-dm must be ADDR=VALUE")
    if poke_values and not a.concrete_memory:
        p.error("--poke-dm requires --concrete-memory")
    if any(not 0 <= address <= 0xFFFFFFFF for address in poke_values):
        p.error("--poke-dm address must be a 32-bit address")
    if a.concrete_memory and not a.blob:
        p.error("--concrete-memory requires --blob")
    if (a.follow_loaded_calls or a.continue_external_calls) and not a.concrete_memory:
        p.error("call following/continuation requires --concrete-memory")
    if a.skip_provisional_entries and not a.follow_loaded_calls:
        p.error("--skip-provisional-entries requires --follow-loaded-calls")
    if a.assume_32bit_normal_words and not a.concrete_memory:
        p.error("--assume-32bit-normal-words requires --concrete-memory")
    if not 0 <= a.max_steps <= 100_000:
        p.error("--max-steps must be between 0 and 100000")
    if not 1 <= a.max_states <= 1_024:
        p.error("--max-states must be between 1 and 1024")
    if not 0 <= a.dossier_bytes <= 256:
        p.error("--dossier-bytes must be between 0 and 256")
    if not 1 <= a.max_call_depth <= 32:
        p.error("--max-call-depth must be between 1 and 32")
    if any(not 0 <= pc <= 0xFFFFFF for pc in a.break_pc):
        p.error("--break-pc must be a 24-bit short-word address")
    if any(not 0 <= address <= 0xFFFFFFFF for address in a.watch_dm):
        p.error("--watch-dm must be a 32-bit address")
    if a.blob and a.base_sw is not None:
        p.error("--base-sw is ambiguous with --blob")
    if not a.blob and a.base_sw is None:
        p.error("--base-sw is required unless --blob is used")
    try:
        with open(a.source, "rb") as fh:
            source = fh.read()
    except OSError as error:
        p.error(str(error))
    if a.blob:
        try:
            source = LoadedMemory.from_stream(source)
        except (TypeError, ValueError) as error:
            p.error("invalid loader stream: " + str(error))
        if not source.ranges():
            p.error("loader stream has no loaded ranges")
        if not source.blocks or "FINAL" not in source.blocks[-1].get("flags", ()):
            p.error("loader stream ended before a final marker")
    try:
        states = trace(
            source,
            a.base_sw,
            a.start,
            values,
            a.max_steps,
            a.max_states,
            concrete_memory=a.concrete_memory,
            follow_loaded_calls=a.follow_loaded_calls,
            continue_external_calls=a.continue_external_calls,
            dossier_bytes=a.dossier_bytes,
            max_call_depth=a.max_call_depth,
            skip_provisional_entries=a.skip_provisional_entries,
            assume_nw32=a.assume_32bit_normal_words,
            core_reset_state=a.core_reset_state,
            breakpoints=a.break_pc,
            provisional_forms=tuple(a.allow_provisional_form),
            pokes=poke_values,
            approx_recips=a.approx_recips,
        )
    except ValueError as error:
        p.error(str(error))
    result: list[dict[str, Any]] = [
        {
            "stopped": s.stopped,
            "steps": s.steps,
            "assumptions": (
                (["32-bit internal normal words"] if s.assume_nw32 else [])
                + (["documented core/MMR reset values"] if s.core_reset_state else [])
                + (["approximate RECIPS seed"] if s.approx_recips_used else [])
            ),
            "trace": s.trace,
            "registers": _register_snapshot(s),
            "watched_dm": _watched_dm_snapshot(s, a.watch_dm),
            **(
                {"provisional_forms_used": list(s.provisional_used)}
                if s.provisional_used
                else {}
            ),
        }
        for s in states
    ]
    if a.trace_json:
        try:
            with open(a.trace_json, "w") as fh:
                json.dump(result, fh, indent=2)
                fh.write("\n")
        except OSError as error:
            p.error("cannot write trace JSON: " + str(error))
    if a.summary:
        print(json.dumps(summarize(states, a.start, a.watch_dm), separators=(",", ":")))
    elif a.json:
        print(json.dumps(result, indent=2))
    else:
        for state in result:
            for event in state["trace"]:
                print(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
