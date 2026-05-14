"""主窗口样式表常量集中管理。

设计动机
--------
历史上 `main_window.py` 里散落着几十上百行的 `setStyleSheet()` 字面量，
阅读和修改都很痛苦。本模块把这些 QSS 抽成带语义名称的常量，在 UI 构造时
直接引用。好处：

- UI 逻辑代码可读性大幅提升。
- 修改皮肤 / 主题时集中在本文件，不必翻 UI 逻辑。
- 新对话框等组件也可复用命名一致的样式。

注意：常量值是从 `main_window.py` 原样搬迁的，未做任何视觉调整，保证改动
视觉 0 差异。
"""

# ---------- 基础 ----------
# 主窗口：深蓝底 + 白字；QMessageBox 的 label 单独用黑字（系统弹窗偏白底）
MAIN_WINDOW = """
    QMainWindow {
        background-color: #1a1f2e;
    }
    QLabel {
        color: #ffffff;
    }
    QMessageBox QLabel {
        color: #000000;
    }
"""

# 顶部列表控制区的小标题（"料盘:" "型号:" "角度:"）
LABEL_TITLE = "color: #a1a1a6; font-size: 15px; font-weight: 600;"

# 顶部绿色强调值（实时显示的型号名 / 角度）
VALUE_HIGHLIGHT = "color: #34C759; font-size: 18px; font-weight: 700;"


# ---------- 顶部控制区 ----------
# 料盘下拉选择框
TRAY_COMBO = """
    QComboBox {
        color: #ffffff;
        background-color: rgba(42, 42, 46, 0.6);
        border: 1px solid rgba(0, 122, 255, 0.2);
        border-radius: 8px;
        padding: 6px 10px;
        font-size: 14px;
        font-weight: 500;
    }
    QComboBox:hover {
        background-color: rgba(53, 53, 57, 0.8);
    }
    QComboBox::drop-down {
        border: none;
    }
    QComboBox QAbstractItemView {
        background-color: #1a1a1e;
        color: #ffffff;
        selection-background-color: #007AFF;
    }
"""

# ＋ 新增料盘（蓝色描边）
ADD_TRAY_BTN = """
    QPushButton {
        color: #007AFF;
        background-color: rgba(0, 122, 255, 0.12);
        border: 1px solid rgba(0, 122, 255, 0.4);
        border-radius: 8px;
        padding: 6px 14px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton:hover { background-color: rgba(0, 122, 255, 0.22); }
    QPushButton:pressed { background-color: rgba(0, 122, 255, 0.32); }
"""

# － 删除料盘（红色描边，警示）
DELETE_TRAY_BTN = """
    QPushButton {
        color: #FF3B30;
        background-color: rgba(255, 59, 48, 0.12);
        border: 1px solid rgba(255, 59, 48, 0.4);
        border-radius: 8px;
        padding: 6px 14px;
        font-size: 14px;
        font-weight: 600;
    }
    QPushButton:hover { background-color: rgba(255, 59, 48, 0.22); }
    QPushButton:pressed { background-color: rgba(255, 59, 48, 0.32); }
"""


# ---------- 右侧分区 ----------
# 摄像头预览框：深色底 + 无边框圆角
CAMERA_FRAME = """
    background-color: #0a0e1a;
    border: none;
    border-radius: 8px;
"""

# 右侧分区通用外壳（"配置中心" / "任务控制"）
SECTION_FRAME = """
    QFrame {
        background-color: #1e2a49;
        border: 1px solid rgba(0, 122, 255, 0.4);
        border-radius: 8px;
        padding: 8px;
    }
"""

# 分区标题（"配置中心" / "任务控制"）
SECTION_TITLE = "color: #007AFF; font-size: 17px; font-weight: 300; letter-spacing: 0.5px;"


# ---------- 按钮 ----------
# 大号主按钮 - 蓝（"设置图像目录"）
PRIMARY_BUTTON = """
    QPushButton {
        background-color: #007AFF;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        font-size: 14px;
    }
    QPushButton:hover {
        background-color: #0A84FF;
    }
    QPushButton:pressed {
        background-color: #0062CC;
    }
"""

# 大号次主按钮 - 橙（"上传参考图片"）
WARNING_BUTTON = """
    QPushButton {
        background-color: #FF9500;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        font-size: 14px;
    }
    QPushButton:hover {
        background-color: #FFB020;
    }
    QPushButton:pressed {
        background-color: #E68800;
    }
"""

# 启动检测（渐变发光，最醒目）
START_BUTTON = """
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                   stop:0 #0D95FF, stop:1 #007AFF);
        color: #ffffff;
        border: 2px solid #0A84FF;
        border-radius: 8px;
        font-size: 16px;
        font-weight: 700;
        letter-spacing: 0px;
        box-shadow: 0 0 20px rgba(0, 122, 255, 0.6);
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                   stop:0 #1FA3FF, stop:1 #0A84FF);
        border: 2px solid #0D95FF;
        box-shadow: 0 0 30px rgba(0, 122, 255, 0.8);
    }
    QPushButton:pressed {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                   stop:0 #0062CC, stop:1 #004A99);
        border: 2px solid #003D7A;
    }
    QPushButton:disabled {
        background-color: #5a5a5f;
        color: #8e8e93;
        border: 2px solid #4d4d52;
        box-shadow: none;
    }
"""

