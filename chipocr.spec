# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

目标：onedir 模式打包出 dist/chipocr/ 整个文件夹，客户拷过去双击 chipocr.exe 即可运行，
无需安装 Python / CUDA Toolkit / cuDNN。

收集要点
--------
1. onnxruntime 的全部 capi (含 onnxruntime_providers_cuda.dll、providers_shared.dll)。
2. 全部 nvidia.* wheel 的 DLL（cublas/cudart/cudnn/cufft/curand/cusolver/cusparse/nvjitlink）。
3. 模型资源 onnx/{det,cls,rec}/ 与辅助包 ocr_onnx_py/。
4. 默认模板 config/templates.json（其余 *.json 是用户运行态配置，首次启动自动生成）。

打包后客户机器要求
------------------
- NVIDIA 显卡 + 驱动 ≥ R525（GTX 1650 + 驱动级 CUDA 12+/13+ 都支持）。
- Visual C++ 2015-2022 Redistributable (x64)。
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = []
binaries = []
hiddenimports = []


def _merge(pkg: str) -> None:
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# 1) ONNX Runtime GPU：连同 capi 下所有 provider DLL 一起收
_merge("onnxruntime")

# 2) NVIDIA CUDA / cuDNN wheel：必须收齐。
#    虽然 onnxruntime/capi/ 里也自带一份同名 DLL，但实测 ORT 自带版本在某些机器上
#    无法正常初始化 CUDA EP（提示 GPU=True 却静默回退 CPU）。dev 环境验证过
#    nvidia/* wheel 的 DLL 是工作的，所以 spec 必须把 nvidia/* wheel 也带上，
#    并在 session_utils 里优先注册它们（add_dll_directory 注册顺序 = 搜索优先级）。
for _pkg in (
    "nvidia.cuda_runtime",
    "nvidia.cublas",
    "nvidia.cudnn",
    "nvidia.cufft",
    "nvidia.curand",
    "nvidia.cusolver",
    "nvidia.cusparse",
    "nvidia.nvjitlink",
):
    _merge(_pkg)

# 3) pyclipper (DB 后处理 unclip)，cv2 (摄像头/图像)
hiddenimports.extend(collect_submodules("pyclipper"))
_merge("cv2")

# 4) 业务资源：模型、辅助包、共享默认配置
datas += [
    ("onnx/det", "onnx/det"),
    ("onnx/cls", "onnx/cls"),
    ("onnx/rec", "onnx/rec"),
    ("ocr_onnx_py", "ocr_onnx_py"),
    ("config/templates.json", "config"),
]


a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 减小体积：项目用不到的大库
        "tkinter",
        "matplotlib",
        "scipy",
        "pandas",
        "PIL.ImageQt",
        "PyQt5",
        "PyQt6",
        "torch",
        "tensorflow",
        "jupyter",
        "notebook",
        "IPython",
        "sympy",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chipocr",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # CUDA DLL 用 UPX 压缩极易加载失败，必须 False
    console=False,        # GUI 应用，关闭黑色控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
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
