---
name: fix-gpu
description: >
  修复并诊断 Windows 上 ONNX Runtime GPU 推理失败的问题。
  当出现 LoadLibrary error 126、CUDAExecutionProvider 加载失败、
  onnxruntime_providers_cuda.dll 找不到、active providers 只有 CPU、
  用户级 onnxruntime CPU 包遮盖 conda GPU 包，或 GPU 已启用但 OCR 仍无结果时触发。
  会定位解释器、provider、CUDA/cuDNN DLL、nvidia wheel 路径、pyclipper 和 OCR 过滤阈值。
allowed-tools: [Bash, Read, Edit, Glob]
disable-model-invocation: false
---

# ONNX Runtime GPU / OCR Empty Result Fix Skill

## 背景知识

Windows 上 `LoadLibrary error 126` = **依赖 DLL 缺失或不在搜索路径**，不是版本不兼容。
ORT 的报错提示「Require cuDNN 9.* / CUDA 12.*」是通用提示语，不代表真的装错版本。

本项目曾成功修复过两个 GPU 根因：

1. 用户级 `onnxruntime` CPU 包排在 conda `onnxruntime-gpu` 前面，导致 GPU 包被遮盖。
2. `onnxruntime_providers_cuda.dll` 缺少 CUDA/cuDNN 依赖 DLL。

pip / nvidia-channel conda 安装的 CUDA wheel 把 DLL 放在：
```
<env>/Lib/site-packages/nvidia/*/bin/
```
这个路径**不在** Windows 默认 DLL 搜索路径里，必须手动注册。

`os.add_dll_directory` 对某些第三方 C 库（如 ORT）的内部 LoadLibrary 不保证生效，
必须同时前置到 `os.environ["PATH"]` 才能兜底。

---

如果 GPU 已启用但 OCR 结果仍为空，不要立即继续改 GPU。先看检测/识别日志：

- 有 `det raw_output_shape` 但没有 `det post_boxes`：优先检查 `pyclipper`。
- 有 `OCR 识别候选 text=... score=... len=...` 但过滤后为空：检查 `ocr/engine.py` 阈值。
- 产品阈值必须保持：`score > 0.5`、`len > 2`、`max_ocr_boxes=4`、`max_return_texts=2`。

---

## 执行步骤

### Step 1：诊断环境

运行以下命令，收集当前状态：

```!
python -c "import sys, onnxruntime as ort; print('exe:', sys.executable); print('ort:', ort.__file__); print('providers:', ort.get_available_providers())"
```

```!
python -m pip show onnxruntime onnxruntime-gpu
```

```!
python -c "import sys, os; nvidia=os.path.join(sys.prefix,'Lib','site-packages','nvidia'); dirs=[os.path.join(nvidia,n,'bin') for n in os.listdir(nvidia)] if os.path.isdir(nvidia) else []; print('\n'.join(dirs))"
```

如果 `ort.__file__` 指向用户级目录，例如：

```text
C:\Users\<user>\AppData\Roaming\Python\Python312\site-packages\onnxruntime
```

而当前项目实际使用 conda `ocr` 环境，则需要删除或卸载用户级 CPU 版 `onnxruntime`，否则会遮盖 conda 环境中的 `onnxruntime-gpu`。

### Step 2：安装缺失的 CUDA 组件

检查是否缺少以下 DLL：

```text
cudart64_12.dll
cublas64_12.dll
cublasLt64_12.dll
cudnn64_9.dll
cufft64_11.dll
curand64_10.dll
cusolver64_11.dll
cusparse64_12.dll
nvrtc64_120_0.dll
nvjitlink_120_0.dll
```

对 `onnxruntime-gpu==1.20.1`、CUDA 12.x、cuDNN 9.x，可用 pip 补齐：

```!
python -m pip install nvidia-cuda-runtime-cu12 nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-cuda-nvrtc-cu12 nvidia-nvjitlink-cu12
```

安装后确认目录存在：

```!
python -c "import sys, os; root=os.path.join(sys.prefix,'Lib','site-packages','nvidia'); print(root); print(os.listdir(root) if os.path.isdir(root) else 'missing')"
```

### Step 3：定位 session_utils.py

**不要**访问 build、dist、logs、results、main.spec、README、requirements.txt 等无关文件。

只查找包含 `onnxruntime` 或 `InferenceSession` 的核心推理文件：

```!
python -c "
import glob, os
for f in glob.glob('**/*.py', recursive=True):
    if any(x in open(f,encoding='utf-8',errors='ignore').read() for x in ['InferenceSession','onnxruntime']):
        if not any(skip in f for skip in ['build','dist','__pycache__']):
            print(f)
"
```

### Step 4：修改 session_utils.py

先检查文件中是否已经有 `_register_nvidia_dll_dirs()`。

如果已有，并且会遍历 `site-packages/nvidia/*/bin`，同时调用 `os.add_dll_directory()` 和前置 `PATH`，不要重复改代码。

如果没有，在找到的文件顶部，`import onnxruntime` **之前**，插入以下代码：

