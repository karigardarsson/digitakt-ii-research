#!/usr/bin/env python3
"""Verify the DT2 1.16 ColdFire-frame reader in the SHARC image.

This is a bounded, byte-backed interface probe.  It verifies the static
instruction chain and runs three explicitly calibrated symbolic slices.  The
slices are discovery evidence, not strict startup qualification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tools.sharc_dev import sharc_trace as trace
from tools.sharc_dev.sharcldr import LoadedMemory

DT2_116_BLOB_SHA256 = "0f514a12a2255f5c081e292c47f1f29462003177658da4bbae0a22fd737fffa2"
MACHINE_OFFSET = 0x94
TRACKS = 16
TX_FRAME_BYTES = 0x802
SPORT_CALLER_PROBES = (
    (0x1C78E9, 0x1C78F6, 0x2618D0, 0x261960),
    (0x1C7978, 0x1C798B, 0x261918, 0x261964),
    (0x1C7A54, 0x1C7A5F, 0x261970, 0x261A00),
    (0x1C7B00, 0x1C7B12, 0x2619B8, 0x261A04),
)
DMA_DESCRIPTOR_PROBES = (
    {
        "start": 0x1C792F,
        "call_pc": 0x1C7971,
        "base": 0x2620C8,
        "candidate_object": 0x2618D0,
        "candidate_slot": 0x261960,
        "seeds": {},
        "descriptors": (
            (0x2620E4, 0x261CC8, 0x00100000, 64, 4, 0, 0),
            (0x2620C8, 0x261DC8, 0x00100000, 64, 4, 0, 0),
        ),
    },
    {
        "start": 0x1C79C4,
        "call_pc": 0x1C79F8,
        "base": 0x262100,
        "candidate_object": 0x261918,
        "candidate_slot": 0x261964,
        "seeds": {
            "R6": 64,
            "R7": 4,
            "R10": 4,
            "R11": 0,
            "R14": 0x00100000,
            "R15": 64,
            "I5": 0x00100000,
        },
        "descriptors": (
            (0x26211C, 0x261EC8, 0x00100000, 64, 4, 0, 0),
            (0x262100, 0x261FC8, 0x00100000, 64, 4, 0, 0),
        ),
    },
    {
        "start": 0x1C7AB7,
        "call_pc": 0x1C7AF9,
        "base": 0x264138,
        "candidate_object": 0x261970,
        "candidate_slot": 0x261A00,
        "seeds": {},
        "descriptors": (
            (0x264154, 0x262138, 0x00100000, 512, 4, 0, 0),
            (0x264138, 0x262938, 0x00100000, 512, 4, 0, 0),
        ),
    },
    {
        "start": 0x1C7B6A,
        "call_pc": 0x1C7B9E,
        "base": 0x264170,
        "candidate_object": 0x2619B8,
        "candidate_slot": 0x261A04,
        "seeds": {
            "R6": 0x00100000,
            "R7": 512,
            "R10": 4,
            "R11": 0,
            "R14": 512,
            "R15": 4,
            "I5": 0x00100000,
        },
        "descriptors": (
            (0x26418C, 0x263138, 0x00100000, 512, 4, 0, 0),
            (0x264170, 0x263938, 0x00100000, 512, 4, 0, 0),
        ),
    },
)


CHECKS: tuple[tuple[int, str, Mapping[str, int], str], ...] = (
    (0x1C0F3C, "17b", {"ureg[6:0]": 39, "data[15:0]": 0xFFFF}, "M7=-1"),
    (0x1C0F40, "17b", {"ureg[6:0]": 38, "data[15:0]": 1}, "M6=1"),
    (0x1C0F42, "17b", {"ureg[6:0]": 45, "data[15:0]": 0}, "M13=0"),
    (0x1C0F44, "17b", {"ureg[6:0]": 37, "data[15:0]": 0}, "M5=0"),
    (
        0x1C7C0F,
        "17a",
        {"ureg[6:0]": 12, "data[31:16]": 0x26, "data[15:0]": 0x1A10},
        "candidate state 2 base in R12",
    ),
    (0x1C7C12, "17b", {"ureg[6:0]": 4, "data[15:0]": 2}, "selector 2"),
    (
        0x1C7C14,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0x9FD5},
        "construct candidate state 2",
    ),
    (
        0x1C7DA9,
        "17a",
        {"ureg[6:0]": 12, "data[31:16]": 0x26, "data[15:0]": 0x1B18},
        "candidate state 1 base in R12",
    ),
    (
        0x1C7DAC,
        "5b_move",
        {
            "srcureghigh[4:0]": 9,
            "srcureglow[1:1]": 1,
            "srcureglow[0:0]": 0,
            "dstureg[6:0]": 4,
        },
        "copy fixed M6=1 to selector R4",
    ),
    (
        0x1C7DAE,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0x9FD5},
        "construct candidate state 1",
    ),
    (
        0x1C9FED,
        "4a",
        {"compute[22:16]": 2, "compute[15:0]": 0x1EC0},
        "copy caller R12 base to R14",
    ),
    (
        0x1C9FF3,
        "5b_move",
        {
            "srcureghigh[4:0]": 3,
            "srcureglow[1:1]": 1,
            "srcureglow[0:0]": 0,
            "dstureg[6:0]": 19,
        },
        "copy R14 base to I3",
    ),
    (
        0x1CA03D,
        "19a",
        {"is[2:0]": 3, "idis[2:0]": 7, "data[15:0]": 0x94},
        "form candidate buffer I4=I3+0x94",
    ),
    (
        0x1CA040,
        "5b_move",
        {
            "srcureghigh[4:0]": 5,
            "srcureglow[1:1]": 0,
            "srcureglow[0:0]": 0,
            "dstureg[6:0]": 4,
        },
        "copy candidate buffer I4 to R4",
    ),
    (
        0x1CA042,
        "15b",
        {"i[2:0]": 3, "d": 1, "ureg[6:0]": 4, "data[6:0]": 8},
        "store candidate buffer at state+0x20",
    ),
    (0x1CA044, "17b", {"ureg[6:0]": 28, "data[15:0]": 32}, "I12=32"),
    (
        0x1CA046,
        "15b",
        {"i[2:0]": 3, "d": 1, "ureg[6:0]": 28, "data[6:0]": 44},
        "store 32 at state+0xb0",
    ),
    (
        0x1C76E5,
        "19a_scaled",
        {
            "w": 1,
            "g": 0,
            "is[2:0]": 6,
            "idis[2:0]": 3,
            "data[31:16]": 0xFFFF,
            "data[15:0]": 0xFFF2,
        },
        "form reader local I5=I6-14 normal words",
    ),
    (
        0x1C7719,
        "5b_move",
        {
            "srcureghigh[4:0]": 5,
            "srcureglow[1:1]": 0,
            "srcureglow[0:0]": 1,
            "dstureg[6:0]": 8,
        },
        "caller copies I5 to argument R8",
    ),
    (
        0x1C771E,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0x2B24},
        "caller invokes frame processor",
    ),
    (
        0x1C78E9,
        "17a",
        {"ureg[6:0]": 2, "data[31:16]": 0x26, "data[15:0]": 0x1960},
        "first SPORT caller materializes destination slot",
    ),
    (
        0x1C78EC,
        "3c",
        {"dmi[2:0]": 7, "dmm[2:0]": 7, "d": 1, "dreg[3:0]": 2},
        "first SPORT caller pushes destination slot",
    ),
    (
        0x1C78EE,
        "17a",
        {"ureg[6:0]": 2, "data[31:16]": 0x26, "data[15:0]": 0x18D0},
        "first SPORT caller materializes object pointer",
    ),
    (
        0x1C78F1,
        "3c",
        {"dmi[2:0]": 7, "dmm[2:0]": 7, "d": 1, "dreg[3:0]": 2},
        "first SPORT caller pushes object pointer",
    ),
    (
        0x1C78F2,
        "16b",
        {"i[2:0]": 7, "m[2:0]": 7, "g": 0, "data[15:0]": 2},
        "first SPORT caller pushes selector",
    ),
    (
        0x1C78F6,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0xA58A},
        "compiler CJUMP into SPORT setup",
    ),
    (
        0x1C78F9,
        "3c",
        {"dmi[2:0]": 7, "dmm[2:0]": 7, "d": 1, "dreg[3:0]": 2},
        "CJUMP delay slot saves prior I6 from R2",
    ),
    (
        0x1C78FA,
        "16a",
        {
            "i[2:0]": 7,
            "m[2:0]": 7,
            "g": 0,
            "sl": 0,
            "by": 0,
            "data[31:16]": 0x1C,
            "data[15:0]": 0x78FC,
        },
        "CJUMP delay slot saves return-address-minus-one",
    ),
    (
        0x1C7938,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 0,
            "ureg[6:0]": 20,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x20C8,
        },
        "first descriptor-list head points to its second descriptor",
    ),
    (
        0x1C795F,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 1,
            "ureg[6:0]": 14,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x20D0,
        },
        "first descriptor long-word store writes CFG/XCNT",
    ),
    (
        0x1C796E,
        "17a",
        {"ureg[6:0]": 8, "data[31:16]": 0x26, "data[15:0]": 0x20C8},
        "pass first descriptor-list head in R8",
    ),
    (
        0x1C7971,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0xA7E4},
        "submit first descriptor list",
    ),
    (
        0x1C79CD,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 0,
            "ureg[6:0]": 20,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x2100,
        },
        "second descriptor-list head points to its second descriptor",
    ),
    (
        0x1C79DD,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 1,
            "ureg[6:0]": 10,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x2110,
        },
        "second descriptor long-word store writes XMOD/YCNT",
    ),
    (
        0x1C79F5,
        "17a",
        {"ureg[6:0]": 8, "data[31:16]": 0x26, "data[15:0]": 0x2100},
        "pass second descriptor-list head in R8",
    ),
    (
        0x1C79F8,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0xA7E4},
        "submit second descriptor list",
    ),
    (
        0x1C7AE6,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 0,
            "ureg[6:0]": 20,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x4138,
        },
        "third descriptor-list head points to its second descriptor",
    ),
    (
        0x1C7AC8,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 1,
            "ureg[6:0]": 6,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x4140,
        },
        "third descriptor long-word store writes CFG/XCNT",
    ),
    (
        0x1C7AF6,
        "17a",
        {"ureg[6:0]": 8, "data[31:16]": 0x26, "data[15:0]": 0x4138},
        "pass third descriptor-list head in R8",
    ),
    (
        0x1C7AF9,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0xA7E4},
        "submit third descriptor list",
    ),
    (
        0x1C7B88,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 0,
            "ureg[6:0]": 20,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x4170,
        },
        "fourth descriptor-list head points to its second descriptor",
    ),
    (
        0x1C7B6D,
        "14a",
        {
            "g": 0,
            "d": 1,
            "l": 1,
            "ureg[6:0]": 6,
            "addr[31:16]": 0x26,
            "addr[15:0]": 0x4178,
        },
        "fourth descriptor long-word store writes CFG/XCNT",
    ),
    (
        0x1C7B9B,
        "17a",
        {"ureg[6:0]": 8, "data[31:16]": 0x26, "data[15:0]": 0x4170},
        "pass fourth descriptor-list head in R8",
    ),
    (
        0x1C7B9E,
        "25a_direct",
        {"addr[23:16]": 0x1C, "addr[15:0]": 0xA7E4},
        "submit fourth descriptor list",
    ),
    (
        0x1C7524,
        "14a",
        {
            "g": 0,
            "d": 0,
            "l": 0,
            "ureg[6:0]": 2,
            "addr[31:16]": 0x25,
            "addr[15:0]": 0xF780,
        },
        "load the ping-pong selector before the buffer-maintenance callback",
    ),
    (
        0x1C7578,
        "14a",
        {
            "g": 0,
            "d": 0,
            "l": 0,
            "ureg[6:0]": 1,
            "addr[31:16]": 0x25,
            "addr[15:0]": 0xF780,
        },
        "reload the ping-pong selector before the marker store",
    ),
    (
        0x1C757B,
        "6b_shiftimm",
        {"cond[4:0]": 31, "shiftimm[22:16]": 0, "shiftimm[15:0]": 0x0B21},
        "scale the selector by 0x800 bytes into R2",
    ),
    (
        0x1C7580,
        "17a",
        {"ureg[6:0]": 28, "data[31:16]": 0x7FFF, "data[15:0]": 0xFFFF},
        "load the exact 0x7fffffff marker candidate into I12",
    ),
    (
        0x1C7583,
        "19a",
        {
            "g": 0,
            "idis[2:0]": 0,
            "is[2:0]": 4,
            "data[31:16]": 0x26,
            "data[15:0]": 0x2138,
        },
        "add the first large DMA-buffer base to the selector offset",
    ),
    (
        0x1C7586,
        "3b",
        {"u": 0, "i[2:0]": 4, "m[2:0]": 5, "d": 1, "l": 0, "ureg[6:0]": 28},
        "store I12 at the selected large DMA-buffer head",
    ),
    (
        0x1CA5C9,
        "15b",
        {"i[2:0]": 6, "d": 0, "ureg[6:0]": 21, "data[6:0]": 4},
        "load SPORT setup I5 from frame+0x10",
    ),
    (
        0x1CA5D1,
        "5a_move",
        {
            "srcureghigh[4:0]": 4,
            "srcureglow[1:1]": 1,
            "srcureglow[0:0]": 1,
            "dstureg[6:0]": 9,
        },
        "copy incoming I3 to SPORT setup R9",
    ),
    (
        0x1CA6A6,
        "15b",
        {"i[2:0]": 2, "d": 0, "ureg[6:0]": 18, "data[6:0]": 5},
        "load first software-object link into I2",
    ),
    (
        0x1CA6C3,
        "15b",
        {"i[2:0]": 4, "d": 0, "ureg[6:0]": 0, "data[6:0]": 5},
        "load DMA base from selected record+0x14",
    ),
    (
        0x1CA6C9,
        "15b",
        {"i[2:0]": 2, "d": 0, "ureg[6:0]": 20, "data[6:0]": 5},
        "load first linked destination pointer into I4",
    ),
    (
        0x1CA6CE,
        "3c",
        {"dmi[2:0]": 4, "dmm[2:0]": 5, "d": 1, "dreg[3:0]": 1},
        "store SPORT base R1 through first linked object",
    ),
    (
        0x1CA6D1,
        "15b",
        {"i[2:0]": 4, "d": 0, "ureg[6:0]": 20, "data[6:0]": 5},
        "load second linked destination pointer into I4",
    ),
    (
        0x1CA6D6,
        "3c",
        {"dmi[2:0]": 4, "dmm[2:0]": 5, "d": 1, "dreg[3:0]": 0},
        "store DMA base R0 through linked I4/M5 destination",
    ),
    (
        0x1CA6F9,
        "6a_mem",
        {
            "i[2:0]": 5,
            "m[2:0]": 5,
            "cond[4:0]": 31,
            "g": 0,
            "d": 1,
            "dreg[3:0]": 9,
        },
        "store incoming I3 value R9 through frame-supplied I5/M5",
    ),
    (
        0x1C2CC1,
        "19a",
        {"is[2:0]": 4, "data[15:0]": 0x75C},
        "sibling frame offset 0x75c",
    ),
    (0x1C2CCB, "19a", {"is[2:0]": 1, "data[15:0]": 0x94}, "I1 += machine offset 0x94"),
    (
        0x1C2CD4,
        "5a_move",
        {
            "srcureghigh[4:0]": 4,
            "srcureglow[1:1]": 0,
            "srcureglow[0:0]": 1,
            "dstureg[6:0]": 26,
        },
        "preserve I1+0x94 in I10",
    ),
    (
        0x1C2CD7,
        "19a",
        {"is[2:0]": 4, "data[15:0]": 0x73C},
        "sibling frame offset 0x73c",
    ),
    (
        0x1C2CA6,
        "5a_move",
        {
            "srcureghigh[4:0]": 9,
            "srcureglow[1:1]": 1,
            "srcureglow[0:0]": 1,
            "dstureg[6:0]": 11,
        },
        "copy fixed M7=-1 shift amount to R11",
    ),
    (
        0x1C2CFF,
        "5a_move",
        {"compute[22:16]": 0x20, "compute[15:0]": 0x05FB},
        "R5 = LSHIFT R15 by R11",
    ),
    (
        0x1C2D02,
        "5b_move",
        {
            "srcureghigh[4:0]": 1,
            "srcureglow[1:1]": 0,
            "srcureglow[0:0]": 1,
            "dstureg[6:0]": 32,
        },
        "copy R5 to M0",
    ),
    (
        0x1C33C4,
        "5a_move",
        {
            "srcureghigh[4:0]": 6,
            "srcureglow[1:1]": 1,
            "srcureglow[0:0]": 0,
            "dstureg[6:0]": 16,
        },
        "restore I10 into I0",
    ),
    (
        0x1C33C7,
        "3b",
        {"i[2:0]": 4, "m[2:0]": 0, "l": 1, "x": 1, "w": 0, "ureg[6:0]": 1},
        "load cached per-track short word",
    ),
    (
        0x1C33D2,
        "3b",
        {"i[2:0]": 0, "m[2:0]": 0, "l": 1, "x": 1, "w": 0, "ureg[6:0]": 0},
        "load frame short word at scaled I0+M0",
    ),
    (0x1C33D7, "2c", {"compute[11:0]": 0x310}, "compare cached R1 with frame R0"),
    (
        0x1C33DF,
        "8a_rel",
        {"cond[4:0]": 0, "j": 1, "reladdr[15:0]": 10},
        "branch on EQ (unchanged)",
    ),
    (
        0x1C33E2,
        "6b_shiftimm",
        {"shiftimm[22:16]": 1, "shiftimm[15:0]": 0xF822},
        "delay slot computes R2 = ASHIFT R2 by -8",
    ),
    (
        0x1C33E5,
        "15b",
        {"i[2:0]": 5, "d": 1, "ureg[6:0]": 2, "data[6:0]": 49},
        "common delay-slot store of shifted R2",
    ),
    (
        0x1C33E7,
        "15b",
        {"i[2:0]": 5, "d": 1, "ureg[6:0]": 46, "data[6:0]": 49},
        "non-EQ-only overwrite with M14",
    ),
)


def extract_machine_types(frame: bytes) -> list[int]:
    """Extract the sixteen big-endian machine-type words from a TX frame."""
    if len(frame) != TX_FRAME_BYTES:
        raise ValueError(
            f"frame is {len(frame)} bytes; expected exactly {TX_FRAME_BYTES}"
        )
    return [
        int.from_bytes(
            frame[MACHINE_OFFSET + 2 * i : MACHINE_OFFSET + 2 * i + 2], "big"
        )
        for i in range(TRACKS)
    ]


def _verify_static(memory: LoadedMemory) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for pc, expected_form, expected_fields, role in CHECKS:
        insn = trace.decode_at(memory, None, pc)
        if insn.type_name != expected_form:
            raise ValueError(
                f"{pc:#x}: expected {expected_form}, decoded {insn.type_name}"
            )
        if insn.length_bytes is None or insn.raw is None:
            raise ValueError(
                f"{pc:#x}: confident instruction has no extent or raw word"
            )
        mismatches = {
            name: {"expected": value, "actual": insn.fields.get(name)}
            for name, value in expected_fields.items()
            if insn.fields.get(name) != value
        }
        if mismatches:
            raise ValueError(f"{pc:#x}: field mismatch: {mismatches}")
        storage = memory.read_sw(pc, insn.length_bytes)
        if storage is None:
            raise ValueError(f"{pc:#x}: instruction bytes are not loader-backed")
        verified.append(
            {
                "pc_sw": pc,
                "form": insn.type_name,
                "bytes": storage.hex(),
                "raw": f"0x{insn.raw:0{insn.length_bytes * 2}x}",
                "role": role,
            }
        )
    return verified


def _probe_track(memory: LoadedMemory, track: int) -> dict[str, Any]:
    states = trace.trace(
        memory,
        None,
        0x1C33C4,
        sets={
            "I10": trace.Affine(MACHINE_OFFSET, (("spi_rx", 1),)),
            "M0": track,
            "I7": 0x270000,
            "B7": 0,
            "L7": 0,
            "M7": -1,
            "I6": 0x270000,
        },
        max_steps=100,
        max_states=256,
        concrete_memory=True,
        follow_loaded_calls=True,
        continue_external_calls=True,
        dossier_bytes=16,
        assume_nw32=True,
        core_reset_state=True,
    )
    loads = {
        event["expression"]
        for state in states
        for event in state.trace
        if event.get("pc_sw") == 0x1C33D2 and event.get("action") == "load"
    }
    expected = f"spi_rx + {MACHINE_OFFSET + 2 * track:#x}"
    # `sets` only seeds I10's *initial* value; it does not pin it. With
    # `--follow-loaded-calls`, a state can revisit 0x1c33c4 a second time
    # after a call chain that recomputes I10 from something the tracer
    # cannot resolve (e.g. a scaled float convert whose scale register
    # isn't a known constant at that point), so the calibrated expression
    # is not always the *only* one observed -- only the presence of the
    # expected, fully-resolved expression is load-bearing here.
    if expected not in loads:
        raise ValueError(
            f"track {track}: expected {expected}, observed {sorted(loads)}"
        )
    pcs = {event.get("pc_sw") for state in states for event in state.trace}
    if not {0x1C33D7, 0x1C33DF}.issubset(pcs):
        raise ValueError(f"track {track}: compare/branch endpoint was not reached")
    changed_stores = {
        (event.get("ureg"), event.get("expression"))
        for state in states
        for event in state.trace
        if event.get("pc_sw") == 0x1C33E7 and event.get("action") == "store"
    }
    if changed_stores != {("M14", "I5 + 196")}:
        raise ValueError(
            f"track {track}: unexpected non-EQ store {sorted(changed_stores)}"
        )
    return {
        "track": track,
        "seeded_i10": "spi_rx + 0x94",
        "seeded_m0": track,
        "load_pc_sw": 0x1C33D2,
        "byte_address": expected,
        "access_width": "short-word-sign-extended",
        "compare_pc_sw": 0x1C33D7,
        "eq_branch_pc_sw": 0x1C33DF,
        "non_eq_first_effect": "DM(I5 + 0xc4) = M14",
        "non_eq_effect_pc_sw": 0x1C33E7,
        "terminal_states": len(states),
        "qualifying": False,
    }


def _probe_sport_caller(
    memory: LoadedMemory,
    start: int,
    call_pc: int,
    expected_object: int,
    expected_slot: int,
) -> dict[str, Any]:
    """Replay one compiler frame with a disposable stack to recover arguments."""
    states = trace.trace(
        memory,
        None,
        start,
        sets={
            "I7": 0x261F00,
            "I6": 0x262000,
            "M7": -1,
            "M6": 1,
            "M5": 0,
        },
        max_steps=48,
        max_states=16,
        concrete_memory=True,
        follow_loaded_calls=True,
        assume_nw32=True,
    )
    observed: set[tuple[int, int]] = set()
    for state in states:
        objects = [
            event.get("concrete_value")
            for event in state.trace
            if event.get("pc_sw") == 0x1CA5C7 and event.get("action") == "load"
        ]
        slots = [
            event.get("concrete_value")
            for event in state.trace
            if event.get("pc_sw") == 0x1CA5C9 and event.get("action") == "load"
        ]
        observed.update(
            (object_value, slot_value)
            for object_value in objects
            for slot_value in slots
            if isinstance(object_value, int) and isinstance(slot_value, int)
        )
    expected = (expected_object, expected_slot)
    if expected not in observed:
        raise ValueError(
            f"{call_pc:#x}: compiler-frame probe missed {expected!r}; "
            f"observed {sorted(observed)!r}"
        )
    return {
        "start_pc_sw": start,
        "call_pc_sw": call_pc,
        "callee_pc_sw": 0x1CA58A,
        "object_loaded_into_i3": expected_object,
        "slot_loaded_into_i5": expected_slot,
        "m5": 0,
        "conditional_store": f"DM({expected_slot:#x})={expected_object:#x}",
        "calibration": "disposable stack I7=0x261f00, I6=0x262000; frame-relative values are invariant",
        "qualifying": False,
    }


def _probe_dma_descriptor_list(
    memory: LoadedMemory, specification: Mapping[str, Any]
) -> dict[str, Any]:
    """Replay one straight-line descriptor build up to its submission call."""
    common_sets = {
        "I7": 0x26F000,
        "I6": 0x26F100,
        "M7": -1,
        "M5": 0,
        "R11": 0,
        "B7": 0,
        "L7": 0,
    }
    sets = {
        **common_sets,
        **specification["seeds"],
    }
    states = trace.trace(
        memory,
        None,
        specification["start"],
        sets=sets,
        max_steps=100,
        max_states=16,
        concrete_memory=True,
        follow_loaded_calls=True,
        assume_nw32=True,
        breakpoints=[0x1CA7E4],
    )
    stopped = [state for state in states if state.stopped == "breakpoint"]
    if len(stopped) != 1:
        raise ValueError(
            f"{specification['start']:#x}: expected one descriptor breakpoint, "
            f"got {[state.stopped for state in states]!r}"
        )
    state = stopped[0]
    base = specification["base"]
    nonconcrete = [
        event
        for event in state.trace
        if event.get("action") == "store"
        and base <= event.get("address", -1) < base + 14 * 4
        and event.get("concrete_write") is False
    ]
    if nonconcrete:
        raise ValueError(
            f"{specification['start']:#x}: non-concrete descriptor-template "
            f"writes: {nonconcrete!r}"
        )
    words: list[int] = []
    for offset in range(14):
        value = trace._dm_read(state, base + 4 * offset, 4)
        if not isinstance(value, trace.Const):
            raise ValueError(
                f"{specification['start']:#x}: descriptor word "
                f"{base + 4 * offset:#x} is not concrete"
            )
        words.append(value.value)
    descriptors = [words[:7], words[7:]]
    expected = [list(item) for item in specification["descriptors"]]
    if descriptors != expected:
        raise ValueError(
            f"{specification['start']:#x}: descriptor mismatch: "
            f"expected {expected!r}, got {descriptors!r}"
        )
    fields = (
        "next_descriptor",
        "start_address",
        "configuration",
        "x_count",
        "x_modify",
        "y_count",
        "y_modify",
    )
    return {
        "start_pc_sw": specification["start"],
        "submit_call_pc_sw": specification["call_pc"],
        "submit_function_sw": 0x1CA7E4,
        "list_head": base,
        "classification": "descriptor-list template passed to 0x1ca7e4; live DMA10 fetch not proven",
        "candidate_object": specification["candidate_object"],
        "candidate_slot": specification["candidate_slot"],
        "descriptors": [dict(zip(fields, item, strict=False)) for item in descriptors],
        "buffer_bytes": descriptors[0][3] * descriptors[0][4],
        "calibration": {
            "disposable_stack": True,
            "seeded_values": sets,
            "seed_provenance": {
                "M7": "global initialization at 0x1c0f3c",
                "M5": "global initialization at 0x1c0f44",
                "R11": "calibrated zero across an unresolved caller path",
                "I6/I7/B7/L7": "disposable non-circular compiler frame",
                "slice_specific": "listed prior caller values across unresolved call boundaries",
            },
            "invariant": "the bounded immediate/direct-store descriptor-template words under the disclosed seeds",
        },
        "runtime_object_join": "not proven",
        "qualifying": False,
    }


def _probe_marker_writer(memory: LoadedMemory) -> dict[str, Any]:
    """Replay the byte-backed marker store with the loader's zero selector."""
    states = trace.trace(
        memory,
        None,
        0x1C7524,
        sets={"M5": 0, "M13": 0},
        max_steps=64,
        max_states=4,
        concrete_memory=True,
        assume_nw32=True,
        core_reset_state=True,
        breakpoints=[0x1C7588],
    )
    stopped = [state for state in states if state.stopped == "breakpoint"]
    if len(stopped) != 1:
        raise ValueError(
            "marker writer: expected one breakpoint, got "
            f"{[state.stopped for state in states]!r}"
        )
    state = stopped[0]
    first = trace._dm_read(state, 0x262138, 4)
    peer = trace._dm_read(state, 0x262938, 4)
    if first != trace.Const(0x7FFFFFFF) or peer != trace.Const(0):
        raise ValueError(
            "marker writer mismatch: expected 0x7fffffff/0 in large buffers, "
            f"got {first!r}/{peer!r}"
        )
    return {
        "start_pc_sw": 0x1C7524,
        "selector_address": 0x25F780,
        "selector_value": 0,
        "selector_scale_pc_sw": 0x1C757B,
        "selector_scale_bytes": 0x800,
        "constant_pc_sw": 0x1C7580,
        "base_add_pc_sw": 0x1C7583,
        "store_pc_sw": 0x1C7586,
        "store_address": 0x262138,
        "stored_value": 0x7FFFFFFF,
        "peer_buffer": 0x262938,
        "descriptor_join": "exact ADDRSTART pair in descriptor-list template 0x264138",
        "coldfire_marker_join": "not proven: no exact supported 0x7fffffff to 0x007fffff transport transformation",
        "calibration": {
            "direct_entry": True,
            "loader_selector_value": 0,
            "seeded_modifiers": {"M5": 0, "M13": 0},
            "seed_sources": {"M5": 0x1C0F44, "M13": 0x1C0F42},
        },
        "qualifying": False,
    }


