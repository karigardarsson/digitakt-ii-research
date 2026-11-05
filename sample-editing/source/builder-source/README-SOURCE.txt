CURRENT CORRESPONDING SOURCE - 2026-11-05
Fixed stock-input OS1.17 builder, no Modular+ runtime or unrelated engines.
assets/: seven CF modules, custom DSP assets and sealed composition recipes.
firmware-source/sample_lab/: current preferred 3b zoom-scroll/A/B/H source,
with prior text polish and focused H rotation threshold2; objects/lab.* are current object/link map.
Other preferred sources/objects are reused unchanged from the existing pack.
relink_modules.py reproduces all seven CF payloads without stock firmware or
a compiler. Current Lab source compilation is verified; clean compilation of
all historical CF/DSP sources is not newly qualified. Source/compiler and
historical hardware evidence remain distinct. PROVENANCE.json records origins.
tools/: original patchkit link helpers. vendor/: MIT encoder source.
Keep LICENSE, NOTICE and third-party terms, including DigiKit attribution.
No Elektron firmware, raw section, key, PCM, device or flash tool is included.
Use top-level README.md, docs/HOW_TO.md, TEST_REPORT.md and acceptance checklist.
End users use the EXE; source users may run python builder_gui.py with CLI args.
For packaging: Python3.12 x64, PyInstaller6.16.0 and build_windows.ps1.
Top-level source/test_builder.py uses DT2_TEST_STOCK to locate user-owned stock.
Historical evidence/ files retain their original hashes and acceptance status.
