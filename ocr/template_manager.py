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
import shutil
import sys
from datetime import datetime

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

    def recognize_template_image(self, image_path):
        """只做参考图 OCR，不立即保存模板。

        新增模板流程需要用户从 OCR 结果中选择标准芯片型号；因此识别和保存
        必须拆开，避免未确认型号时污染 ``templates.json``。
        """
        try:
            result = self.engine.predict_image(image_path)
            self.last_error = result.get("status", "")

            if str(result.get("status", "")).startswith("error"):
                return {
                    "success": False,
                    "detected_model": "",
                    "detected_angle": 0,
                    "detected_texts": [],
                    "error": self.last_error,
                }

            detected_angle = int(result.get("angle", 0) or 0)
            detected_texts = [str(text) for text in result.get("texts", [])]
            detected_model = ""
            if detected_texts:
                detected_model = self._normalize_model_text(detected_texts[0])
            else:
                self.last_error = result.get("status", "empty")

            return {
                "success": True,
                "detected_model": detected_model,
                "detected_angle": detected_angle,
                "detected_texts": detected_texts,
                "error": self.last_error,
            }
        except Exception as e:
            self.last_error = str(e)
            print(f"模板识别失败: {e}")
            return {
                "success": False,
                "detected_model": "",
                "detected_angle": 0,
                "detected_texts": [],
                "error": str(e),
            }

    def save_template(
        self,
        model_name,
        angle,
        image_path=None,
        ocr_texts=None,
        tray_id=None,
        light_config=None,
        description="用户确认的模板",
    ):
        """保存用户确认后的模板配置。"""
        model_name = str(model_name or "").strip()
        if not model_name:
            self.last_error = "未选择模板型号。"
            return False

        existing = self.templates.get(model_name, {})
        now = datetime.now().isoformat(timespec="seconds")
        standard_image_path = existing.get("standardImagePath")
        if image_path:
            standard_image_path = self._copy_standard_image(model_name, image_path)

        self.templates[model_name] = {
            **existing,
            "angle": int(angle),
            "description": description,
            "modelName": model_name,
            "standardChipModel": model_name,
            "ocrTexts": list(ocr_texts or []),
            "standardImagePath": standard_image_path,
            "trayId": tray_id,
            "lightConfig": light_config or existing.get("lightConfig", {}),
            "createdAt": existing.get("createdAt", now),
            "updatedAt": now,
        }
        return self.save_templates()

    def add_template_from_image(self, image_path, model_name=None):
        """对参考图片跑一次 OCR，抽出型号 + 角度并保存为模板。

        保留旧接口供其它调用方兼容；新 UI 使用 ``recognize_template_image``
        和 ``save_template``，确保用户先选择标准型号再保存。
        """
        recognized = self.recognize_template_image(image_path)
        if not recognized["success"] or not recognized["detected_texts"]:
            return None, None, False

        detected_model = model_name or recognized["detected_model"]
        if not detected_model:
            return None, None, False

        saved = self.save_template(
            detected_model,
            recognized["detected_angle"],
            image_path=image_path,
            ocr_texts=recognized["detected_texts"],
            description=(
                "从图片自动识别生成 "
                f"(识别文本: {recognized['detected_texts'][0][:30]})"
            ),
        )
        return detected_model, int(recognized["detected_angle"]), saved

    def _normalize_model_text(self, raw_text):
        """从 OCR 文本中提取默认型号候选。"""
        raw_text = str(raw_text or "")[:40]
        alphanumeric = re.sub(r'[^A-Za-z0-9]', '', raw_text)
        return alphanumeric if alphanumeric else raw_text.strip()

    def _copy_standard_image(self, model_name, image_path):
        """把模板标准图复制到配置目录，避免临时文件被删除后路径失效。"""
        if not image_path or not os.path.isfile(image_path):
            return None
        image_dir = os.path.join(os.path.dirname(self.template_file), "template_images")
        os.makedirs(image_dir, exist_ok=True)
        _, ext = os.path.splitext(image_path)
        ext = ext if ext else ".png"
        safe_model = re.sub(r'[^A-Za-z0-9_-]+', "_", model_name).strip("_") or "template"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(image_dir, f"{safe_model}_{timestamp}{ext}")
        try:
            shutil.copy2(image_path, dst)
            return dst
        except Exception as e:
            self.last_error = f"复制模板标准图失败：{e}"
            print(self.last_error)
            return image_path

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
