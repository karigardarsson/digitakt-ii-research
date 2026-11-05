"""Offline Windows application and file-only command line entry point."""
from pathlib import Path
import argparse
import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog,messagebox,ttk

from builder_core import BuildError,build_firmware,OUTPUT_NAME,OUTPUT_SHA256

# Change only after Kári has accepted this exact firmware on hardware.
HARDWARE_ACCEPTED = True

def explanatory_text():
    status='' if HARDWARE_ACCEPTED else ' Final hardware acceptance required.'
    return ('Supply your own official Digitakt II OS 1.17 .syx file.\n'
            'Includes Universal Zoom, SmartSlice, SampleLAB and CFADE.'+status+'\n'
            'Builds and verifies a local file; never connects to or flashes your instrument.')

class Application:
    def __init__(self,root):
        self.root=root
        root.title('Digitakt II — Sample Editing Pack Builder')
        root.geometry('760x560'); root.minsize(720,520)
        self.stock=tk.StringVar();self.output=tk.StringVar();self.events=queue.Queue();self.busy=False
        box=ttk.Frame(root,padding=20);box.pack(fill='both',expand=True)
        ttk.Label(box,text='Digitakt II Sample Editing Pack',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(box,text='Universal Zoom  •  SmartSlice  •  SampleLAB  •  CFADE',font=('Segoe UI',12)).pack(anchor='w',pady=(5,12))
        ttk.Label(box,text=explanatory_text(),font=('Segoe UI',10),wraplength=680).pack(anchor='w')
        self.controls=[]
        for title,var,command in [('Stock OS 1.17',self.stock,self.choose_stock),('Save new firmware as',self.output,self.choose_output)]:
            ttk.Label(box,text=title).pack(anchor='w',pady=(12,3))
            row=ttk.Frame(box);row.pack(fill='x')
            entry=ttk.Entry(row,textvariable=var);entry.pack(side='left',fill='x',expand=True)
            button=ttk.Button(row,text='Browse…',command=command);button.pack(side='left',padx=(8,0))
            self.controls.extend([entry,button])
        self.build_button=ttk.Button(box,text='Build Sample Editing Pack',command=self.start)
        self.build_button.pack(anchor='w',pady=14);self.controls.append(self.build_button)
        self.log=tk.Text(box,height=7,wrap='word',state='disabled',font=('Segoe UI',10));self.log.pack(fill='both',expand=True)
        self.append('Ready. Select your stock OS 1.17 firmware to begin.')
        ttk.Label(box,text='Kári / karigardarsson • Modified work derived from DigiKit\ndigikit by Em D - https://github.com/ElekLabs/digikit\nNot affiliated with Elektron. Source, license and notices accompany this application.',wraplength=680).pack(anchor='w',pady=(12,0))
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.after(100,self.poll)

    def choose_stock(self):
        path=filedialog.askopenfilename(title='Choose official Digitakt II OS 1.17',filetypes=[('Firmware','*.syx')])
        if path:
            self.stock.set(path)
            if not self.output.get():self.output.set(str(Path(path).with_name(OUTPUT_NAME)))

    def choose_output(self):
        path=filedialog.asksaveasfilename(title='Save new Sample Editing firmware',defaultextension='.syx',initialfile=OUTPUT_NAME,filetypes=[('Firmware','*.syx')])
        if path:self.output.set(path)

    def append(self,text):
        self.log.configure(state='normal');self.log.insert('end',text+'\n');self.log.see('end');self.log.configure(state='disabled')

    def start(self):
        if self.busy:return
        if not self.stock.get() or not self.output.get():
            messagebox.showerror('Choose files','Choose your stock input and a new output filename.');return
        self.busy=True
        for widget in self.controls:widget.configure(state='disabled')
        stock,output=self.stock.get(),self.output.get()
        def worker():
            try:self.events.put(('done',build_firmware(stock,output,lambda s:self.events.put(('progress',s)))))
            except BuildError as exc:self.events.put(('error',str(exc)))
            except Exception:self.events.put(('error','Unexpected build failure. No verified output was committed.'))
        threading.Thread(target=worker,daemon=True).start()

    def poll(self):
        while not self.events.empty():
            kind,value=self.events.get()
            if kind=='progress':self.append(value)
            else:
                self.busy=False
                for widget in self.controls:widget.configure(state='normal')
                if kind=='done':self.append('Verified and saved: '+value['path']+'\nSHA-256: '+value['sha256'])
                else:self.append('Build stopped: '+value)
        self.root.after(100,self.poll)

    def close(self):
        if self.busy:messagebox.showinfo('Build in progress','Please wait for the build to finish.');return
        self.root.destroy()

def main():
    if len(sys.argv)>1:
        parser=argparse.ArgumentParser(description='Build the fixed Sample Editing Pack from your own stock DT2 OS 1.17; no device access.')
        parser.add_argument('--build',required=True,metavar='STOCK.syx')
        parser.add_argument('--output',required=True,metavar='NEW.syx')
        parser.add_argument('--report',type=Path,metavar='NEW.json')
        args=parser.parse_args()
        if args.report and (args.report.exists() or args.report.resolve() in (Path(args.build).resolve(),Path(args.output).resolve())):
            return 2
        if args.report and not args.report.parent.is_dir():return 2
        try:
            result=build_firmware(args.build,args.output);code=0
        except (BuildError,OSError) as exc:
            result={'status':'REJECTED','error':str(exc)};code=1
        if args.report:
            try:
                with args.report.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,ensure_ascii=False)
            except OSError:return 2
        if sys.stdout:print(json.dumps(result,ensure_ascii=True))
        return code
    root=tk.Tk();Application(root);root.mainloop();return 0

if __name__=='__main__':
    raise SystemExit(main())
