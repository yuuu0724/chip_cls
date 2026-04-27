# CLAUDE.md

本文件为 Claude Code 在此仓库工作时的指导说明。

## 项目概览

PySide6（Qt）桌面应用——“AI 芯片料盘视觉检测系统”。应用对芯片料盘图像执行 OCR，并将每个槽位标记为 **正常 / 方向错误 / 型号错误 / 空 / 识别失败**。料盘规格支持 `3×7`、`4×6`、`2×10` 及自定义行列配置。

## 运行方式

建议使用项目验证过的 Python 3.12 conda 环境：

```powershell
python -m pip install -r requirements.txt
python main.py
```

无测试套件、无 lint 配置。日志输出到 `logs/app_YYYYMMDD.log`。提交前至少执行：

```powershell
python -m compileall main.py ui ocr data workers ocr_onnx_py
```

## 目录结构

```text
chipocr/
├── main.py                  入口，注册 NVIDIA DLL + 初始化日志 + 启动 Qt
├── data/                    配置、日志、料盘管理与 AppServices 服务容器
│   ├── services.py          AppServices：装配 engine/managers/logger 注入 UI
│   ├── config_manager.py    应用配置（config/app_config.json）
│   ├── tray_manager.py      料盘配置（config/trays_config.json）
│   └── logger.py            CSV 日志 + 界面截图
├── ocr/                     OCR 流水线与业务判定
│   ├── engine.py            ONNX 推理引擎（cls -> det -> rec）
│   ├── template_manager.py  型号模板管理（config/templates.json）
│   └── logic_controller.py  检测结果判定逻辑
├── ocr_onnx_py/             text_det/text_cls/text_rec + ONNX session 管理
├── ui/                      PySide6 界面
│   ├── main_window.py       主窗口 OCRApp，仅依赖 AppServices
│   ├── material_slot.py     单个料位组件
│   ├── dialogs.py           模板、拍摄、虚拟键盘、新增料盘、移动确认对话框
│   └── styles.py            QSS 样式常量集中管理
├── workers/                 QThread 后台线程
│   ├── camera_worker.py     摄像头预览
│   ├── control_worker.py    图片目录批量检测
│   ├── live_inspection_worker.py  实时逐槽位识别
│   └── rs232_interface.py   RS232 通信预留接口
├── onnx/{det,cls,rec}/      ONNX 模型文件与 inference.yml
├── config/templates.json    默认型号模板（随代码提交）
└── requirements.txt
```

## 关键架构约束

- **ONNX Session 类级共享**：`OCREngine._shared_detector/classifier/recognizer` 跨实例复用，不可改为实例级状态。`AppServices.create_default()` 必须先构造 `OCREngine`，再构造 `TemplateManager`。
- **DLL 注册顺序**：`main.py` 和 `ocr_onnx_py/session_utils.py` 中必须先注册 NVIDIA DLL 目录，再 `import onnxruntime`。
- **路径解析**：`ocr/engine.py` 中 `get_resource_root()` 使用 `parents[1]`；`get_helper_root()` 使用 `parents[1] / "ocr_onnx_py"`。
- **OCR 过滤阈值**：`score > 0.5`、`len > 2`、`max_ocr_boxes=4`、`max_return_texts=2` 是产品调参结果，勿随意修改。
- **OCR 后处理依赖**：`ocr_onnx_py/text_det.py` 的 DB 后处理 `unclip()` 依赖 `pyclipper`。如果日志停在 `det raw_output_shape` 且没有 `det post_boxes`，优先检查 `pyclipper`。
- **OCR 空结果排查**：GPU provider 正常但结果为空时，先看 `det post_boxes`、`OCR 检测候选框数量`、`OCR 识别候选 text/score/len`，再判断是检测无框、识别为空还是过滤过严。
- **料盘规格**：`TRAY_SPEC_PRESETS` 与 `CUSTOM_TRAY_SPEC_KEY` 在 `ui/dialogs.py`；`tray_manager` 保存每个料盘的 `spec` 键。
- **服务注入**：UI 只依赖 `AppServices`，不要在 `OCRApp` 中直接 new 底层 manager；`OCRApp(services=None)` 仍需兼容。
- **样式集中管理**：所有 QSS 字面量放在 `ui/styles.py`，UI 逻辑仅引用样式常量。
- **实时识别线程**：`LiveInspectionWorker` 连续采集 3 帧，状态一致才确认槽位；手动/自动模式都通过 `confirm_move()` 进入下一槽位。

## 编码规范

- 使用 4 空格缩进
- 模块、函数使用 `snake_case`，类使用 `CamelCase`
- Qt 事件处理函数保持现有命名风格，如 `on_tray_changed`
- 用户界面文本、日志消息、用户可见注释统一使用中文
- 槽位索引内部从 `0` 开始，CSV 日志和 UI 标签显示时加 `1`

## 依赖与 GPU

- `requirements.txt` 固定了 PySide6、OpenCV、NumPy、ONNX Runtime GPU、`pyclipper` 与 NVIDIA CUDA/cuDNN pip 包版本。
- Windows GPU 环境依赖 `site-packages/nvidia/*/bin` 下的 DLL，启动代码会通过 `os.add_dll_directory` 和 `PATH` 前置注册。
- 若用户级 `onnxruntime` CPU 包遮盖 conda 环境中的 `onnxruntime-gpu`，需优先清理遮盖包。

## 提交注意

- 不提交运行产物：`logs/`、`results/`、`build/`、`dist/`
- 不提交本地配置：`config/app_config.json`、`config/trays_config.json`、`.idea/`、`.claude/settings.local.json`
- `config/templates.json` 是共享初始数据，仅在默认模板确实变化时提交
- 提交信息使用中文短句
