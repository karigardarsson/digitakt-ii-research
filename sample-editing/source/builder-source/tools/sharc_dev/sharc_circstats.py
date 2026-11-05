#!/usr/bin/env python3
"""Count circular-buffer post-modifies in a generated SHARC core.

Copies a generated core directory (``tools/sharc_dev/sharc_rsgen.py`` output, normally
``out/native/dn2-audio/gen``) and adds a hook to every copy of
``memory._post_modify_index`` in it: each load/store post-modify of an index
register with MODE1.CBUFEN set and L != 0 is counted, by (I register, B, scaled L,
scaled modifier), and the ones whose result differs from the plain sum (the
wrapped ones) also by I register and PC. With ``SHARC_CIRC_NOWRAP=1`` the hook
returns the plain sum, which shows what a run would do without the wrap. The
totals are printed to stderr at exit when ``SHARC_CIRC_STATS`` is set:

    uv run python -m tools.sharc_dev.sharc_circstats out/native/dn2-audio/gen out/native/dn2-audio/gen-circstat
    uv run python -m tools.emulator.native_pgo build --gen out/native/dn2-audio/gen-circstat
    uv run python -m tools.emulator.native_pgo run BINARY --log LOG --env SHARC_CIRC_STATS=1
    rg CIRC_ LOG

The copy is a separate gen (its own cargo target dir, see tools/emulator/native_target.py)
and is a diagnostic only: do not use it for timing.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys

HOOK = r"""
// ---- diagnostic hook (tools/sharc_dev/sharc_circstats.py) ----
pub fn circ_hook(idx: i128, pc: i128, iv: i128, base: i128, length: i128, delta: i128, r: i128) -> i128 {
    use std::collections::BTreeMap;
    use std::sync::{Mutex, OnceLock};
    static NOWRAP: OnceLock<bool> = OnceLock::new();
    static WRAPS: Mutex<BTreeMap<(i64, i64), u64>> = Mutex::new(BTreeMap::new());
    static CONFIGS: Mutex<BTreeMap<(i64, i64, i64, i64), u64>> = Mutex::new(BTreeMap::new());
    static TOTAL: Mutex<(u64, u64)> = Mutex::new((0, 0));
    static INIT: OnceLock<bool> = OnceLock::new();
    INIT.get_or_init(|| {
        unsafe extern "C" {
            fn atexit(cb: extern "C" fn()) -> i32;
        }
        extern "C" fn dump() {
            if std::env::var_os("SHARC_CIRC_STATS").is_none() {
                return;
            }
            let t = *TOTAL.lock().unwrap();
            eprintln!("CIRC_STATS total_circular_postmods={} wrapped={}", t.0, t.1);
            for ((i, b, l, d), n) in CONFIGS.lock().unwrap().iter() {
                eprintln!("CIRC_CFG I{} B={:#x} Lscaled={:#x} delta={} n={}", i, b, l, d, n);
            }
            for ((i, pc), n) in WRAPS.lock().unwrap().iter() {
                eprintln!("CIRC_WRAP I{} pc={:#x} n={}", i, pc, n);
            }
        }
        unsafe {
            atexit(dump);
        }
        true
    });
    let nowrap = *NOWRAP.get_or_init(|| std::env::var_os("SHARC_CIRC_NOWRAP").is_some());
    let plain = (iv + delta) & 0xFFFF_FFFF;
    {
        let mut t = TOTAL.lock().unwrap();
        t.0 += 1;
        if r != plain {
            t.1 += 1;
        }
    }
    *CONFIGS
        .lock()
        .unwrap()
        .entry((idx as i64, base as i64, length as i64, delta as i64))
        .or_insert(0) += 1;
    if r != plain {
        *WRAPS.lock().unwrap().entry((idx as i64, pc as i64)).or_insert(0) += 1;
        if nowrap {
            return plain;
        }
    }
    r
}
"""

CALL = re.compile(
    r"((?:super::memory|crate::generated::image::spec_\d+)::_circular_step"
    r"(?:__v\d+)?\(s, (?:rf, )?\(iv\)\.val\(\), \(base\)\.val\(\), scaled_length, __a1\)\?)"
)


def instrument(src: str, dst: str) -> int:
    """Copy SRC to DST and hook every post-modify; returns the hook count."""
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    hooked = 0
    for path in glob.glob(dst + "/core_*.rs") + glob.glob(dst + "/spec_*.rs"):
        out = []
        index = None
        core = False
        with open(path) as handle:
            lines = handle.read().split("\n")
        for line in lines:
            m = re.search(r"pub fn _post_modify_index(__v\d+)?\(", line)
            if m:
                core = m.group(1) is None
                index = "index" if core else None
            if index is None and not core:
                # A specialised copy names its length register: L<n> = 48 + n.
                m2 = re.search(
                    r"length = bnd::_ureg_rf\(rf, RegView::CUR, (\d+)i128\)", line
                )
                if m2:
                    index = f"{int(m2.group(1)) - 48}i128"
            m3 = CALL.search(line)
            if m3 and index is not None:
                pc = "s.pc_sw" if core else "rf.pc"
                new = (
                    "{ let __r = %s; crate::generated::syms::circ_hook(%s, %s, (iv).val(), "
                    "(base).val(), scaled_length, __a1, __r) }"
                    % (m3.group(1), index, pc)
                )
                line = line.replace(m3.group(1), new)
                hooked += 1
            out.append(line)
        with open(path, "w") as handle:
            handle.write("\n".join(out))
    with open(dst + "/syms.rs", "a") as handle:
        handle.write(HOOK)
    return hooked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", help="generated core directory")
    parser.add_argument("dst", help="instrumented copy (replaced)")
    args = parser.parse_args()
    n = instrument(args.src, args.dst)
    print(f"{n} post-modify sites hooked in {args.dst}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
