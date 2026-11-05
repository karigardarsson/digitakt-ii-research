from pathlib import Path
root=Path(SPECPATH)
a=Analysis([str(root/'builder_gui.py')],pathex=[str(root)],binaries=[],
           datas=[(str(root/'assets'),'assets'),(str(root/'native'),'native')],hiddenimports=[])
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='DT2-Sample-Editing-Builder',
        debug=False,strip=False,upx=False,console=False,disable_windowed_traceback=True)
