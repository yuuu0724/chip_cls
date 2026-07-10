# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH)

datas = []
binaries = []
hiddenimports = [
    "text_det",
    "text_cls",
    "text_rec",
    "chip_det",
    "dict_loader",
    "logger_utils",
    "session_utils",
    "utils",
    "pymodbus.client",
    "pymodbus.client.serial",
    "serial",
]


def add_data_tree(path, target):
    src = ROOT / path
    if src.exists():
        datas.append((str(src), target))


add_data_tree("onnx", "onnx")
add_data_tree("ocr_onnx_py", "ocr_onnx_py")
add_data_tree("config", "config")

for package in [
    "onnxruntime",
    "nvidia.cublas",
    "nvidia.cuda_nvrtc",
    "nvidia.cuda_runtime",
    "nvidia.cudnn",
    "nvidia.cufft",
    "nvidia.curand",
    "nvidia.cusolver",
    "nvidia.cusparse",
    "nvidia.nvjitlink",
]:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT), str(ROOT / "ocr_onnx_py")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chipocr",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="chipocr",
)
