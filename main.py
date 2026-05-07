"""应用主入口。

负责一系列"必须在任何业务 import 之前完成"的启动前置：

1. 定位应用根目录（开发态 vs PyInstaller 冻结态）；
2. 把根目录加入 ``sys.path`` 以保证 ``ocr/`` ``ui/`` 等模块可导入；
3. 注册 NVIDIA CUDA DLL 目录，让 ``onnxruntime`` 能找到 GPU 运行库；
4. 首次冻结态启动时，把打包进来的默认配置文件释放到用户可编辑位置；
5. 配置全局日志；
6. 启动 Qt 事件循环。

顺序十分敏感：NVIDIA DLL 注册必须早于 `onnxruntime` 首次导入，否则 GPU
provider 会因找不到 DLL 而静默 fallback 到 CPU。因此 ``ui.main_window`` 的
import 放在文件底部而不是顶部。
"""

import logging
import os
import sys
from datetime import datetime


def get_app_root():
    """返回应用根目录。

    - 冻结态（PyInstaller 打包）：exe 所在目录；
    - 开发态：本文件（``main.py``）所在目录，即仓库根。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# 冻结态下 cwd 可能是 PyInstaller 的 _MEIPASS 或用户随手双击的位置，
# 强制切到 exe 所在目录，后续所有相对路径（config/、results/、logs/）才稳定。
project_root = get_app_root()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if getattr(sys, "frozen", False):
    os.chdir(project_root)


def _register_nvidia_dll_dirs():
    """把 CUDA DLL 所在目录注册到进程 DLL 搜索路径。

    背景
    ----
    Windows 上 pip/conda 安装的 CUDA wheel 把 DLL 放在
    ``site-packages/nvidia/*/bin``，该路径默认不在 DLL 搜索路径里。
    必须同时 ``os.add_dll_directory`` + 前置 PATH —— 因为 onnxruntime 内部
    的 ``LoadLibrary`` 并不保证尊重 ``add_dll_directory``。

    ⚠️ 必须早于任何可能触发 `onnxruntime` 导入的代码调用，
    否则 CUDA provider 会失败并静默退化到 CPU。详见 gpu_fix_log.md。
    """
    # Windows 上 pip/conda 安装的 CUDA wheel 把 DLL 放在 site-packages/nvidia/*/bin，
    # 该路径不在默认 DLL 搜索路径里。必须同时 os.add_dll_directory + 前置 PATH，
    # 因为 ORT 内部的 LoadLibrary 不保证尊重 add_dll_directory。必须早于任何可能
    # 触发 onnxruntime 导入的代码。参见 gpu_fix_log.md。
    if sys.platform != "win32":
        return

    candidates = []
    # 冻结：PyInstaller 可能把 CUDA DLL 摊到 _MEIPASS 根目录，
    # 也可能保留 _MEIPASS/nvidia/<lib>/bin/ 子目录布局，两路都注册。
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = sys._MEIPASS
        # 注册顺序就是 DLL 搜索优先级：先 nvidia/* wheel（dev 验证过工作），
        # 再 ORT 自带的 capi/，最后 _MEIPASS 根作为 PATH 兜底。
        nvidia_root = os.path.join(meipass, "nvidia")
        if os.path.isdir(nvidia_root):
            for name in os.listdir(nvidia_root):
                bin_dir = os.path.join(nvidia_root, name, "bin")
                if os.path.isdir(bin_dir):
                    candidates.append(bin_dir)
        capi_dir = os.path.join(meipass, "onnxruntime", "capi")
        if os.path.isdir(capi_dir):
            candidates.append(capi_dir)
        candidates.append(meipass)

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


def _redirect_stderr_in_frozen():
    """冻结态 (PyInstaller console=False) 下 sys.stderr 是 None，
    导致 ONNX Runtime / CUDA C++ 层的诊断输出彻底丢失。
    把 stderr 重定向到 logs/stderr_YYYYMMDD.log，方便排查 GPU 加载问题。
    """
    if not getattr(sys, "frozen", False):
        return
    try:
        log_dir = os.path.join(project_root, "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"stderr_{datetime.now():%Y%m%d}.log")
        f = open(path, "a", encoding="utf-8", buffering=1)
        sys.stderr = f
        # native code 直接写 fd=2 的也要捕获 (ORT 内部 std::cerr / printf)
        try:
            os.dup2(f.fileno(), 2)
        except (OSError, AttributeError, ValueError):
            pass
    except Exception:
        pass


_redirect_stderr_in_frozen()


def _init_user_config():
    """冻结态首次启动：把打包的默认 JSON 释放到用户可编辑目录。

    PyInstaller 把 ``config/*.json`` 一起打包进 ``_MEIPASS``，但这些文件
    在每次启动都会被临时释放/清理，用户编辑不会持久。
    这里把它们 copy 到 exe 同级的 ``config/`` 下，实现"首次运行有默认配置、
    后续保留用户修改、重装 exe 不会覆盖已有配置"的行为。

    开发态直接 return（工程根下本来就有 ``config/``）。
    """
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
    """配置根 logger：同时输出到文件和控制台。

    - 文件：``logs/app_YYYYMMDD.log``，按天一个文件，DEBUG 全量；
    - 控制台：INFO 级别，避免刷屏；
    - 格式：``时间 | 级别 | logger 名 | 消息``，便于事后 grep 定位来源；
    - 清空已有 handlers，防止反复调用（例如 pytest 场景）把日志双写。

    只在应用启动时调用一次。
    """
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
    """应用入口：初始化日志 → 建 QApplication → show 主窗口 → 进事件循环。

    `sys.exit(app.exec())` 会阻塞直到用户关窗，之后用 app.exec() 的返回码
    退出进程（0 = 正常）。
    """
    setup_logging()
    logging.info("应用启动, project_root=%s", project_root)

    app = QApplication(sys.argv)
    window = OCRApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
