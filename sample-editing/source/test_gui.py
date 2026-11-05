"""Focused native Tk layout/text/dialog checks; no firmware or device actions."""
from pathlib import Path
import sys,json,tkinter as tk
from tkinter import font
from unittest.mock import patch
H=Path(__file__).resolve().parent
(H/'validation').mkdir(exist_ok=True)
sys.path.insert(0,str(H/'builder-source'));import builder_gui as g
def main():
 root=tk.Tk();root.withdraw();app=g.Application(root)
 root.geometry('760x560-30000-30000');root.deiconify();root.update()
 box=root.winfo_children()[0];widgets=box.winfo_children()
 intro=next(w for w in widgets if hasattr(w,'cget') and w.winfo_class()=='TLabel' and 'Supply your own' in str(w.cget('text')))
 assert font.Font(root=root,font=intro.cget('font')).cget('size')==10
 assert g.HARDWARE_ACCEPTED is True
 assert 'Final hardware acceptance required' not in intro.cget('text')
 assert app.log.get('1.0','end-1c')=='Ready. Select your stock OS 1.17 firmware to begin.\n'
 assert app.log.cget('state')=='disabled' and app.log.winfo_height()>=50
 bounds=[]
 for w in widgets:
  y=w.winfo_y();height=w.winfo_height();assert y>=0 and y+height<=box.winfo_height(),(w,y,height,box.winfo_height())
  bounds.append(dict(widget=w.winfo_class(),y=y,height=height))
 assert all(a['y']+a['height']<=b['y'] for a,b in zip(bounds,bounds[1:])),bounds
 footer=widgets[-1];assert 'digikit by Em D - https://github.com/ElekLabs/digikit' in footer.cget('text')
 with patch.object(g.filedialog,'asksaveasfilename',return_value='') as dialog:
  app.choose_output();options=dialog.call_args.kwargs
  assert options['defaultextension']=='.syx' and options['initialfile']==g.OUTPUT_NAME and g.OUTPUT_NAME.endswith('.syx')
 with patch.object(g.filedialog,'askopenfilename',return_value='C:/Samples/Stock.syx'):
  app.choose_stock();assert Path(app.output.get()).name==g.OUTPUT_NAME
 g.HARDWARE_ACCEPTED=False;assert 'Final hardware acceptance required' in g.explanatory_text()
 g.HARDWARE_ACCEPTED=True;assert 'Final hardware acceptance required' not in g.explanatory_text()
 root.destroy()
 result=dict(status='PASS',native_Tk_geometry=[760,560],explanatory_font_points=10,ready_message=True,current_release_warning_absent=True,accepted_text_branch_removes_warning=True,acceptance_flag_remains_true=True,dialog_options=options,stock_selection_default=g.OUTPUT_NAME,layout_bounds=bounds,attribution_preserved=True,limitation='Programmatic native Tk widget/layout checks; final frozen GUI not manually inspected, DPI/Windows variants not newly qualified.')
 (H/'validation/gui-tests.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS native Tk font/readiness/status/layout/dialog/attribution checks')
if __name__=='__main__':main()