```python
import os
import sys

def _register_nvidia_dll_dirs() -> list:
    """
    Windows 上将 site-packages/nvidia/*/bin 同时注册到
    os.add_dll_directory 和 PATH 前缀，确保 ORT 内部 LoadLibrary 能找到 CUDA DLL。
    参考：gpu_fix_log.md - 单用 add_dll_directory 对 ORT 内部调用不保证生效。
    """
    if sys.platform != "win32":
        return []
    registered: list = []
    for root in {sys.prefix, sys.base_prefix}:
        nvidia_root = os.path.join(root, "Lib", "site-packages", "nvidia")
        if not os.path.isdir(nvidia_root):
            continue
        for name in os.listdir(nvidia_root):
            bin_dir = os.path.join(nvidia_root, name, "bin")
            if os.path.isdir(bin_dir):
                try:
                    os.add_dll_directory(bin_dir)
                except OSError:
                    pass
                cur_path = os.environ.get("PATH", "")
                if bin_dir not in cur_path:
                    os.environ["PATH"] = bin_dir + os.pathsep + cur_path
                registered.append(bin_dir)
    return registered

_REGISTERED_DLL_DIRS = _register_nvidia_dll_dirs()

import onnxruntime as ort  # 必须在 DLL 目录注册之后导入
```

### Step 5：更新 create_session（如果存在）

找到创建 `InferenceSession` 的地方，将 provider 改为元组形式并支持环境变量控制：

```python
import os

def create_session(model_path: str, session_options=None):
    use_gpu = os.environ.get("OCR_USE_GPU", "1") != "0"
    device_id = int(os.environ.get("OCR_GPU_DEVICE_ID", "0"))

    cuda_provider = (
        "CUDAExecutionProvider",
        {
            "device_id": device_id,
            "cudnn_conv_algo_search": "DEFAULT",
            "arena_extend_strategy": "kNextPowerOfTwo",
            "do_copy_in_default_stream": True,
        },
    )

    providers = [cuda_provider, "CPUExecutionProvider"] if use_gpu else ["CPUExecutionProvider"]

    session = ort.InferenceSession(
        model_path,
        sess_options=session_options,
        providers=providers,
    )
    return session
```

### Step 6：验证

在本项目中优先用实际 OCR 环境验证：

```!
C:\Users\duany\.conda\envs\ocr\python.exe -c "from ocr.engine import OCREngine; engine = OCREngine(); print('init_error=', engine.backend_init_error)"
```

期望输出：
```
init_error= None
```

同时日志应出现：

```text
ONNX Runtime 可用 providers: ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']
模型 [det] 加载完成 | GPU=True | device_id=0
模型 [cls] 加载完成 | GPU=True | device_id=0
模型 [rec] 加载完成 | GPU=True | device_id=0
```

---

## GPU 已成功但 OCR 仍为空

### Step 7：检查 pyclipper

`ocr_onnx_py/text_det.py` 的 DB 后处理 `unclip()` 依赖 `pyclipper`。

如果日志停在：

```text
INFO | ocr.text_det | det raw_output_shape=(...)
```

且没有：

```text
INFO | ocr.text_det | det post_boxes=...
```

执行：

```!
C:\Users\duany\.conda\envs\ocr\python.exe -c "import pyclipper; print(pyclipper.__version__)"
```

缺失则安装并写入 `requirements.txt`：

```!
C:\Users\duany\.conda\envs\ocr\python.exe -m pip install pyclipper==1.3.0.post6
```

```text
pyclipper==1.3.0.post6
```

如果环境目录不可写，pip 可能安装到用户级 site-packages。只要实际 OCR 环境 `ENABLE_USER_SITE=True` 且能导入即可。

### Step 8：检查 OCR 过滤阈值

确认 `ocr/engine.py` 使用产品阈值：

```python
if score > 0.5 and len(clean_text) > 2 and clean_text not in valid_texts:
```

不要改成 `score > 0.8`、`len > 4` 之类更严格的值。

建议保留诊断日志：

```python
logger.info("OCR 检测候选框数量=%d", len(raw_results))
logger.info("OCR 识别候选 text=%r score=%.4f len=%d", clean_text, score, len(clean_text))
logger.info("OCR 过滤后文本=%s，兜底文本=%s", valid_texts, fallback_texts)
```

---

## 迁移到新机器的最小步骤

1. 安装 NVIDIA 驱动（≥ CUDA 12.x）
2. 确认没有用户级 CPU 版 `onnxruntime` 遮盖项目环境
3. `pip install onnxruntime-gpu==1.20.1`
4. 安装 9 个 `nvidia-*-cu12` pip 包补齐 CUDA/cuDNN DLL
5. `pip install pyclipper==1.3.0.post6`
6. 直接运行，`session_utils.py` 会自动注册 DLL 路径

---

## 环境变量说明

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OCR_USE_GPU` | `1` | 设为 `0` 强制回退 CPU |
| `OCR_GPU_DEVICE_ID` | `0` | 多卡时选择 GPU 编号 |