# 刷新（灰色半透明）
REFRESH_BUTTON = """
    QPushButton {
        background-color: rgba(90, 90, 95, 0.6);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.2);
        border-radius: 8px;
        font-weight: 600;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: rgba(99, 99, 104, 0.8);
    }
    QPushButton:pressed {
        background-color: rgba(77, 77, 82, 0.9);
    }
"""

# 灰度图像切换按钮
GRAYSCALE_TOGGLE_BUTTON = """
    QPushButton {
        background-color: rgba(90, 90, 95, 0.58);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.22);
        border-radius: 8px;
        font-weight: 600;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: rgba(99, 99, 104, 0.82);
    }
    QPushButton:checked {
        background-color: rgba(10, 132, 255, 0.30);
        border: 1px solid rgba(10, 132, 255, 0.95);
    }
    QPushButton:checked:hover {
        background-color: rgba(10, 132, 255, 0.42);
    }
"""

# 访问历史数据（绿色）
HISTORY_BUTTON = """
    QPushButton {
        background-color: rgba(52, 199, 89, 0.25);
        color: #ffffff;
        border: 1px solid rgba(52, 199, 89, 0.8);
        border-radius: 8px;
        font-weight: 600;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: rgba(52, 199, 89, 0.35);
    }
    QPushButton:pressed {
        background-color: rgba(52, 199, 89, 0.45);
    }
"""

# 退出（深红）
EXIT_BUTTON = """
    QPushButton {
        background-color: #8B4C47;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        font-size: 13px;
    }
    QPushButton:hover {
        background-color: #A55A54;
    }
    QPushButton:pressed {
        background-color: #6B3A36;
    }
"""


# ---------- 配置中心内的控件 ----------
# 配置中心内的标签（"型号:" / "角度:"）
PARAM_LABEL = "color: #a1a1a6; font-size: 13px; font-weight: 500; min-width: 36px;"

# 配置中心内的单行输入框（型号 / 角度）
PARAM_INPUT = """
    QLineEdit {
        color: #ffffff;
        background-color: #2a2a2e;
        border: 1px solid #007AFF;
        border-radius: 8px;
        padding: 5px 9px;
        font-size: 13px;
        font-weight: 500;
        selection-background-color: #007AFF;
    }
    QLineEdit:focus {
        border: 2px solid #007AFF;
        padding: 4px 8px;
    }
"""


# ---------- 历史数据预览 ----------
# 历史 CSV 预览
CSV_VIEWER = """
    QTextEdit {
        color: #ffffff;
        background-color: #151b2c;
        border: 1px solid rgba(0, 122, 255, 0.35);
        border-radius: 6px;
        font-family: Consolas, 'Courier New', monospace;
        font-size: 13px;
    }
"""

# 图片预览右上角悬浮按钮（全屏/缩小/退出）
OVERLAY_BUTTON = """
    QPushButton {
        background-color: rgba(0, 0, 0, 0.50);
        color: #ffffff;
        border: 1px solid rgba(255, 255, 255, 0.25);
        border-radius: 6px;
        padding: 6px 14px;
        font-size: 13px;
        font-weight: 600;
    }
    QPushButton:hover { background-color: rgba(0, 0, 0, 0.72); }
    QPushButton:pressed { background-color: rgba(0, 0, 0, 0.85); }
"""

# 实时识别入口按钮（紫色，与蓝色"开始检测"区分）
LIVE_BUTTON = """
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                   stop:0 #BF5AF2, stop:1 #9B59B6);
        color: #ffffff;
        border: 2px solid #BF5AF2;
        border-radius: 8px;
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 0px;
    }
    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                   stop:0 #D070FF, stop:1 #AF6ED6);
        border: 2px solid #D070FF;
    }
    QPushButton:pressed {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                   stop:0 #8A4CB2, stop:1 #6A3090);
        border: 2px solid #6A3090;
    }
    QPushButton:disabled {
        background-color: #5a5a5f;
        color: #8e8e93;
        border: 2px solid #4d4d52;
    }
"""

# 实时识别模式下拉（复用 TRAY_COMBO 的风格，但宽度更窄）
LIVE_MODE_COMBO = """
    QComboBox {
        color: #ffffff;
        background-color: rgba(42, 42, 46, 0.6);
        border: 1px solid rgba(191, 90, 242, 0.4);
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 13px;
        font-weight: 500;
    }
    QComboBox:hover { background-color: rgba(53, 53, 57, 0.8); }
    QComboBox::drop-down { border: none; }
    QComboBox QAbstractItemView {
        background-color: #1a1a1e;
        color: #ffffff;
        selection-background-color: #BF5AF2;
    }
"""
