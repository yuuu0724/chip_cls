"""模板管理器 - 管理和维护 OCR 模板"""
import json
import os
import re
from .engine import OCREngine


class TemplateManager:
    """
    管理芯片型号的识别模板
    保存格式：{'型号': {'angle': 90, 'description': '标准方向'}}
    """
    
    def __init__(self, template_file=None):
        if template_file is None:
            template_file = os.path.join(os.path.dirname(__file__), "..", "config", "templates.json")
        self.template_file = template_file
        self.templates = self.load_templates()
        self.engine = OCREngine()
    
    def load_templates(self):
        """从 JSON 文件加载模板"""
        if os.path.exists(self.template_file):
            try:
                with open(self.template_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载模板失败: {e}")
                return {}
        return {}
    
    def save_templates(self):
        """保存模板到 JSON 文件"""
        try:
            os.makedirs(os.path.dirname(self.template_file), exist_ok=True)
            with open(self.template_file, 'w', encoding='utf-8') as f:
                json.dump(self.templates, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存模板失败: {e}")
            return False
    
    def add_template_from_image(self, image_path, model_name=None):
        """
        从上传的图片识别型号和方向，并保存为模板
        返回: (识别的型号, 识别的方向, 成功标志)
        """
        try:
            # 使用 OCR 引擎识别图片
            result = self.engine.predict_image(image_path)
            
            if result.get("status") == "error":
                return None, None, False
            
            detected_angle = result.get("angle", 0)
            detected_texts = result.get("texts", [])
            
            # 如果没有检测到文本，返回失败
            if not detected_texts:
                return None, None, False
            
            # 使用检测到的第一个文本作为型号（或使用用户指定的名称）
            if model_name:
                detected_model = model_name
            else:
                # 使用第一条识别结果作为型号名称
                # 提取纯字母数字部分作为推荐的模板名
                raw_text = detected_texts[0][:20]  # 限制长度
                # 尝试提取字母和数字（去掉特殊字符）
                alphanumeric = re.sub(r'[^A-Za-z0-9]', '', raw_text)
                detected_model = alphanumeric if alphanumeric else raw_text
            
            # 注意：不要在模板中保存"texts"字段，只保存标准的angle和description
            # 这样保证数据结构一致
            self.templates[detected_model] = {
                "angle": int(detected_angle),
                "description": f"从图片自动识别生成 (识别文本: {detected_texts[0][:30]})"
            }
            
            self.save_templates()
            return detected_model, int(detected_angle), True
            
        except Exception as e:
            print(f"模板识别失败: {e}")
            return None, None, False
    
    def get_template(self, model_name):
        """获取指定型号的模板参数"""
        return self.templates.get(model_name)
    
    def list_all_templates(self):
        """列出所有已保存的模板"""
        return list(self.templates.keys())
    
    def delete_template(self, model_name):
        """删除指定的模板"""
        if model_name in self.templates:
            del self.templates[model_name]
            self.save_templates()
            return True
        return False
    
    def update_template(self, model_name, angle, description=""):
        """更新现有模板"""
        if model_name in self.templates:
            self.templates[model_name]["angle"] = int(angle)
            if description:
                self.templates[model_name]["description"] = description
            self.save_templates()
            return True
        return False
