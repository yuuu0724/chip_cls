# ChipOCR - AI 芯片料盘视觉检测系统

ChipOCR 是一个 PySide6 桌面应用，用于对芯片料盘进行视觉 OCR 检测，并按槽位标记结果：

- 正常
- 方向错误
- 型号错误
- 空
- 识别失败

系统支持批量图片检测和摄像头实时逐槽位检测。实时检测流程已接入杰美康运动控制器，通过 Modbus RTU 控制 XYZ 三轴自动移槽。

## 主要功能

- OCR 检测：基于 ONNX Runtime 加载 det、cls、rec 模型，识别芯片字符和方向。
- 模板管理：支持本地图片上传或摄像头拍摄参考图，OCR 提取型号与角度后由用户确认。
- 批量检测：按料盘槽位顺序读取图片，生成 CSV 结果和界面截图。
- 实时识别：摄像头采集当前槽位，连续多帧状态一致后确认结果。
- 自动移槽：实时识别时按料盘原点、行列规格、横纵间距自动移动到下一个槽位。
- 料盘配置：支持 `3x7`、`4x6`、`2x10` 和自定义行列；保存横纵间距、槽位原点和光源配置。
- 运动控制：通过 Modbus RTU 控制杰美康 XYZ 三轴，支持机械回零、相对脉冲运动、软限位和当前位置反馈。
- 历史回溯：可在界面中查看历史 CSV 和截图。

## 运行环境

建议使用项目验证过的 Python 3.12 conda 环境。

运行系统：

- Windows
- Python 3.12 优先
- NVIDIA GPU + CUDA 12.x + cuDNN 9.x 可启用 GPU 推理
- 无 GPU 时可改用 CPU 版 ONNX Runtime

硬件通信：

- 协议：Modbus RTU
- 默认串口：`COM14`
- 从站地址：`2`
- 波特率：`9600`
- 数据位：`8`
- 校验位：`N`
- 停止位：`1`
- 超时：`1s`

## 安装与启动

```powershell
python -m pip install -r requirements.txt
python main.py
```

启动后程序会先连接运动控制器并触发机械回零。回零完成前主界面保持锁定；如果连接或回零失败，界面会提示重试或关闭。

## 基本使用流程

1. 启动软件，等待机械回零完成。
2. 选择已有料盘，软件会自动移动到该料盘原点。
3. 如需新增料盘，点击“新增料盘”：
   - 软件先自动回到机械原点。
   - 输入料盘编号。
   - 选择或输入行列规格。
   - 输入横向/纵向间距，支持 mm 或脉冲。
   - 使用弹窗内 XYZ 点动把摄像头移动到首槽中心。
   - 点击“获取当前坐标（设为原点）”，保存该料盘原点。
4. 上传参考图片或使用摄像头拍摄参考图，确认型号和角度。
5. 点击“开始检测”执行批量图片检测，或点击“实时识别”执行摄像头自动移槽检测。
6. 在“访问历史数据”中查看生成的 CSV 和截图。

## 实时识别流程

实时识别不再需要人工确认移槽。

流程如下：

1. 点击“实时识别”。
2. 软件自动移动到当前料盘第 1 个槽位原点。
3. 摄像头采集当前槽位，OCR 连续多帧判断状态。
4. 当前槽位识别完成后，软件自动移动到下一个槽位。
5. 识别顺序为从左到右、从上到下。
6. 全部槽位完成后，软件自动返回当前料盘原点。
7. 生成 CSV 和界面截图。

槽位坐标计算：

```text
row = slot_index // cols
col = slot_index % cols

slot_x = origin_x + col * x_pitch
slot_y = origin_y + row * y_pitch
slot_z = origin_z
```

控制器只支持相对运动，因此移动时会先读取当前位置，再计算 delta 后下发相对脉冲。

## Modbus 运动控制

运动控制实现位于：

```text
motion/modbus_motion_controller.py
```

D 寄存器地址换算：

