# CLAUDE.md

本文件为 Claude Code 在此仓库工作时的指导说明。

## 项目概览

PySide6 (Qt) 桌面应用——"AI 芯片料盘视觉检测系统"。对��片料盘图像执行 OCR，将每个槽位标记为 **正常 / 方向错误 / 型号错误 / 空 / 识别失败**。料盘规格支持 3×7、4×6、2×10 等动态配置。

## 运行方式

```bash
pip install -r requirements.txt
python main.py
```

无测试套件、无 lint 配置。日志输出到 `logs/app_YYYYMMDD.log`。

## 目录结构

```
chipocr/
├── main.py                 入口，配置日志 + 注册 NVIDIA DLL + 启动 Qt
├── ocr/                    OCR 流水线
│   ├── engine.py           ONNX 推理引擎（cls→det→rec）
│   ├── template_manager.py 型号模板管理（config/templates.json）
│   └── logic_controller.py 检测结果判定逻辑
├── data/                   持久化
│   ├── config_manager.py   应用配置（config/app_config.json）
│   ├── tray_manager.py     料盘配置（config/trays_config.json）
│   └── logger.py           CSV 日志 + 界面截图
├── ui/                     PySide6 界面
│   ├── main_window.py      主窗口（OCRApp）
│   ├── material_slot.py    单个料位组件
│   └── dialogs.py          对话框（模板确认、摄像头拍摄、新增料盘）
├── workers/                QThread 后台线程
│   ├── camera_worker.py    摄像头预览
│   └── control_worker.py   批量检测运行器
├── ocr_onnx_py/            OCR 辅助包（text_det/cls/rec + ONNX session 管理）
├── onnx/                   ONNX 模型文件（det/cls/rec）
├── config/                 配置文件目录
│   └── templates.json      型号模板（随代码提交）
└── requirements.txt
```

## 关键架构约束

- **ONNX Session 类级共享**：`OCREngine._shared_detector/classifier/recognizer` 跨实例复用，不可重构为实例级状态
- **DLL 注册顺序**：`main.py` 和 `ocr_onnx_py/session_utils.py` 中必须先注册 NVIDIA DLL 目录，再 `import onnxruntime`
- **路径解析**：`engine.py` 中 `get_resource_root()` 用 `parents[1]`（= chipocr/），`get_helper_root()` 同样用 `parents[1] / "ocr_onnx_py"`
- **过滤阈值**：`score > 0.5`、`len > 2`、`max_ocr_boxes=4`、`max_return_texts=2` 是产品调参结果，勿随意修改
- **料盘规格**：`TRAY_SPEC_PRESETS`（ui/dialogs.py）定义可用规格，tray_manager 存储每个料盘的 spec 键

## 代码规范

- 用户界面字符串、日志消息、注释均使用中文
- 槽位索引内部从 0 开始，CSV 日志和 UI 标签显示时加 1
