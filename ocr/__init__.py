"""OCR 核心模块"""
from .engine import OCREngine
from .template_manager import TemplateManager
from .logic_controller import MaterialController

__all__ = ['OCREngine', 'TemplateManager', 'MaterialController']
