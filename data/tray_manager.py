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
from datetime import datetime


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
                "rows": 3,
                "cols": 7,
                "status": "可用"
            },
            "A0002": {
                "name": "料盘 A0002",
                "description": "BGA专用料盘",
                "model": "MAX485",
                "angle": 0,
                "spec": "3x7",
                "rows": 3,
                "cols": 7,
                "status": "可用"
            },
            "A0003": {
                "name": "料盘 A0003",
                "description": "SOIC单位料盘",
                "model": "24C02BN",
                "angle": 90,
                "spec": "3x7",
                "rows": 3,
                "cols": 7,
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

    def get_tray_dimensions(self, tray_id):
        """返回料盘行列数，优先使用新字段，兼容旧的 ``spec``。"""
        tray = self.trays.get(tray_id) or {}
        rows = tray.get("rows")
        cols = tray.get("cols")
        if rows and cols:
            return int(rows), int(cols)
        try:
            rows_text, cols_text = self.get_tray_spec(tray_id).split("x")
            return int(rows_text), int(cols_text)
        except (ValueError, AttributeError):
            return 3, 7

    def get_tray_motion_params(self, tray_id):
        """返回料盘运动参数；旧配置缺字段时返回 None。"""
        tray = self.trays.get(tray_id)
        if not tray:
            return None
        origin = tray.get("firstSlotOrigin") or {}
        return {
            "rows": tray.get("rows"),
            "cols": tray.get("cols"),
            "pitchX": tray.get("pitchX"),
            "pitchY": tray.get("pitchY"),
            "originX": origin.get("x"),
            "originY": origin.get("y"),
            "originZ": origin.get("z"),
            "rowDirection": tray.get("rowDirection", 1),
            "colDirection": tray.get("colDirection", 1),
        }

    def is_tray_config_complete(self, tray_id):
        """校验料盘是否具备后续运动所需的完整几何参数。"""
        params = self.get_tray_motion_params(tray_id)
        if not params:
            return False, "当前未选择有效料盘。"

        required = ["rows", "cols", "pitchX", "pitchY", "originX", "originY", "originZ"]
        missing = [key for key in required if params.get(key) is None]
        if missing:
            return False, "料盘缺少运动参数：" + ", ".join(missing)

        try:
            rows = int(params["rows"])
            cols = int(params["cols"])
            pitch_x = float(params["pitchX"])
            pitch_y = float(params["pitchY"])
            float(params["originX"])
            float(params["originY"])
            float(params["originZ"])
        except (TypeError, ValueError):
            return False, "料盘运动参数包含非法数字。"

        if rows <= 0 or cols <= 0:
            return False, "料盘行数、列数必须大于 0。"
        if pitch_x <= 0 or pitch_y <= 0:
            return False, "料盘横向间距、纵向间距必须大于 0。"
        return True, "料盘运动参数完整。"

    def calculate_slot_coordinate(self, tray_id, slot_index):
        """按 0 基准槽位索引计算目标坐标。"""
        ok, message = self.is_tray_config_complete(tray_id)
        if not ok:
            raise ValueError(message)

        params = self.get_tray_motion_params(tray_id)
        rows = int(params["rows"])
        cols = int(params["cols"])
        slot_index = int(slot_index)
        if slot_index < 0 or slot_index >= rows * cols:
            raise ValueError("槽位索引超出当前料盘范围。")

        row = slot_index // cols
        col = slot_index % cols
        col_dir = -1 if int(params.get("colDirection", 1)) < 0 else 1
        row_dir = -1 if int(params.get("rowDirection", 1)) < 0 else 1
        return {
            "x": float(params["originX"]) + col * float(params["pitchX"]) * col_dir,
            "y": float(params["originY"]) + row * float(params["pitchY"]) * row_dir,
            "z": float(params["originZ"]),
            "row": row,
            "col": col,
        }

    def add_tray(
        self,
        tray_id,
        name,
        description,
        model,
        angle,
        spec="3x7",
        rows=None,
        cols=None,
        pitch_x=None,
        pitch_y=None,
        origin_x=None,
        origin_y=None,
        origin_z=None,
        light_config=None,
    ):
        """新增一条料盘记录并立即落盘。

        注意
        ----
        不检查 ``tray_id`` 是否已存在；调用方（UI 层 `AddTrayDialog`）
        负责在弹窗阶段保证唯一性。
        """
        if rows is None or cols is None:
            try:
                rows, cols = spec.split("x")
            except (ValueError, AttributeError):
                rows, cols = 3, 7
        now = datetime.now().isoformat(timespec="seconds")
        self.trays[tray_id] = {
            "name": name,
            "description": description,
            "model": model,
            "angle": angle,
            "spec": spec,
            "rows": int(rows),
            "cols": int(cols),
            "pitchX": float(pitch_x) if pitch_x is not None else None,
            "pitchY": float(pitch_y) if pitch_y is not None else None,
            "firstSlotOrigin": {
                "x": float(origin_x) if origin_x is not None else None,
                "y": float(origin_y) if origin_y is not None else None,
                "z": float(origin_z) if origin_z is not None else None,
            },
            "lightConfig": light_config or {},
            "rowDirection": 1,
            "colDirection": 1,
            "status": "可用",
            "createdAt": now,
            "updatedAt": now,
        }
        self.save_trays()
        return True

    def update_tray(self, tray_id, **kwargs):
        """按 kwargs 合并更新料盘字段；不存在则不做事返回 False。"""
        if tray_id in self.trays:
            kwargs["updatedAt"] = datetime.now().isoformat(timespec="seconds")
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
