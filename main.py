"""主入口"""
import logging
import os
import sys
from datetime import datetime


def get_app_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


project_root = get_app_root()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if getattr(sys, "frozen", False):
    os.chdir(project_root)


def _register_nvidia_dll_dirs():
    # Windows 上 pip/conda 安装的 CUDA wheel 把 DLL 放在 site-packages/nvidia/*/bin，
    # 该路径不在默认 DLL 搜索路径里。必须同时 os.add_dll_directory + 前置 PATH，
    # 因为 ORT 内部的 LoadLibrary 不保证尊重 add_dll_directory。必须早于任何可能
    # 触发 onnxruntime 导入的代码。参见 gpu_fix_log.md。
    if sys.platform != "win32":
        return

    candidates = []
    # 冻结：PyInstaller 把 CUDA DLL 拍到 _MEIPASS 根目录
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(sys._MEIPASS)

    for root in {sys.prefix, sys.base_prefix}:
        nvidia_root = os.path.join(root, "Lib", "site-packages", "nvidia")
        if not os.path.isdir(nvidia_root):
            continue
        for name in os.listdir(nvidia_root):
            bin_dir = os.path.join(nvidia_root, name, "bin")
            if os.path.isdir(bin_dir):
                candidates.append(bin_dir)

    for bin_dir in candidates:
        try:
            os.add_dll_directory(bin_dir)
        except OSError:
            pass
        cur_path = os.environ.get("PATH", "")
        if bin_dir not in cur_path:
            os.environ["PATH"] = bin_dir + os.pathsep + cur_path


_register_nvidia_dll_dirs()


def _init_user_config():
    # 冻结态：把 _MEIPASS/config 里打包的默认 JSON 复制一份到 exe 同级 config/，
    # 让用户能编辑且重装 exe 不覆盖已有配置。
    if not (getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")):
        return
    bundled = os.path.join(sys._MEIPASS, "config")
    user = os.path.join(project_root, "config")
    if not os.path.isdir(bundled):
        return
    os.makedirs(user, exist_ok=True)
    import shutil
    for name in os.listdir(bundled):
        src = os.path.join(bundled, name)
        dst = os.path.join(user, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)


_init_user_config()


def setup_logging():
    """配置日志：同时输出到文件和控制台。"""
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"app_{datetime.now():%Y%m%d}.log")

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    logging.info("日志已初始化 => %s", log_file)


from ui.main_window import OCRApp
from PySide6.QtWidgets import QApplication


def main():
    """主入口"""
    setup_logging()
    logging.info("应用启动, project_root=%s", project_root)

    app = QApplication(sys.argv)
    window = OCRApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
