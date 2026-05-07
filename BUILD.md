# 打包说明

把 ChipOCR 打包成 Windows 可执行程序（PyInstaller onedir 模式），发给客户使用。
最后一次成功打包：`dist_20260507_165310\chipocr\`，体积约 5.9 GB，目标硬件 GTX 1650 + 驱动级 CUDA 13.x。

---

## 一、前置（开发机一次性配置）

- Python 3.10+ conda 环境（项目验证用 `proj_ocr`）
- `pip install -r requirements.txt`
- `pip install pyinstaller`（验证 6.19.0）
- 命令行可正常 `python main.py` 跑通，并在 `logs\app_*.log` 看到 `GPU=True`

---

## 二、一键打包

```powershell
cd F:\Data\Project\OCR-Proj\chipocr
.\build.bat
```

`build.bat` 等价于：

```powershell
python -m PyInstaller chipocr.spec --clean --noconfirm
```

产物：`dist\chipocr\chipocr.exe` + `dist\chipocr\_internal\`，整体约 5.9 GB。

> **如果 `dist\` 被占用导致打包失败**（多见于 Windows Defender 还在扫描刚生成的 5GB DLL，或 Explorer 打开过 `dist\chipocr\_internal`），用带时间戳的输出目录绕开：
> ```powershell
> $stamp = Get-Date -Format yyyyMMdd_HHmmss
> python -m PyInstaller chipocr.spec --noconfirm `
>     --distpath "dist_$stamp" --workpath "build_$stamp"
> ```

---

## 三、产物自检

打包后**必做一步**：双击 `dist\chipocr\chipocr.exe` 跑一次"开始检测"，看 `dist\chipocr\logs\app_YYYYMMDD.log`：

GPU 正常的标志：

```
已注册 CUDA DLL 目录: ['..._internal\\nvidia\\cublas\\bin', '..._internal\\nvidia\\cuda_runtime\\bin', ...]
ONNX Runtime 可用 providers: [..., 'CUDAExecutionProvider', ...]
模型 [det] 加载完成 | providers=['CUDAExecutionProvider', 'CPUExecutionProvider'] | GPU=True | device_id=0
```

如果看到 `GPU=False`，代码会自动触发诊断块强制 CUDA-only 重建会话，把 ORT 真实异常打到日志：
```
[诊断] CUDA EP 强制加载失败: <类型>: <信息>
```
同时 `logs\stderr_YYYYMMDD.log` 会保存 ORT C++ 层详细输出。

---

## 四、压缩并发送给客户

**压整个 `chipocr` 文件夹**（不要加 `\*`，否则解压时会散开到客户的目录里）：

```powershell
# 7-Zip：约 2 GB（推荐）
& "C:\Program Files\7-Zip\7z.exe" a -t7z -mx=5 "F:\chipocr_v1.7z" `
    "F:\Data\Project\OCR-Proj\chipocr\dist\chipocr"

# 或 PowerShell 内置 zip：约 3 GB
Compress-Archive `
    -Path "F:\Data\Project\OCR-Proj\chipocr\dist\chipocr" `
    -DestinationPath "F:\chipocr_v1.zip" `
    -CompressionLevel Optimal
```

通过百度网盘 / 阿里云盘 / OneDrive / 公司文件服务器发给客户（邮件传不动）。

---

## 五、客户机器运行要求

> 把下面这段直接发给客户

**前置（一次性配置，配过的电脑可跳过）**：

1. NVIDIA 显卡 + 驱动 R525 以上（驱动级 CUDA 12.x / 13.x 都可，向下兼容）
2. **Visual C++ 2015–2022 Redistributable (x64)**：
   <https://aka.ms/vs/17/release/vc_redist.x64.exe>
   多数 Win10/11 自带，缺了会提示 "找不到 MSVCP140.dll"。装一次就好。

**运行步骤**：

