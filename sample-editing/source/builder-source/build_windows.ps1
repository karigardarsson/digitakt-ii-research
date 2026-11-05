$ErrorActionPreference = 'Stop'
$BuildRoot = $PSScriptRoot
python -c "import PyInstaller; assert PyInstaller.__version__ == '6.16.0'"
if ($LASTEXITCODE -ne 0) { throw 'Install requirements-build.txt with Python 3.12 x64.' }
python -m PyInstaller --noconfirm --distpath (Join-Path $BuildRoot 'dist') --workpath (Join-Path $BuildRoot 'packaging-work') (Join-Path $BuildRoot 'DT2-Sample-Editing-Builder.spec')
if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed.' }
