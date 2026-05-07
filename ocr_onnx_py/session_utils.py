from __future__ import annotations

import logging
import os
import sys


def _register_nvidia_dll_dirs() -> list[str]:
    """在 import onnxruntime 之前，把 CUDA/cuDNN 所在目录注册到 DLL 搜索路径。

    nvidia/conda-nvidia 渠道与 pip wheel 都会把 CUDA/cuDNN DLL 放到
    <env>/Lib/site-packages/nvidia/<lib>/bin/，这些目录默认不在 Windows DLL
    搜索路径里，导致 onnxruntime_providers_cuda.dll 加载依赖失败 (error 126)。

    冻结态 (PyInstaller) 下 sys.prefix 指向 _MEIPASS，但 wheel layout 不会
    保留为 Lib/site-packages/nvidia/<lib>/bin/。spec 收集结果通常落到
    _MEIPASS/nvidia/<lib>/bin/，或干脆摊到 _MEIPASS 根目录。这里两条路都覆盖。
    """
    if sys.platform != "win32":
        return []
    candidates: list[str] = []

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = sys._MEIPASS
        # 注册顺序就是 DLL 搜索优先级：把 dev 验证过的 nvidia/* wheel DLL 放最前，
        # ORT 自带的 capi/ 作为兜底，_MEIPASS 根目录作为 PATH 兜底放最后。
        nvidia_root = os.path.join(meipass, "nvidia")
        if os.path.isdir(nvidia_root):
            for name in os.listdir(nvidia_root):
                bin_dir = os.path.join(nvidia_root, name, "bin")
                if os.path.isdir(bin_dir):
                    candidates.append(bin_dir)
        capi_dir = os.path.join(meipass, "onnxruntime", "capi")
        if os.path.isdir(capi_dir):
            candidates.append(capi_dir)
        candidates.append(meipass)

    for root in {sys.prefix, sys.base_prefix}:
        nvidia_root = os.path.join(root, "Lib", "site-packages", "nvidia")
        if not os.path.isdir(nvidia_root):
            continue
        for name in os.listdir(nvidia_root):
            bin_dir = os.path.join(nvidia_root, name, "bin")
            if os.path.isdir(bin_dir):
                candidates.append(bin_dir)

    registered: list[str] = []
    for bin_dir in candidates:
        try:
            os.add_dll_directory(bin_dir)
        except OSError:
            pass
        # 有些 DLL 加载路径 (如 ORT 内部) 不走 add_dll_directory，
        # 同时把目录前置到 PATH 兜底。
        cur_path = os.environ.get("PATH", "")
        if bin_dir not in cur_path:
            os.environ["PATH"] = bin_dir + os.pathsep + cur_path
        registered.append(bin_dir)
    return registered


_REGISTERED_DLL_DIRS = _register_nvidia_dll_dirs()

import onnxruntime as ort  # noqa: E402  # must follow DLL dir registration

logger = logging.getLogger(__name__)

if _REGISTERED_DLL_DIRS:
    logger.info("已注册 CUDA DLL 目录: %s", _REGISTERED_DLL_DIRS)


# 通过环境变量可以关闭 GPU（默认开启）。例如: OCR_USE_GPU=0
_USE_GPU_ENV = os.environ.get("OCR_USE_GPU", "1").strip().lower() not in ("0", "false", "no")
_GPU_DEVICE_ID = int(os.environ.get("OCR_GPU_DEVICE_ID", "0"))

# 诊断标记：CUDA 静默回退 CPU 时只重试一次，避免对 cls/det/rec 都重复刷诊断日志
_DIAG_DONE: dict = {"fired": False}


def _cuda_provider_options() -> dict:
    """CUDAExecutionProvider 参数，针对 OCR 这种小模型场景做了默认优化。"""
    return {
        "device_id": _GPU_DEVICE_ID,
        # EXHAUSTIVE 首次会慢一些，但之后卷积会更快；HEURISTIC 是折中
        "cudnn_conv_algo_search": "EXHAUSTIVE",
        "arena_extend_strategy": "kSameAsRequested",
        "do_copy_in_default_stream": True,
    }


def _build_providers() -> list:
    """返回按优先级排序的 providers 配置，CUDA 带参数，CPU 兜底。"""
    available = ort.get_available_providers()
    logger.info("ONNX Runtime 可用 providers: %s", available)

    providers: list = []
    if _USE_GPU_ENV and "CUDAExecutionProvider" in available:
        providers.append(("CUDAExecutionProvider", _cuda_provider_options()))
    elif _USE_GPU_ENV:
        logger.warning(
            "OCR_USE_GPU=1 但未检测到 CUDAExecutionProvider，"
            "请确认已安装 onnxruntime-gpu 且 CUDA/cuDNN 匹配。将回退 CPU。"
        )
    providers.append("CPUExecutionProvider")
    return providers


def create_session(model_path: str) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.log_severity_level = 3
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    providers = _build_providers()
    use_gpu = any(
        (p[0] if isinstance(p, tuple) else p) != "CPUExecutionProvider"
        for p in providers
    )

    if use_gpu:
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
    else:
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        cpu_count = os.cpu_count() or 4
        options.intra_op_num_threads = min(max(cpu_count // 2, 1), 4)
        options.inter_op_num_threads = 1

    try:
        session = ort.InferenceSession(model_path, sess_options=options, providers=providers)
    except Exception as e:
        if not use_gpu:
            raise
        logger.warning(
            "CUDA session 创建失败 (%s)，回退到 CPU provider", e,
        )
        providers = ["CPUExecutionProvider"]
        cpu_count = os.cpu_count() or 4
        options.intra_op_num_threads = min(max(cpu_count // 2, 1), 4)
        session = ort.InferenceSession(model_path, sess_options=options, providers=providers)

    active_providers = session.get_providers()
    on_gpu = "CUDAExecutionProvider" in active_providers
    model_name = os.path.basename(os.path.dirname(model_path))

    # 诊断：期望 GPU 但 ORT 静默回退 CPU 时，强制 CUDA-only 再创建一次会话，
    # 让 ORT 必须抛真实异常 —— 否则 ORT 默默把 CUDA EP 从 active 列表里剔除，
    # 上层完全看不到原因。仅对第一个加载的模型 (det) 触发，避免重复刷屏。
    if use_gpu and not on_gpu and not _DIAG_DONE["fired"]:
        _DIAG_DONE["fired"] = True
        logger.warning(
            "[诊断] 请求 CUDAExecutionProvider 但 active=%s，尝试 CUDA-only 重建以捕获真实错误",
            active_providers,
        )
        diag_opts = ort.SessionOptions()
        diag_opts.log_severity_level = 0  # VERBOSE，ORT 详细日志走 stderr
        try:
            ort.InferenceSession(
                model_path,
                sess_options=diag_opts,
                providers=[("CUDAExecutionProvider", _cuda_provider_options())],
            )
            logger.warning("[诊断] CUDA-only 居然成功 — 第一次可能是 ORT 临时状态")
        except Exception as diag_err:
            logger.error(
                "[诊断] CUDA EP 强制加载失败: %s: %s",
                type(diag_err).__name__, diag_err, exc_info=True,
            )

    logger.info(
        "模型 [%s] 加载完成 | providers=%s | GPU=%s | device_id=%s",
        model_name, active_providers, on_gpu,
        _GPU_DEVICE_ID if on_gpu else "-",
    )
    return session
