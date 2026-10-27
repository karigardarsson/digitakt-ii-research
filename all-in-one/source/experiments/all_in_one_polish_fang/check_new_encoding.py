"""Early isolated new-object gate; never substitutes for the whole-image gate."""
import json
from pathlib import Path
from tools.patchkit import pk_encoding_gate as g,port_resolve
from tools.sharc_dev.sharcldr import LoadedMemory
HERE=Path(__file__).resolve().parent;W=HERE.parents[1]/'out/all-in-one-polish-fang-r4-work'
def main():
    plan=json.loads((W/'fang-seed.plan.json').read_text());part=dict(plan)
    part['writes']=[w for w in plan['writes'] if w['owner'].startswith('machines/sy-fang@')]
    ranges=g.code_ranges(part);assert ranges
    mem=LoadedMemory.from_stream(port_resolve.sections_of(W/'fang-seed.syx')[7]);db=g.default_db(plan)
    errors=g.check_code(mem,ranges,g.stock_shapes(db)|g.proven_shapes(),g.stock_control_words(db)|g.proven_words())
    errors+=g.check_simd_memory(mem,ranges)+g.check_same_dag(mem,ranges)
    r=dict(status='PASS' if not errors else 'FAIL',new_object_only=True,full_image_gate_still_required=True,
           errors=[g.format_violation(v) for v in errors])
    (W/'new-encoding.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r),flush=True)
    assert not errors
if __name__=='__main__':main()
