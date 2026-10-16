$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Vendor = Join-Path $Root "vendor\elektron-firmware-tool"
$Native = Join-Path $Root "native\elektron-firmware-tool.exe"
$BuildDeps = Join-Path $Root ".build-deps"
$Gcc = "C:\msys64\ucrt64\bin\gcc.exe"

if (-not (Test-Path -LiteralPath $Gcc)) {
    throw "MSYS2 UCRT64 GCC was not found at $Gcc"
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Native) | Out-Null
$env:PATH = "C:\msys64\ucrt64\bin;" + $env:PATH
Push-Location $Root
try {
    $Sources = @("aplib.c", "container.c", "device.c", "firmware.c", "integrity.c", "main.c", "report.c", "transport.c") | ForEach-Object { "vendor\elektron-firmware-tool\$_" }
    & $Gcc -std=c11 -O2 -Wall -Wextra -static -static-libgcc -I "vendor\elektron-firmware-tool" -o "native\elektron-firmware-tool.exe" @Sources
    if ($LASTEXITCODE -ne 0) { throw "Native encoder build failed." }
} finally {
    Pop-Location
}

python -m unittest discover -s $Root -p "test_builder.py" -v
if ($LASTEXITCODE -ne 0) { throw "Builder tests failed." }

if (-not (Test-Path -LiteralPath (Join-Path $BuildDeps "PyInstaller"))) {
    python -m pip install --disable-pip-version-check --target $BuildDeps -r (Join-Path $Root "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller dependency installation failed." }
}
$env:PYTHONPATH = $BuildDeps
python -m PyInstaller --noconfirm --clean --distpath (Join-Path $Root "dist") --workpath (Join-Path $Root "build") (Join-Path $Root "DT2-DejaVu-Builder.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
