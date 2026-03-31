"""逻辑控制器 - 检测结果分析"""


class MaterialController:
    @staticmethod
    def analyze_status(detected_data, target_model, target_angle):
        texts = detected_data.get("texts", [])
        angle = detected_data.get("angle", 0)
        
        if not texts:
            return "空", "yellow"

        # 成员检测：只要目标型号是识别内容中任何一行的子串
        target_up = target_model.upper().strip()
        model_match = any(target_up in t.upper() for t in texts)

        if model_match:
            return ("正常", "green") if int(angle) == int(target_angle) else ("方向错误", "red")
        
        return "型号错误", "red"
