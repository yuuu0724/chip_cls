# Repository Guidelines

## 项目概览

PySide6（Qt）桌面应用——“AI 芯片料盘视觉检测系统”。应用对芯片料盘图像执行 OCR，并将每个槽位标记为 **正常 / 方向错误 / 型号错误 / 空 / 识别失败**。料盘规格支持 `3×7`、`4×6`、`2×10` 及自定义行列配置。

## 项目结构与模块组织

- `main.py`：桌面入口；先注册 NVIDIA DLL 路径，再初始化日志并启动 PySide6 应用
- `ui/`：界面层；包含主窗口、料位组件、对话框与集中式 QSS 样式常量
- `workers/`：后台 `QThread`；包含摄像头预览、批量检测、实时逐槽位检测与 RS232 预留接口
- `ocr/`：OCR 编排与业务逻辑；包含 ONNX 推理引擎、模板管理、结果判定
- `data/`：配置、持久化与服务容器；`AppServices` 负责装配 engine/managers/logger 并注入 UI
- `ocr_onnx_py/`：底层 ONNX OCR 辅助模块与 session 管理
- `onnx/{det,cls,rec}`：检测、方向分类、文字识别模型资源
- `config/`：版本化配置；默认模板位于 `config/templates.json`
- `logs/`、`results/`：运行时生成产物，不提交

## 开发与运行

建议使用项目验证过的 Python 3.12 conda 环境：

```powershell
python -m pip install -r requirements.txt
python main.py
```

- `python main.py` 是主要本地冒烟运行方式
- 当前没有提交 `pytest` 或 lint 配置
- 日志输出到 `logs/app_YYYYMMDD.log`
- 提交前至少执行一次语法检查：

```powershell
python -m compileall main.py ui ocr data workers ocr_onnx_py
```

## 关键架构约束

- **ONNX Session 类级共享**：`OCREngine._shared_detector/classifier/recognizer` 必须跨实例复用，不可重构为实例级状态；`AppServices.create_default()` 中需先创建 `OCREngine`，再创建 `TemplateManager`，避免重复加载模型
- **DLL 注册顺序**：`main.py` 与 `ocr_onnx_py/session_utils.py` 中必须先注册 NVIDIA DLL 目录，再执行 `import onnxruntime`
- **路径解析**：`ocr/engine.py` 中 `get_resource_root()` 使用 `parents[1]`（项目根目录），`get_helper_root()` 使用 `parents[1] / "ocr_onnx_py"`
- **过滤阈值**：`score > 0.5`、`len > 2`、`max_ocr_boxes=4`、`max_return_texts=2` 为产品调参结果，勿随意修改
- **OCR 检测后处理依赖**：`ocr_onnx_py/text_det.py` 的 DB 后处理 `unclip()` 依赖 `pyclipper`；若日志停在 `det raw_output_shape` 且没有 `det post_boxes`，优先检查 `pyclipper` 是否可导入
- **OCR 空结果排查顺序**：GPU provider 正常但识别为空时，先看 `det post_boxes`、`OCR 检测候选框数量`、`OCR 识别候选 text/score/len` 日志，再判断是检测无框、识别为空还是过滤过严
- **料盘规格**：`ui/dialogs.py` 中的 `TRAY_SPEC_PRESETS` 定义预设；`CUSTOM_TRAY_SPEC_KEY` 表示用户自定义规格；`tray_manager` 保存每个料盘的 `spec` 键
- **服务注入**：UI 只依赖 `AppServices` 容器，不要在 `OCRApp` 中直接实例化底层 manager；`OCRApp(services=None)` 仍需兼容，并自动调用 `AppServices.create_default()`
- **样式集中管理**：所有 QSS 字面量统一放在 `ui/styles.py`，界面逻辑仅引用样式常量，不要将主题或皮肤修改散落到业务代码
- **实时识别线程**：`LiveInspectionWorker` 通过 `CameraWorker.current_frame_bgr` 取帧，连续 3 帧状态一致才确认；手动/自动模式均通过 `confirm_move()` 解锁下一槽位

## 编码规范

- 使用 4 空格缩进
- 模块、函数使用 `snake_case`，类使用 `CamelCase`
- Qt 事件处理函数保持现有命名风格，如 `on_tray_changed`
- 用户界面文本、日志消息、用户可见注释统一使用中文
- 槽位索引在内部逻辑中从 `0` 开始，CSV 日志和 UI 标签展示时再加 `1`
- 保持现有启动约束，尤其不要破坏 `main.py` 中的 DLL 注册流程与 `ocr/engine.py` 中的 session 复用机制

## 测试与验证

- 当前无现成自动化测试套件
- 最低验证要求为语法检查：`python -m compileall main.py ui ocr data workers ocr_onnx_py`
- 涉及 OCR/GPU 时需验证日志中出现 CUDA provider 与模型 GPU 加载信息
- 涉及 UI/流程变更时需手动检查：启动应用、切换料盘、编辑/加载模板、验证摄像头预览、执行一次 OCR 批量检测，并确认 `results/` 下生成 CSV 与图片产物
- 如新增纯逻辑单元测试，可放入新的 `tests/` 包中，文件命名为 `test_*.py`

## 提交与配置

- 提交信息延续现有中文短句风格，例如：`更新料盘 UI 与参考图获取流程`
- PR 需说明用户可见改动、列出手动验证步骤；涉及 UI 变更时附截图
- 若修改 `config/templates.json`、`onnx/` 下资源等共享资产，需要在 PR 中明确说明
- 不要提交生成文件：`logs/`、`results/`、`build/`、`dist/`、`config/app_config.json`、`config/trays_config.json`
- 不要提交本地 IDE 或 Claude 会话配置：`.idea/`、`.claude/settings.local.json`
- `config/templates.json` 视为共享初始数据，仅在默认模板集合确实变更时更新
