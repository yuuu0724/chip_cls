# 🔍 AI 芯片料盘视觉检测系统

基于 PySide6 + PaddleOCR 的全自动芯片极性识别系统，支持 21 个料位的并行检测。

## 📋 系统要求

- **Python**: 3.10+

## 🚀 快速开始

### 1️⃣ 安装依赖
```bash
pip install -r requirements.txt
```

### 2️⃣ 运行应用
```bash
python main_gui.py
```

### 3️⃣ 使用流程
1. 点击「配置中心」→「设置图像目录」选择图像文件夹
2. 点击蓝色「开始检测」按钮开始识别
3. 实时显示 21 个料位的检测结果

## 📁 核心目录

```
OCR-Proj/
├── demo/                 # 主应用
│   ├── main_gui.py      # UI 界面
│   ├── ocr_engine.py    # OCR 引擎
│   └── *.py            # 其他模块
├── model/onnx/         # ONNX 模型（DET/CLS/REC）
├── predict.py          # 独立推理脚本
└── requirements.txt    # 依赖列表
```

## ✨ 功能特性

✅ 三阶段 OCR（检测→分类→识别）  
✅ 实时摄像头预览  
✅ 21 个料位并行检测  
✅ 模板自动匹配  
✅ 结果自动导出 CSV  
✅ 深色主题 UI  



