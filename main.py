"""应用启动入口"""
import sys
import os

# 添加项目根目录到 sys.path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from ui.main_window import OCRApp
from PySide6.QtWidgets import QApplication


def main():
    """启动应用"""
    app = QApplication(sys.argv)
    window = OCRApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
