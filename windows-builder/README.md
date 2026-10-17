# Windows builder source

This directory contains the source for the standalone Windows x64 DT2 Déjà Vu builder. It embeds original project patch data, but no official Elektron firmware or extracted firmware section.

## Build environment

- Windows x64
- Python 3.12 with Tk 8.6
- PyInstaller 6.16.0
- MSYS2 UCRT64 GCC at C:\msys64\ucrt64\bin\gcc.exe

Run build_windows.ps1 from PowerShell.

The script compiles the pinned MIT-licensed container encoder, runs the host tests, installs the pinned PyInstaller build dependency into an isolated local folder when needed, and creates dist\DT2-DejaVu-Builder.exe.

The builder accepts only the exact official Digitakt II OS 1.16 or OS 1.17 files. It detects the version by SHA-256 and applies only that version's frozen patch profile. It writes output only when the result matches the corresponding hardware-tested V5.1 SHA-256.

- OS 1.17 stock: `26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5`
- OS 1.17 output: `cd553ef6e84d60401b8ac3abc3492b61343ba8a6ac720cf019e15b613cd67b2e`
- OS 1.16 stock: `278541e466edcd77d6b3e018a91fb90185932d3c7de224dd3e68294dddf3a9ec`
- OS 1.16 output: `d9fe67b3778ce5d31aaaa807e6e43ac1929d0f36a10c393adc0a8c590c547835`

