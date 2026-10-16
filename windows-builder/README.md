# Windows builder source

This directory contains the source for the standalone Windows x64 DT2 Déjà Vu builder. It embeds original project patch data, but no official Elektron firmware or extracted firmware section.

## Build environment

- Windows x64
- Python 3.12 with Tk 8.6
- PyInstaller 6.16.0
- MSYS2 UCRT64 GCC at C:\msys64\ucrt64\bin\gcc.exe

Run build_windows.ps1 from PowerShell.

The script compiles the pinned MIT-licensed container encoder, runs the host tests, installs the pinned PyInstaller build dependency into an isolated local folder when needed, and creates dist\DT2-DejaVu-Builder.exe.

The builder accepts only official Digitakt II OS 1.16 with SHA-256 278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec. It writes output only when the generated file matches V5.1 SHA-256 d9fe67b3778ce5d31aaaa807e6e43ac1929d0f36a10c393adc0a8c590c547835.

