# ChipOCR - AI 芯片料盘视觉检测系统

ChipOCR 是一个基于 PySide6、ONNX Runtime 和 OCR 模型的桌面检测工具，用于对芯片料盘图像执行识别，并按槽位判定芯片型号与方向是否符合当前模板配置。

## 功能特性

- 批量检测：按料盘槽位逐张识别，结果标记为正常、方向错误、型号错误、空、识别失败。
- 实时识别：通过摄像头逐槽位采集，连续多帧状态一致后确认结果，支持手动确认移动到下一槽位。
- 动态料盘规格：内置 `3x7`、`4x6`、`2x10` 规格，并支持自定义行列配置。
- 模板管理：支持本地图片上传或摄像头拍摄参考图，OCR 自动提取型号与方向角度后由用户确认。
- 摄像头预览：主界面右侧显示实时预览，画面按比例缩放，避免拉伸变形。
- 历史回溯：检测结果输出 CSV 与截图，可在界面中查看历史表格和图片。
- 配置持久化：保存图像目录、输出目录、摄像头索引、料盘规格、模板型号与角度。

## 环境要求

- Python 3.12 conda 环境优先，Python 3.10+ 可作为兼容目标。
- Windows 桌面环境。
- NVIDIA GPU + CUDA 12.x 运行时可启用 GPU 推理；无 GPU 时可回退 CPU。

## 快速开始

```powershell
python -m pip install -r requirements.txt
python main.py
```

如需 GPU 加速，请确保 CUDA 相关运行时库和 ONNX Runtime GPU provider 可用。启动后可在 `logs/app_YYYYMMDD.log` 中查看 provider 与模型加载信息。

## 基本流程

1. 启动应用后选择或新增料盘。
2. 在配置中心设置图像目录。
3. 上传参考图片或使用摄像头拍摄参考图，确认 OCR 提取的型号与角度。
4. 点击开始检测执行批量 OCR，或点击实时识别进入摄像头逐槽位检测流程。
5. 在历史数据中查看生成的 CSV 与图片结果。

## 项目结构

```text
chipocr/
├── main.py              桌面入口，注册 NVIDIA DLL 路径并启动 PySide6 应用
├── ui/                  主窗口、料位组件、对话框与集中式 QSS 样式
├── workers/             摄像头预览、批量检测、实时识别与 RS232 预留线程
├── ocr/                 OCR 引擎、模板管理与结果判定逻辑
├── data/                配置、日志、料盘管理与 AppServices 服务容器
├── ocr_onnx_py/         底层 ONNX OCR 推理辅助模块
├── onnx/                det、cls、rec 模型资源
├── config/              默认模板与运行配置目录
├── logs/                运行日志，自动生成
└── results/             检测 CSV 与截图产物，自动生成
```

## 开发验证

当前项目未提交 pytest 或 lint 配置。提交前至少执行语法检查：

```powershell
python -m compileall main.py ui ocr data workers ocr_onnx_py
```

涉及 OCR/GPU 变更时，需确认日志中出现 CUDA provider 与模型加载信息；涉及 UI 或流程变更时，建议手动验证启动应用、切换料盘、编辑模板、摄像头预览、批量检测和结果文件生成。

## 运行产物

- `logs/`：按日期生成应用日志。
- `results/`：生成检测 CSV 和截图。
- `config/app_config.json`、`config/trays_config.json`：本地运行配置。

这些文件属于本地运行产物，通常不提交到仓库。

## 许可

仅供内部使用。
