# ChipOCR — AI 芯片料盘视觉检测系统

基于 ONNX Runtime + PySide6 的桌面应用，对芯片料盘图像执行 OCR 识别，自动判定每个槽位的芯片型号与方向是否正确。

## 功能

- 批量检测：按料盘槽位逐张识别，结果标记为 正常 / 方向错误 / 型号错误 / 空
- 动态料盘规格：支持 3×7、4×6、2×10 等多种布局，切换料盘自动适配网格
- 模板管理：通过本地图片或摄像头拍摄设置参考型号，OCR 自动提取型号与角度
- 摄像头实时预览
- 历史数据查看：CSV 表格 + 界面截图回溯

## 环境要求

- Python 3.10+
- NVIDIA GPU + 驱动（支持 CUDA 12.x），无 GPU 自动回退 CPU

## 快速开始

```bash
pip install -r requirements.txt
python main.py
```

GPU 加速需额外安装 CUDA 运行时库：

```bash
pip install nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-nvjitlink-cu12
```

## 项目结构

```
chipocr/
├── main.py              入口
├── ocr/                 OCR 引擎（检测 → 分类 → 识别）
├── data/                配置与日志持久化
├── ui/                  PySide6 界面
├── workers/             后台线程（摄像头、批量检测）
├── ocr_onnx_py/         ONNX 推理辅助模块
├── onnx/                模型文件（det/cls/rec）
└── config/              运行时配置
```

## 许可

仅供内部使用。
