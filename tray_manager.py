# tray_manager.py
import json
import os

class TrayManager:
    """
    管理不同料盘的配置信息
    每个料盘可以装入不同型号的芯片
    """
    
    def __init__(self, config_file="trays_config.json"):
        self.config_file = config_file
        self.trays = self.load_trays()
    
    def load_trays(self):
        """加载料盘配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载料盘配置失败: {e}")
                return self.get_default_trays()
        return self.get_default_trays()
    
    def get_default_trays(self):
        """获取默认料盘配置"""
        return {
            "A0001": {
                "name": "料盘 A0001",
                "description": "标准LQFP单位料盘",
                "model": "ATMLH904",
                "angle": 90,
                "status": "可用"
            },
            "A0002": {
                "name": "料盘 A0002",
                "description": "BGA专用料盘",
                "model": "MAX485",
                "angle": 0,
                "status": "可用"
            },
            "A0003": {
                "name": "料盘 A0003",
                "description": "SOIC单位料盘",
                "model": "24C02BN",
                "angle": 90,
                "status": "可用"
            }
        }
    
    def save_trays(self):
        """保存料盘配置"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.trays, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存料盘配置失败: {e}")
            return False
    
    def get_tray_list(self):
        """获取所有料盘列表"""
        return list(self.trays.keys())
    
    def get_tray_info(self, tray_id):
        """获取料盘详细信息"""
        return self.trays.get(tray_id)
    
    def get_tray_model_and_angle(self, tray_id):
        """获取料盘对应的型号和角度"""
        tray = self.trays.get(tray_id)
        if tray:
            return tray.get("model"), tray.get("angle")
        return None, None
    
    def add_tray(self, tray_id, name, description, model, angle):
        """添加新料盘"""
        self.trays[tray_id] = {
            "name": name,
            "description": description,
            "model": model,
            "angle": angle,
            "status": "可用"
        }
        self.save_trays()
        return True
    
    def update_tray(self, tray_id, **kwargs):
        """更新料盘配置"""
        if tray_id in self.trays:
            self.trays[tray_id].update(kwargs)
            self.save_trays()
            return True
        return False
    
    def delete_tray(self, tray_id):
        """删除料盘"""
        if tray_id in self.trays and tray_id != "A0001":  # 不允许删除默认料盘
            del self.trays[tray_id]
            self.save_trays()
            return True
        return False
