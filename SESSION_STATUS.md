# 本次会话状态记录

## 已完成：GPU 修复

### 根本原因（两个）

1. **用户级 CPU 版遮盖 GPU 版**
   - 路径 `C:\Users\duany\AppData\Roaming\Python\Python312\site-packages\onnxruntime 1.25.0`（CPU 专用）在 sys.path 中排在 conda 环境前面
   - conda `ocr` 环境实际装有 `onnxruntime-gpu 1.20.1`，但被覆盖
   - **已修复**：删除用户级 onnxruntime 1.25.0（文件夹 + dist-info）

2. **缺少 CUDA/cuDNN DLL**
   - `onnxruntime_providers_cuda.dll` 的依赖 DLL 全部缺失
   - **已修复**：在 `ocr` conda 环境中安装了以下 9 个 nvidia pip 包：

```
nvidia-cuda-runtime-cu12   → cudart64_12.dll
nvidia-cublas-cu12         → cublas64_12.dll, cublasLt64_12.dll
nvidia-cudnn-cu12          → cudnn64_9.dll
nvidia-cufft-cu12          → cufft64_11.dll
nvidia-curand-cu12         → curand64_10.dll
nvidia-cusolver-cu12       → cusolver64_11.dll
nvidia-cusparse-cu12       → cusparse64_12.dll
nvidia-cuda-nvrtc-cu12     → nvrtc64_120_0.dll
nvidia-nvjitlink-cu12      → nvjitlink_120_0.dll
```

### 安装位置
`C:\Users\duany\.conda\envs\ocr\Lib\site-packages\nvidia\`

### GPU 修复验证（日志已确认）
```
INFO | session_utils | ONNX Runtime 可用 providers: ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']
INFO | session_utils | 模型 [det] 加载完成 | GPU=True | device_id=0
INFO | session_utils | 模型 [cls] 加载完成 | GPU=True | device_id=0
INFO | session_utils | 模型 [rec] 加载完成 | GPU=True | device_id=0
```

### 代码改动
**无**。`session_utils.py` 中的 `_register_nvidia_dll_dirs()` 已能自动注册 nvidia pip 包目录，无需修改任何代码。

---

## 已解决：字符识别失败排查记录

### 原始现象
GPU 已启用，模型全部正常加载，但识别结果为空，日志停在：
```
INFO | ocr.text_det | det raw_output_shape=(1, 1, 512, 288)
```
之后无任何 rec 输出，说明识别结果为空或被过滤掉。

### 疑点 1：`ocr/engine.py` 过滤阈值异常严格

当时代码（`ocr/engine.py` 第 388 行）：
```python
if score > 0.8 and len(clean_text) > 4 and clean_text not in valid_texts:
```

CLAUDE.md 文档记录的产品调参阈值：
```
score > 0.5、len > 2
```

**严格程度相差很大**：当前阈值要求置信度 > 0.8 且文本长度 > 4，会过滤掉大量有效识别结果。`ocr/engine.py` 在 git status 中显示为已修改（M），说明最近被改动过。

### 疑点 2：缺少 `pyclipper`

`ocr_onnx_py/text_det.py` 的 DB 后处理 `unclip()` 依赖 `pyclipper`。
缺失时，检测日志会停在：

```text
det raw_output_shape=...
```

后续不会进入：

```text
det post_boxes=...
OCR 识别候选 text=...
```

### 已执行排查步骤

1. **对比 git diff，确认阈值是什么时候改的**
   ```bash
   git diff HEAD ocr/engine.py
   ```

2. **恢复产品调参阈值**：
   ```python
   # engine.py 第 388 行
   if score > 0.5 and len(clean_text) > 2 ...
   ```

3. **加日志验证 det 是否有框输出**，在 `_predict_core` 中补充：
   ```python
   logger.info("OCR 检测候选框数量=%d", len(raw_results))
   ```

4. **安装 OCR 检测后处理依赖**：
   ```powershell
   C:\Users\duany\.conda\envs\ocr\python.exe -m pip install pyclipper==1.3.0.post6
   ```

### 环境信息
- GPU: RTX 4060
- CUDA 驱动: 12.6（driver 560.94）
- onnxruntime-gpu: 1.20.1（CUDA 12.x build, cuDNN 9.x）
- conda 环境: ocr（Python 3.12.13）
- 项目路径: `F:\Data\Project\OCR-Proj\chipocr`

---

## 2026-04-27 继续处理：OCR 结果为空排查

### 已修改

1. `ocr/engine.py`
   - 将识别过滤阈值恢复为产品调参值：
     ```python
     score > 0.5 and len(clean_text) > 2
     ```
   - 增加 OCR 诊断日志：
     - `OCR 检测候选框数量=...`
     - `OCR 检测未返回文本框，使用整图作为识别候选`
     - `OCR 识别候选 text=... score=... len=...`
     - `OCR 过滤后文本=...，兜底文本=...`

2. `requirements.txt`
   - 新增：
     ```text
     pyclipper==1.3.0.post6
     ```
   - 原因：`ocr_onnx_py/text_det.py` 的 DB 后处理 `unclip()` 必须使用 `pyclipper`。当前环境缺失时，日志会停在 `det raw_output_shape` 之后，无法进入 `det post_boxes` / rec 阶段。

### 环境处理

- 已使用实际 OCR 环境路径验证：
  ```powershell
  C:\Users\duany\.conda\envs\ocr\python.exe
  ```
- `pyclipper 1.3.0.post6` 已可被实际 OCR 环境导入。
- 注意：因为环境目录不可写，pip 将包安装到用户级 site-packages：
  ```text
  C:\Users\duany\AppData\Roaming\Python\Python312\site-packages
  ```
  实际 OCR 环境 `ENABLE_USER_SITE=True`，沙盒外验证可正常导入。

### 验证

```powershell
python -m compileall main.py ui ocr data workers ocr_onnx_py
```

结果：通过。

```powershell
C:\Users\duany\.conda\envs\ocr\python.exe -c "from ocr.engine import OCREngine; engine = OCREngine(); print(engine.backend_init_error)"
```

结果：`None`，说明引擎初始化成功。

```powershell
C:\Users\duany\.conda\envs\ocr\python.exe -c "import numpy as np; from ocr.engine import OCREngine; engine = OCREngine(); image = np.full((240, 320, 3), 255, dtype=np.uint8); print(engine.predict_image_from_array(image))"
```

结果：`{'angle': 0, 'texts': [], 'status': 'empty'}`，空白图主流程未抛异常。

### 用户验证结果

用户已确认实际识别成功。最终有效修改如下：

- `ocr/engine.py`
  - 恢复识别过滤阈值：`score > 0.5`、`len(clean_text) > 2`
  - 增加 det/rec 诊断日志，便于区分“检测无框”“识别为空”“过滤过严”
- `requirements.txt`
  - 新增 `pyclipper==1.3.0.post6`
- 环境
  - 实际 OCR 环境可导入 `pyclipper 1.3.0.post6`
  - GPU provider 已正常启用

### 后续注意

实际料盘图片运行时，重点看日志是否出现：

```text
det post_boxes=...
OCR 检测候选框数量=...
OCR 识别候选 text=...
```

如果三类日志都出现但结果仍为空，再优先检查模板匹配、目标型号/角度判定逻辑，而不是先怀疑 GPU。
