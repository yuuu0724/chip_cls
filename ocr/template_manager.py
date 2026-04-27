"""模板管理器 - 读写 `config/templates.json`。

把一张参考图片上的芯片型号 + 方向角度保存成"模板"，后续检测时用模板里的
型号字符串做匹配。保存格式示例：

    {
        "ATMLH904": {
            "angle": 90,
            "description": "标准方向"
        },
        ...
    }
"""
import json
import os
import re
import sys

from .engine import OCREngine


def _app_root():
    """返回应用根目录（同 `data.config_manager._app_root`）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TemplateManager:
    """芯片型号识别模板的 CRUD。

    内部持有一个 `OCREngine`；由于 `OCREngine` 的 ONNX session 走类级
    共享，多个 `TemplateManager`/主窗口共存也不会重复加载模型。
    """

    def __init__(self, template_file=None):
        """
        Parameters
        ----------
        template_file : str | None
            模板 JSON 路径；None 时使用默认 ``<app_root>/config/templates.json``。
        """
        if template_file is None:
            template_file = os.path.join(_app_root(), "config", "templates.json")
        self.template_file = template_file
        self.templates = self.load_templates()
        self.engine = OCREngine()
        # 最近一次操作的错误信息（供 UI 显示给用户）
        self.last_error = ""

    def load_templates(self):
        """从 JSON 文件加载模板；文件不存在返回空 dict。"""
        if os.path.exists(self.template_file):
            try:
                with open(self.template_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载模板失败: {e}")
                return {}
        return {}

    def save_templates(self):
        """把内存里的模板字典写回 JSON 文件。

        写失败时把异常字符串放到 `last_error`，调用方可以读。
        """
        try:
            os.makedirs(os.path.dirname(self.template_file), exist_ok=True)
            with open(self.template_file, 'w', encoding='utf-8') as f:
                json.dump(self.templates, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.last_error = str(e)
            print(f"保存模板失败: {e}")
            return False

    def add_template_from_image(self, image_path, model_name=None):
        """对参考图片跑一次 OCR，抽出型号 + 角度并保存为模板。

        Parameters
        ----------
        image_path : str
            图片绝对路径。
        model_name : str | None
            如果用户已经明确知道型号，可以直接传入作为模板名；否则从
            OCR 结果的第一条文本里抽字母数字作为推荐名。

        Returns
        -------
        tuple[str | None, int | None, bool]
            ``(识别出的型号, 识别出的角度, 成功标志)``。失败时前两项为 None。

        失败原因会写入 `self.last_error`，UI 可拼接在提示框里显示。
        """
        try:
            result = self.engine.predict_image(image_path)
            self.last_error = result.get("status", "")

            # 引擎内部抛错 -> 直接失败
            if str(result.get("status", "")).startswith("error"):
                return None, None, False

            detected_angle = result.get("angle", 0)
            detected_texts = result.get("texts", [])

            if not detected_texts:
                self.last_error = result.get("status", "empty")
                return None, None, False

            # 确定最终的"模板名"：优先用户显式指定，否则从 OCR 第一条文本抽
            if model_name:
                detected_model = model_name
            else:
                raw_text = detected_texts[0][:20]  # 截断避免过长
                # 只保留字母数字，去掉空格/标点等；全被过滤掉时退回原文
                alphanumeric = re.sub(r'[^A-Za-z0-9]', '', raw_text)
                detected_model = alphanumeric if alphanumeric else raw_text

            # 注意：只存 angle + description，不把原始 texts 写进去，保证
            # 模板数据结构稳定（后续字段升级时不会混进脏数据）
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
        """按型号名取模板；不存在返回 None。"""
        return self.templates.get(model_name)

    def list_all_templates(self):
        """列出所有已保存的模板型号名。"""
        return list(self.templates.keys())

    def delete_template(self, model_name):
        """删除指定模板。找不到返回 False。"""
        if model_name in self.templates:
            del self.templates[model_name]
            self.save_templates()
            return True
        return False

    def update_template(self, model_name, angle, description=""):
        """更新已有模板的角度 / 描述。找不到返回 False。"""
        if model_name in self.templates:
            self.templates[model_name]["angle"] = int(angle)
            if description:
                self.templates[model_name]["description"] = description
            self.save_templates()
            return True
        return False
