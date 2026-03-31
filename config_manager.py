"""
应用配置管理器 - 管理用户配置和设置
"""
import json
import os
from pathlib import Path


class ConfigManager:
    """应用配置管理器"""
    
    CONFIG_FILE = "app_config.json"
    
    DEFAULT_CONFIG = {
        "image_directory": None,  # 用户指定的图像目录
        "output_directory": "output",  # 结果输出目录
        "auto_detect": False,  # 自动检测开关
        "camera_id": 0,  # 摄像头 ID
    }
    
    def __init__(self):
        self.config = self.DEFAULT_CONFIG.copy()
        self.load_config()
    
    def load_config(self):
        """从文件加载配置"""
        if os.path.exists(self.CONFIG_FILE):
            try:
                with open(self.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self.config.update(loaded)
            except Exception as e:
                print(f"[警告] 加载配置失败: {e}，使用默认配置")
        else:
            self.save_config()
    
    def save_config(self):
        """保存配置到文件"""
        try:
            with open(self.CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[错误] 保存配置失败: {e}")
    
    def set_image_directory(self, directory):
        """设置图像目录"""
        if directory and os.path.isdir(directory):
            self.config["image_directory"] = directory
            self.save_config()
            return True
        return False
    
    def get_image_directory(self):
        """获取图像目录"""
        image_dir = self.config.get("image_directory")
        if image_dir and os.path.isdir(image_dir):
            return image_dir
        return None
    
    def set_output_directory(self, directory):
        """设置输出目录"""
        if directory:
            os.makedirs(directory, exist_ok=True)
            self.config["output_directory"] = directory
            self.save_config()
            return True
        return False
    
    def get_output_directory(self):
        """获取输出目录"""
        output_dir = self.config.get("output_directory", "output")
        os.makedirs(output_dir, exist_ok=True)
        return output_dir
    
    def is_image_directory_set(self):
        """检查图像目录是否已设置"""
        image_dir = self.get_image_directory()
        return image_dir is not None
    
    def get_config(self):
        """获取完整配置"""
        return self.config.copy()
    
    def reset_to_default(self):
        """重置为默认配置"""
        self.config = self.DEFAULT_CONFIG.copy()
        self.save_config()