def build_report(blob_path: Path, frame_path: Path | None = None) -> dict[str, Any]:
    blob = blob_path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != DT2_116_BLOB_SHA256:
        raise ValueError(
            f"wrong SHARC image: expected {DT2_116_BLOB_SHA256}, got {digest}"
        )
    memory = LoadedMemory.from_stream(blob)
    report: dict[str, Any] = {
        "schema": 1,
        "image_sha256": digest,
        "evidence": "static-byte-backed plus calibrated symbolic slices",
        "qualifying": False,
        "coldfire_contract": {
            "payload_bytes": TX_FRAME_BYTES,
            "machine_word": "big-endian 16-bit at 0x94 + 2*track",
            "tracks": TRACKS,
        },
        "static_chain": _verify_static(memory),
        "calibrated_track_probes": [_probe_track(memory, i) for i in (0, 1, 15)],
        "calibrated_sport_caller_probes": [
            _probe_sport_caller(memory, *arguments) for arguments in SPORT_CALLER_PROBES
        ],
        "calibrated_dma_descriptor_probes": [
            _probe_dma_descriptor_list(memory, specification)
            for specification in DMA_DESCRIPTOR_PROBES
        ],
        "calibrated_marker_writer_probe": _probe_marker_writer(memory),
        "result": {
            "reader_function_sw": 0x1C2B24,
            "load_pc_sw": 0x1C33D2,
            "compare_pc_sw": 0x1C33D7,
            "unchanged_branch_pc_sw": 0x1C33DF,
            "machine_word": "DM(spi_rx + 0x94 + 2*track) (SWSE)",
            "behavior": "compare received word with cached per-track word; EQ skips the M14 overwrite at DM(I5+0xc4)",
            "non_eq_first_effect": "0x1c33e7 stores M14 to DM(I5+0xc4)",
        },
        "candidate_receive_states": [
            {
                "selector": 1,
                "state_base": 0x261B18,
                "buffer": 0x261BAC,
                "pointer_field": 0x261B38,
                "count_field": 0x261BC8,
                "count": 32,
                "reader_i5_alias": "not proven",
            },
            {
                "selector": 2,
                "state_base": 0x261A10,
                "buffer": 0x261AA4,
                "pointer_field": 0x261A30,
                "count_field": 0x261AC0,
                "count": 32,
                "reader_i5_alias": "not proven",
            },
        ],
        "interface_boundaries": {
            "dma_descriptor_lists": {
                "public_register_order": [
                    "DMA_DSCPTR_NXT",
                    "DMA_ADDRSTART",
                    "DMA_CFG",
                    "DMA_XCNT",
                    "DMA_XMOD",
                    "DMA_YCNT",
                    "DMA_YMOD",
                ],
                "dma10_mmrs": {
                    "descriptor_next": 0x31023000,
                    "start_address": 0x31023004,
                    "configuration": 0x31023008,
                    "x_count": 0x3102300C,
                    "x_modify": 0x31023010,
                    "y_count": 0x31023014,
                    "y_modify": 0x31023018,
                },
                "large_list_geometry": "two 512 x 4-byte ping-pong lists; each buffer is 0x800 bytes",
                "coldfire_geometry_match": "same byte length as one eDMA48/50 major-loop bank; ownership and wiring not proven",
                "dma10_selection": "not proven",
            },
            "reader_i5": {
                "construction": "0x1c76e5 sets I5=I6-14 normal words (I6-0x38 under the 32-bit normal-word model)",
                "use": "0x1c7719 copies I5 to R8 for the 0x1c2b24 call",
                "candidate_frame_values": {
                    "for_0x261bac": 0x261BE4,
                    "for_0x261aa4": 0x261ADC,
                },
                "runtime_i6": "not proven",
            },
            "dma10_store": {
                "base_load": "0x1ca6c3 loads selected-record+0x14 into R0",
                "destination_chain": "L1=DM(P+0x14) at 0x1ca6a6; L2=DM(L1+0x14) at 0x1ca6c9; DM(L2)=SPORT base at 0x1ca6ce; L3=DM(L2+0x14) at 0x1ca6d1; DM(L3)=DMA base at 0x1ca6d6 (M5=0)",
                "destination": "not proven",
            },
            "sport_setup_store": {
                "destination_base": "0x1ca5c9 loads I5 from frame+0x10",
                "value": "0x1ca5d1 copies incoming I3 to R9",
                "store": "0x1ca6f9 stores R9 through I5/M5",
                "caller_pairs": [
                    {"slot": slot, "object": object_value}
                    for _, _, object_value, slot in SPORT_CALLER_PROBES
                ],
                "m5": "initialized to zero at 0x1c0f44 and not written on these caller/callee paths",
                "effect_if_path_executes": "stores each object pointer in its paired global slot",
                "dma10_alias": "disproved for these four caller pairs",
            },
        },
    }
    if frame_path is not None:
        frame = frame_path.read_bytes()
        report["frame"] = {
            "path": str(frame_path),
            "sha256": hashlib.sha256(frame).hexdigest(),
            "bytes": len(frame),
            "machine_types": extract_machine_types(frame),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blob", type=Path, help="DT2 1.16 section_7_BLOB.bin")
    parser.add_argument(
        "--frame", type=Path, help="optional 0x802-byte ColdFire TX frame"
    )
    parser.add_argument("-o", "--output", type=Path, help="write JSON report here")
    args = parser.parse_args()
    report = build_report(args.blob, args.frame)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
