"""UI 用户界面模块"""
from .main_window import OCRApp
from .material_slot import MaterialSlot
from .dialogs import TemplateConfirmDialog, CameraCaptureDialog, AddTrayDialog

__all__ = ['OCRApp', 'MaterialSlot', 'TemplateConfirmDialog', 'CameraCaptureDialog', 'AddTrayDialog']
