# logic_controller.py
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
        
        # 调试日志：帮助诊断匹配问题
        # print(f"[DEBUG] 目标型号: {target_model}, 识别结果: {texts}, 匹配: {model_match}")

        if model_match:
            return ("正常", "green") if int(angle) == int(target_angle) else ("方向错误", "red")
        
        return "型号错误", "red"