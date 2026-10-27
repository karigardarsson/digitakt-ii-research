"""Paired final-image DSP regression against immutable Repair4 and stock."""
import hashlib
import json
from pathlib import Path
from patchkit.test_all_in_one25_dsp import armed
from support.plaits_rig import ImageRig,CALLER_RET
from support.dsp_inline_image import image_memory,render_voice
from patchkit.test_dsp_inline import active_voice_pokes
HERE=Path(__file__).resolve().parent;DV=HERE.parents[1]
W=DV/'out/all-in-one-polish-fang-r4-work';R4=DV/'out/twinshot-fus2-repair4-work'
HOST=DV.parent/'plaits-va-run02/out/spacejam-hopt2-playable';SRC=HOST/'out/all-in-one-plaits25-e48/source'

def main():
    image=W/'complete-polished.syx'
    old=ImageRig(R4/'integrated-05.syx',json.loads((R4/'seed-05.plan.json').read_text()))
    new=ImageRig(image,json.loads((W/'fang-seed.plan.json').read_text()))
    cases=[]
    for model in range(25):
        for rig in (old,new):armed(rig,10,model)
        for block in range(2):
            for rig in (old,new):rig.poke_byte(0x24f0cc+20,int(block==0))
            a,b=old.block(20),new.block(20)
            assert a['final_pc_sw']==b['final_pc_sw']==CALLER_RET,(model,a['halt'],b['halt'])
            for key in ('out','regs'):assert a[key]==b[key],(model,block,key)
        cases.append(model);print('Plaits exact',model,flush=True)
    stock=image_memory(SRC/'firmware/Digitakt_II_OS1.17.syx');current=image_memory(image)
    for voice in (4,29):
        p=active_voice_pokes(voice);a=render_voice(stock,voice,p);b=render_voice(current,voice,p)
        assert a['final_pc_sw']==b['final_pc_sw']==CALLER_RET
        for key in ('out','record','regs'):assert a[key]==b[key],(voice,key)
    r=dict(status='PASS',image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
        Plaits25_two_blocks_PCM_and_registers_exact=cases,stock_SAMPLE_PCM_record_registers_exact=[4,29],
        tier='bounded actual native DSP with synthetic frames/armed records; not hardware')
    (W/'dsp-regression.json').write_text(json.dumps(r,indent=2)+'\n');print('DSP regression PASS',flush=True)

if __name__=='__main__':main()
