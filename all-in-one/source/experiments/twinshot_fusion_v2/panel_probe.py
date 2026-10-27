"""Scheduled native CF panel events with zero-DSP peer; no audio claim.

Supports actual firmware CF cold boot or a diagnostic runtime overlay. Neither
mode establishes coupled DSP audio or physical hardware equivalence.
"""
import argparse
import hashlib
import json
import struct
import tempfile
import subprocess
import os
from pathlib import Path

from tools.control.digi_client import Emu
from tools.patchkit import port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "out"):
        ap.add_argument("--" + name, type=Path, required=True)
    ap.add_argument("--state",type=Path)
    ap.add_argument("--cold",action="store_true")
    ap.add_argument("--trap",action="store_true")
    ap.add_argument("--omit-fus2-hint",action="store_true")
    ap.add_argument("--context-ui",action="store_true",help="validate mode-aware UI without duplicate SHAP")
    a = ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    stock = Path("firmware/Digitakt_II_OS1.17.syx").resolve()
    binary = Path("out/native/target-wt/spacejam-hopt2-playable/nogen/release/digi-emud").resolve()
    state = (a.state or Path("out/cache/pk_cfcall/Digitakt_II_OS1.17.state")).resolve()
    before, after = port_resolve.sections_of(stock)[3], port_resolve.sections_of(a.image)[3]
    plan = json.loads(a.plan.read_text())
    def sym(name):
        return next(v for k,v in plan["symbols"].items() if k.endswith(":"+name))
    edits = {}
    end = dv.STOCK_MAIN_END - 0x40000400
    for i in range(0,end,4):
        if before[i:i+4] != after[i:i+4]:
            edits[0x40000400+i] = int.from_bytes(after[i:i+4],"big")
    for src,n,dest,bss in dv.parsed_segments(after)[0]:
        data = dv.at(after,src+12,n)+bytes(bss)
        assert dest % 4 == 0
        for i in range(0,len(data),4):
            edits[dest+i] = int.from_bytes(data[i:i+4].ljust(4,b"\0"),"big")
    report = {"image_sha256":hashlib.sha256(a.image.read_bytes()).hexdigest(),
              "tier":"native scheduled CF UI, zero-DSP runtime overlay; no audio or hardware proof",
              "edits":len(edits),"events":[]}
    try:
        # AF_UNIX sockets are unsupported on this WSL Windows-mounted tree.
        # Keep only the private live socket on Linux tmp; reports stay local.
        with tempfile.TemporaryDirectory(prefix="fus2-panel-") as tmp, \
             Emu.spawn(str(stock),"--dsp","zero","--cf-snapshot",str(state),"--paused",
                       binary=binary,run_dir=tmp,timeout=30) as e:
            print("daemon ready",flush=True)
            if a.cold:
                report["tier"] = "CF-only cold boot and scheduled native UI; modeled DSP peer, no audio/hardware proof"
                report["firmware_load"] = e.call("firmware.load",syx=str(a.image.resolve()),
                    base_sha256="26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5",
                    force=True,auto_no=True,start="cold")
                print("actual candidate CF cold boot completed",flush=True)
            else:
                for address,value in edits.items():
                    e.poke32(address,value)
                print("actual CF overlay loaded",flush=True)
            if a.trap:
                toolroot = Path(__file__).resolve().parents[2]/"out/dt2-dv-offline/tools/root"
                prefix = str(toolroot/"usr/bin/m68k-linux-gnu-")
                env = dict(os.environ,LD_LIBRARY_PATH=str(toolroot/"usr/lib/x86_64-linux-gnu"))
                obj,elf,binfile = (Path(tmp)/x for x in ("trap.o","trap.elf","trap.bin"))
                subprocess.run([prefix+"as","-mcfv4e","-o",str(obj),str(Path(__file__).with_name("trap_fixture.s"))],env=env,check=True)
                subprocess.run([prefix+"ld.bfd","-Ttext=0x47f22000","-e","repair_event_sink","-o",str(elf),str(obj)],env=env,check=True)
                subprocess.run([prefix+"objcopy","-O","binary",str(elf),str(binfile)],env=env,check=True)
                trap = binfile.read_bytes()
                for i in range(0,len(trap),4):
                    e.poke32(0x47f22000+i,int.from_bytes(trap[i:i+4].ljust(4,b"\0"),"big"))
                # Overwrite the abort self-loop plus following unreachable
                # bytes, in emulator RAM only. All page code stays original.
                e.poke32(0x4013ABC0,0x60fe4ef9)
                e.poke32(0x4013ABC4,0x47f22000)
                names = subprocess.check_output([prefix+"nm","-g",str(elf)],env=env,text=True)
                target = next(int(line.split()[0],16) for line in names.splitlines() if line.endswith(" repair_header_probe"))
                tail = e.peek32(0x40016E70)&65535
                e.poke32(0x40016E6C,0x4ef90000|(target>>16))
                e.poke32(0x40016E70,((target&65535)<<16)|tail)
            if a.omit_fus2_hint:
                addr = sym("mf_names")+14*12+8
                report["removed_hint_pointer"] = e.peek32(addr)
                assert report["removed_hint_pointer"]==0x40242C9E
                e.poke32(addr,0)
            # Cold boot changes heap placement. Locate the real initialized
            # SourcePageView; never treat the old snapshot's address as live.
            views = [p for p in range(0x450A8000,0x450B8000,4) if e.peek32(p)==0x401EDC98]
            report["source_views"] = views
            print("source views",[hex(v) for v in views],flush=True)
            assert len(views)==1, views
            view = views[0]
            def observe(label):
                (a.out/(label.replace(" ","-")+".png")).write_bytes(e.frame_png())
                kit = e.peek32(0x80004704)
                sound = kit+0x34
                row = {"label":label,"status":e.status(),"sound":sound,
                       "audio_sound_cache":e.peek32(0x8000470C),
                       "type":((e.peek32(sound+0xA0) or 0)>>8)&255,
                       "source_page":e.peek32(view+0x90),
                       "start_words":e.peek32(sound+0x14+29*2-2),
                       "filter_words":e.peek32(sound+0x14+37*2-2)}
                row["mirrors"] = {str(i): ((e.peek32((sound+0x14+2*i)&~3) or 0) >>
                                  (16 if (sound+0x14+2*i)%4==0 else 0))&65535
                                  for i in range(25,39)}
                report["events"].append(row)
                print(json.dumps({k:v for k,v in row.items() if k!="status"}),flush=True)
                cf = row["status"]["cf"]
                assert cf.get("error") is None, cf
                if not a.trap:
                    assert cf["pc"] != 0x4013ABC2, "native abort self-loop: UI frozen"
            def step(ms):
                e.step(int(ms*132000),timeout_wall_s=30)
            def tap(key):
                e.button(key,True); step(20)
                e.button(key,False); step(10)
            tap(12); step(300)
            observe("ready")
            # Native machine-selection UI. No direct type or parameter writes.
            e.button(17,True); step(5); tap(2); e.button(17,False); step(100)
            observe("machine-menu")
            order = [e.peek32(sym("mf_list_buf")+4*i) for i in range(16)]
            report["native_machine_order"] = order
            print("machine order",order,flush=True)
            assert 14 in order, order
            for _ in range(order.index(14)):
                tap(14); step(20)
            tap(10); step(100); tap(10); step(100)
            observe("FUS2 selected")
            assert report["events"][-1]["type"] == 14
            tap(2); step(50); observe("SRC first")
            if a.trap:
                report["trap_registers"] = [e.peek32(0x47f0d000+4*i) for i in range(16)]
                report["trap_stack"] = [e.peek32(0x47f0d080+4*i) for i in range(512)]
                report["exception_words"] = [e.peek32(0x450DBEF0+4*i) for i in range(32)]
                report["header_padding_count"] = e.peek32(0x47f0e000)
                print("header padding count",report["header_padding_count"],flush=True)
                print("trap",[hex(v) for v in report["trap_registers"]],flush=True)
                print("code returns",[hex(v) for v in report["trap_stack"] if v and 0x40000400<v<0x40320000],flush=True)
                return
            assert report["events"][-1]["source_page"] == 1
            tap(25); step(100); observe("native audition")
            tap(2); step(100); observe("SRC return first")
            assert report["events"][-1]["source_page"] == 0
            tap(2); step(100); observe("SRC page2 again")
            assert report["events"][-1]["source_page"] == 1
            before_controls = report["events"][-1]["mirrors"]
            before_hits = report["events"][-1]["status"]["cf"]["mainloop_hits"]
            for enc in ((1,2) if a.context_ui else (1,2,3)):
                for _ in range(40):
                    e.turn(enc,4); step(2)
                step(50); observe(f"page2 encoder {enc}")
            after_controls = report["events"][-1]["mirrors"]
            report["page2_changed_mirrors"] = [i for i in before_controls if before_controls[i]!=after_controls[i]]
            report["page2_loop_progress"] = report["events"][-1]["status"]["cf"]["mainloop_hits"]-before_hits
            assert report["page2_loop_progress"]>0, "native UI stopped progressing"
            assert set(report["page2_changed_mirrors"]) == ({"29","30"} if a.context_ui else {"29","30","34"}), report["page2_changed_mirrors"]
            tap(2); step(100); observe("SRC return")
            assert report["events"][-1]["source_page"] == 0
            if a.context_ui:
                # FUSE now occupies encoder4. Drive real encoder events until
                # each discrete mode is reached; never poke Sound to make a UI
                # screenshot pass. Bound attempts and reject overshooting.
                for mode,name in enumerate(("BLND","RING","DUCK","MRPH","GATE")):
                    for attempt in range(128):
                        kit=e.peek32(0x80004704); snd=kit+0x34
                        raw=e.peek32(snd+0x14+26*2)&65535
                        if raw==mode*256:
                            break
                        e.turn(4,1 if raw<mode*256 else -1);step(100)
                    else:
                        raise AssertionError(("native FUSE encoder cannot reach mode",mode,raw))
                    step(100);observe(name+" page1")
                    tap(2);step(100);observe(name+" page2")
                    if mode:
                        for _ in range(24):
                            e.turn(3,4);step(4)
                        step(100);observe(name+" response help")
                    tap(2);step(100)
                    for _ in range(24):
                        e.turn(7,4);step(4)
                    step(100);observe(name+" amount help")
                assert all(r["type"]==14 for r in report["events"] if r["label"] not in ("ready","machine-menu"))
                # Repair4: exercise context-dependent INFO through scheduled
                # native SYNC changes, not direct writes to saved fields.
                # Actually re-enter OFF, rather than mislabeling the previous
                # knob's still-visible help while SYNC happens to start at0.
                for sync,name in ((1,"MATCH warmup"),(0,"OFF"),(1,"MATCH"),(2,"REL")):
                    for attempt in range(128):
                        snd=e.peek32(0x80004704)+0x34
                        address=snd+0x14+26*2
                        word=e.peek32(address&~3)
                        raw=(word>>(16 if address%4==0 else 0))&65535
                        if raw==sync*256:break
                        e.turn(3,1 if raw<sync*256 else -1);step(100)
                    else:raise AssertionError(("native SYNC cannot reach",sync,raw))
                    step(100);observe("SYNC "+name+" help")
            report["status"] = "COMPLETED"
    except Exception as exc:
        report.update({"status":"BLOCKED_OR_FAULT","error":repr(exc)})
        print(repr(exc),flush=True)
    finally:
        (a.out/"panel-report.json").write_text(json.dumps(report,indent=2)+"\n")


if __name__ == "__main__":
    main()