1. 解压压缩包到任意目录（例如 `D:\chipocr\`），保留 `_internal\` 子目录完整，**不要只把 `chipocr.exe` 单独拷出来**。
2. 双击 `chipocr.exe`。
3. 首次启动 5–15 秒（CUDA 初始化 + cuDNN 算法搜索），之后正常。
4. 程序在 `chipocr.exe` 同级自动生成：
   - `config\`：用户配置
   - `logs\app_YYYYMMDD.log`：运行日志
   - `results\`：检测产物 (CSV + 截图)

**报错排查**：把 `logs\app_YYYYMMDD.log` 整个回传给开发方。

---

## 六、关键设计决策与踩坑记录

PyInstaller 打包 `onnxruntime-gpu` 有几个非显然的坑，代码里都已修好。下面是为什么本项目的 `chipocr.spec` 与 `_register_nvidia_dll_dirs()` 必须这样写，方便后续维护时不要无意改坏。

### 1. 冻结态 DLL 搜索路径必须显式注册

PyInstaller bootloader 在 Win10+ 调用 `SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_USER_DIRS)`，限定 DLL 只能从 `AddDllDirectory` 注册过的目录加载。`onnxruntime_providers_cuda.dll` 调 `LoadLibrary("cublasLt64_12.dll")` 时不会再回退到自身所在目录。

修法：`main.py` / `ocr_onnx_py/session_utils.py` 的 `_register_nvidia_dll_dirs()` 在冻结态按优先级注册：
- `_MEIPASS/nvidia/<lib>/bin/`（最先）
- `_MEIPASS/onnxruntime/capi/`
- `_MEIPASS` 根目录（兜底）

### 2. ORT 自带的 capi DLL 在 GTX 1650 上 CUDA 初始化静默失败

`onnxruntime-gpu==1.20.1` wheel 的 `capi/` 自带一份 cublas/cudnn DLL，但实测在 GTX 1650 上 ORT 把 `CUDAExecutionProvider` 静默从 active 列表剔除，session 仍成功创建但只剩 CPU EP。换用 `nvidia-*-cu12` wheel 的 DLL（dev 验证可用）则正常。

修法：`chipocr.spec` 同时 collect `onnxruntime` 与 全部 `nvidia.*` wheel，并在代码里把 `nvidia/*/bin` 注册到 DLL 搜索路径**最前**，让 Windows 优先匹配 wheel 版本而不是 ORT 自带版本。代价是体积 ~5.9 GB（vs 仅 ORT 时 ~2.9 GB）。

### 3. ORT 静默回退 CPU 时 Python 层无任何异常

ORT 把 CUDA EP 从 active 列表剔除时不抛异常。`session.get_providers()` 显示 `['CPUExecutionProvider']`，但 `ort.InferenceSession()` 调用本身成功返回。

修法：`session_utils.py:create_session()` 末尾加诊断块——`use_gpu=True` 但 active 不含 CUDA 时，立刻 CUDA-only 重建一次会话，逼 ORT 抛真实异常并 log 出来。仅对第一个加载的模型触发，避免对 cls/det/rec 三次重复刷诊断。

### 4. `console=False` 下 stderr 全丢

PyInstaller GUI 打包（`console=False`，本项目就是）后 `sys.stderr` 是 None，ORT C++ 层的 `std::cerr` 输出全部丢失。

修法：`main.py:_redirect_stderr_in_frozen()` 在冻结态把 stderr 重定向到 `logs/stderr_YYYYMMDD.log`，并 `os.dup2` 让 native code 的 fd=2 也指向同一文件。

### 5. UPX 压缩会破坏 CUDA DLL

`chipocr.spec` 的 `EXE` / `COLLECT` 都明确设 `upx=False`。CUDA 大型 DLL（cublasLt 600+MB）经 UPX 压缩后 LoadLibrary 会失败。**不要打开 UPX。**

### 6. DLL 注册顺序不可调

`main.py` 与 `ocr_onnx_py/session_utils.py` 中 `_register_nvidia_dll_dirs()` 必须**早于任何 `import onnxruntime`**。`ocr_onnx_py/session_utils.py` 顶部 `import onnxruntime as ort` 之前必须先调用注册函数（已用模块级初始化保证）。

---

## 七、相关文件清单

```text
chipocr.spec                       PyInstaller 配置（onedir + nvidia.* 全套）
build.bat                          一键打包脚本（清理旧产物 → 跑 PyInstaller）
main.py                            含 NVIDIA DLL 注册 + stderr 重定向（冻结态触发）
ocr_onnx_py/session_utils.py       含 DLL 注册 + CUDA 静默回退诊断
```

打包产物（`dist\` / `build\`）不入库，参见 `CLAUDE.md` 中的"提交注意"。
