from pathlib import Path
import os,platform
root=Path(os.environ['DT2_MACOS_STAGE'])
arch=platform.machine()
a=Analysis([str(root/'launcher.py')],pathex=[str(root)],binaries=[],
           datas=[(str(root/'assets'),'assets'),(str(root/'native'),'native')],hiddenimports=[])
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='DT2 Sample Editing Builder',
        debug=False,strip=False,upx=False,console=False,argv_emulation=False,
        target_arch=arch,codesign_identity=None)
coll=COLLECT(exe,a.binaries,a.datas,name='DT2 Sample Editing Builder')
app=BUNDLE(coll,name='DT2 Sample Editing Builder.app',
           bundle_identifier='is.karigardarsson.dt2sampleediting',
           info_plist={'CFBundleName':'DT2 Sample Editing Builder',
                       'CFBundleShortVersionString':'1.0.0','LSMinimumSystemVersion':'15.0',
                       'NSHighResolutionCapable':True})