```python
def d_addr(d_number: int) -> int:
    return 7040 + (d_number - 99) * 2
```

关键寄存器：

```text
D99  机械回零触发

Z轴：
D100 触发
D101 相对运动脉冲
D102 当前位置反馈
D103 速度

Y轴：
D110 触发
D111 相对运动脉冲
D112 当前位置反馈
D113 速度

X轴：
D120 触发
D121 相对运动脉冲
D122 当前位置反馈
D123 速度
```

32 位数据采用标准有符号整数，低 16 位在前，高 16 位在后。不要使用 32768 作为进位基数。

```python
def split_s32_to_words(value: int) -> tuple[int, int]:
    raw32 = value & 0xFFFFFFFF
    low_word = raw32 & 0xFFFF
    high_word = (raw32 >> 16) & 0xFFFF
    return low_word, high_word

def combine_s32_from_words(low_word: int, high_word: int) -> int:
    raw32 = ((high_word & 0xFFFF) << 16) | (low_word & 0xFFFF)
    if raw32 >= 0x80000000:
        raw32 -= 0x100000000
    return raw32
```

单位换算：

```text
X轴：1 mm = 1000 脉冲
Y轴：1 mm = 500 脉冲
Z轴：1 mm = 2000 脉冲
```

软限位：

```text
X轴：0 ~ 230000
Y轴：0 ~ 210000
Z轴：-2000 ~ 60000
```

## 项目结构

```text
chipocr/
├── main.py              桌面入口，先注册 NVIDIA DLL 路径再启动 PySide6
├── ui/                  主窗口、弹窗、料位组件和集中式 QSS 样式
├── workers/             摄像头预览、批量检测、实时识别线程
├── motion/              Modbus RTU 三轴运动控制
├── ocr/                 OCR 引擎、模板管理和结果判定
├── data/                配置、日志、料盘管理和 AppServices 服务容器
├── ocr_onnx_py/         ONNX OCR 底层辅助模块
├── onnx/                det、cls、rec 模型资源
├── config/              默认模板和本地配置目录
├── logs/                运行日志，自动生成
└── results/             检测 CSV 和截图产物，自动生成
```

## 配置文件

运行时配置默认写入：

```text
config/app_config.json
config/trays_config.json
```

常用配置：

- `modbus_port`：默认 `COM14`
- `modbus_slave_id`：默认 `2`
- `camera_id`：摄像头索引
- `post_home_x_position` / `post_home_y_position` / `post_home_z_position`：机械回零完成后三轴移动到的开发者配置脉冲位置；X/Y 默认为 `null` 不移动，Z 默认 `-170000`
- `image_directory`：批量检测图片目录

启动 logo：

- `config/logo.png`：程序启动时显示的静态 logo。客户可直接替换同名 PNG 文件，重启程序后生效。

料盘配置包含：

- 料盘编号
- 行数、列数
- 横向/纵向间距脉冲
- 首槽原点 X/Y/Z 累计脉冲
- 绑定模板型号和角度
- 光源配置

## 开发验证

当前仓库没有 pytest 或 lint 配置。提交前至少执行：

```powershell
python -m compileall main.py ui ocr data workers ocr_onnx_py motion
```

涉及 OCR/GPU 时，检查日志中是否出现 CUDA provider 和模型加载信息。

涉及运动控制时，至少手动验证：

- 启动后机械回零完成前界面锁定
- 切换料盘后自动移动到该料盘原点
- 新增料盘前自动回到机械原点
- 新增料盘弹窗内 XYZ 点动可用
- 获取当前坐标后原点写入正确
- 实时识别按从左到右、从上到下自动移槽
- 实时识别结束后返回料盘原点

## 运行产物

以下内容属于本地运行产物，通常不提交：

```text
logs/
results/
build/
dist/
config/app_config.json
config/trays_config.json
```

`config/templates.json` 是共享初始模板数据，仅在默认模板集合确实变更时提交。

## 许可

仅供内部使用。
