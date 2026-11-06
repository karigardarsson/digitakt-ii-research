"""Native macOS bundle entry point; the existing GUI and file-only CLI are reused."""
from pathlib import Path
import json,platform,sys,os
import builder_gui as gui
import builder_core as core

def self_test(destination):
    assert sys.platform=='darwin' and getattr(sys,'frozen',False)
    resources,manifest=core.checked_resources()
    assert core.firmware_tool(resources).name=='elektron-firmware-tool'
    root=gui.tk.Tk();root.withdraw();app=gui.Application(root)
    root.geometry('760x560+20000+20000');root.deiconify();root.update()
    box=root.winfo_children()[0];widgets=box.winfo_children()
    assert app.log.get('1.0','end-1c').startswith('Ready. Select your stock OS 1.17')
    assert app.log.cget('state')=='disabled' and app.log.winfo_height()>=40
    labels=[str(w.cget('text')) for w in widgets if w.winfo_class()=='TLabel']
    assert any('digikit by Em D - https://github.com/ElekLabs/digikit' in s for s in labels)
    assert not any('Final hardware acceptance required' in s for s in labels)
    for w in widgets:assert 0<=w.winfo_y() and w.winfo_y()+w.winfo_height()<=box.winfo_height()
    options={};original=gui.filedialog.asksaveasfilename
    try:
        gui.filedialog.asksaveasfilename=lambda **kw:options.update(kw) or ''
        app.choose_output()
    finally:gui.filedialog.asksaveasfilename=original
    assert options['defaultextension']=='.syx' and options['initialfile']==core.OUTPUT_NAME
    tk_version=root.tk.call('info','patchlevel');root.destroy()
    result=dict(status='PASS',native_architecture=platform.machine(),macOS=platform.mac_ver()[0],
                frozen=True,python=platform.python_version(),Tk=str(tk_version),
                native_resources_verified=True,GUI_created=True,layout_and_ready_log=True,
                attribution_preserved=True,save_dialog_default=options['initialfile'],
                output_sha256=core.OUTPUT_SHA256,stock_sha256=core.STOCK_SHA256)
    Path(destination).write_text(json.dumps(result,indent=2)+'\n')
    return 0

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--self-test':
        raise SystemExit(self_test(sys.argv[2]))
    raise SystemExit(gui.main())
