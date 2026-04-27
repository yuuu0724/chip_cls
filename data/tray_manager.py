"""料盘配置管理 - 读写 `config/trays_config.json`。

每块物理料盘对应一条记录：

    {
        "A0001": {
            "name": "料盘 A0001",
            "description": "标准LQFP单位料盘",
            "model": "ATMLH904",   # 当前装入的芯片型号
            "angle": 90,           # 芯片正方向角度
            "spec": "3x7",         # 行列规格，决定 UI 网格大小
            "status": "可用"
        },
        ...
    }
"""
import json
import os
import sys


def _app_root():
    """返回应用根目录（和 `ConfigManager._app_root` 语义一致）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TrayManager:
    """料盘配置的 CRUD。

    持久化文件默认位于 ``<app_root>/config/trays_config.json``；
    可以在构造时通过 `config_file` 参数覆盖（单测时有用）。
    """

    def __init__(self, config_file=None):
        """构造时立即从磁盘加载配置到内存。"""
        if config_file is None:
            config_file = os.path.join(_app_root(), "config", "trays_config.json")
        self.config_file = config_file
        self.trays = self.load_trays()

    def load_trays(self):
        """读取配置；文件不存在或解析失败就用内置默认料盘。"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载料盘配置失败: {e}")
                return self.get_default_trays()
        return self.get_default_trays()

    def get_default_trays(self):
        """首次启动的默认料盘清单。

        A0001 被视为"出厂默认"料盘，`delete_tray` 会拒绝删除它。
        """
        return {
            "A0001": {
                "name": "料盘 A0001",
                "description": "标准LQFP单位料盘",
                "model": "ATMLH904",
                "angle": 90,
                "spec": "3x7",
                "status": "可用"
            },
            "A0002": {
                "name": "料盘 A0002",
                "description": "BGA专用料盘",
                "model": "MAX485",
                "angle": 0,
                "spec": "3x7",
                "status": "可用"
            },
            "A0003": {
                "name": "料盘 A0003",
                "description": "SOIC单位料盘",
                "model": "24C02BN",
                "angle": 90,
                "spec": "3x7",
                "status": "可用"
            }
        }

    def save_trays(self):
        """把当前内存里的 trays 落盘（UTF-8 + 2 空格缩进方便人阅读）。"""
        try:
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.trays, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存料盘配置失败: {e}")
            return False

    def get_tray_list(self):
        """返回所有料盘 ID 列表（顺序 = dict 插入顺序 = 用户新增顺序）。"""
        return list(self.trays.keys())

    def get_tray_info(self, tray_id):
        """按 ID 取整条记录；不存在返回 None。"""
        return self.trays.get(tray_id)

    def get_tray_model_and_angle(self, tray_id):
        """取料盘当前挂载的 (型号, 角度)；不存在返回 (None, None)。"""
        tray = self.trays.get(tray_id)
        if tray:
            return tray.get("model"), tray.get("angle")
        return None, None

    def get_tray_spec(self, tray_id):
        """取料盘的行列规格键（形如 ``"3x7"``），缺省 ``"3x7"``。"""
        tray = self.trays.get(tray_id)
        if tray:
            return tray.get("spec", "3x7")
        return "3x7"

    def add_tray(self, tray_id, name, description, model, angle, spec="3x7"):
        """新增一条料盘记录并立即落盘。

        注意
        ----
        不检查 ``tray_id`` 是否已存在；调用方（UI 层 `AddTrayDialog`）
        负责在弹窗阶段保证唯一性。
        """
        self.trays[tray_id] = {
            "name": name,
            "description": description,
            "model": model,
            "angle": angle,
            "spec": spec,
            "status": "可用"
        }
        self.save_trays()
        return True

    def update_tray(self, tray_id, **kwargs):
        """按 kwargs 合并更新料盘字段；不存在则不做事返回 False。"""
        if tray_id in self.trays:
            self.trays[tray_id].update(kwargs)
            self.save_trays()
            return True
        return False

    def delete_tray(self, tray_id):
        """删除料盘。

        业务约束：A0001 视为默认料盘，禁止删除，返回 False。
        """
        if tray_id in self.trays and tray_id != "A0001":
            del self.trays[tray_id]
            self.save_trays()
            return True
        return False
