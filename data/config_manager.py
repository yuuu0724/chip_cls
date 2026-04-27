"""应用配置管理器 - 读写 `config/app_config.json`。

存放不随某个料盘变化的应用级偏好：图像目录、输出目录、摄像头 ID 等。
和料盘绑定的配置（型号、角度、规格）请看 `TrayManager`。
"""
import json
import os
import sys


def _app_root():
    """返回应用根目录。

    - 冻结态（PyInstaller 打包后）：返回 exe 所在目录；
    - 开发态：返回 ``data/`` 的父目录，也就是项目根。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigManager:
    """读写应用级配置。

    配置文件位于 ``<app_root>/config/app_config.json``。首次启动若文件不存在，
    会用 `DEFAULT_CONFIG` 生成一份并写盘。
    """

    # 配置文件路径（类级常量）
    CONFIG_FILE = os.path.join(_app_root(), "config", "app_config.json")

    # 默认配置：首次运行或文件损坏时的 fallback
    DEFAULT_CONFIG = {
        "image_directory": None,   # 用户指定的图像目录；必须手动设置否则不能开始检测
        "output_directory": "output",  # 结果输出目录（保留字段，当前主要用 DataLogger.base_dir）
        "auto_detect": False,      # 预留开关：未来可实现上传即检测
        "camera_id": 0,            # 摄像头索引，0 是内置，1/2 是外接
    }

    def __init__(self):
        """启动时先拷贝默认配置，再覆盖从磁盘读到的值。"""
        self.config = self.DEFAULT_CONFIG.copy()
        self.load_config()

    def load_config(self):
        """从 JSON 读配置并 merge 到内存字典。

        - 文件不存在：写一份默认配置下去；
        - 文件存在但解析失败：保留默认配置，打印警告（不抛异常，避免崩溃启动流程）。
        """
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # 用 update 而不是替换，保证将来新增的默认字段不会丢失
                    self.config.update(loaded)
            except Exception as e:
                print(f"[警告] 加载配置失败: {e}，使用默认配置")
        else:
            self.save_config()

    def save_config(self):
        """把当前内存配置落盘。写盘失败只打印错误，不抛。"""
        try:
            os.makedirs(os.path.dirname(self.CONFIG_FILE), exist_ok=True)
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[错误] 保存配置失败: {e}")

    def set_image_directory(self, directory):
        """设置图像目录并立即落盘。

        Parameters
        ----------
        directory : str
            目录路径；必须是已存在的目录，否则返回 False 且不做变更。

        Returns
        -------
        bool
            True 表示写入成功。
        """
        if directory and os.path.isdir(directory):
            self.config["image_directory"] = directory
            self.save_config()
            return True
        return False

    def get_image_directory(self):
        """返回已保存的图像目录；目录若不存在返回 None。"""
        image_dir = self.config.get("image_directory")
        if image_dir and os.path.isdir(image_dir):
            return image_dir
        return None

    def set_output_directory(self, directory):
        """设置输出目录（不存在则创建）并落盘。"""
        if directory:
            os.makedirs(directory, exist_ok=True)
            self.config["output_directory"] = directory
            self.save_config()
            return True
        return False

    def get_output_directory(self):
        """返回输出目录，不存在时自动创建。默认 ``output``。"""
        output_dir = self.config.get("output_directory", "output")
        os.makedirs(output_dir, exist_ok=True)
        return output_dir

    def is_image_directory_set(self):
        """图像目录是否已合法设置（存在的目录）。"""
        image_dir = self.get_image_directory()
        return image_dir is not None

    def get_config(self):
        """返回配置副本，避免外部直接修改内部 dict。"""
        return self.config.copy()

    def reset_to_default(self):
        """重置为默认配置并落盘（用于"恢复出厂"按钮等）。"""
        self.config = self.DEFAULT_CONFIG.copy()
        self.save_config()
