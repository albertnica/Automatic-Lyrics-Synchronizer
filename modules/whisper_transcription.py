"""
modules/whisper_transcription.py
Helper utilities to run ASR transcription using configured models via the
Hugging Face `transformers` pipeline.

Responsibilities:
- wrap pipeline initialization and invocation with stderr filtering and safe fallbacks so callers do not need to handle noisy HF/torch messages.
- manage device selection (CPU vs CUDA) and provide best-effort diagnostics about device choice.
- expose simple APIs: `transcribe_with_model` (single-model run) and `transcribe_iter_models`.
"""

import os
import sys
import warnings
import logging
from typing import List, Dict, Any, Optional, Tuple, Callable
from config import TRANSCRIBE_MODELS
from .logs_manager import dbg, info

try:
    from transformers import pipeline
    from transformers import logging as transformers_logging
except Exception:
    pipeline = None
    transformers_logging = None

try:
    from qwen_asr import Qwen3ASRModel
except Exception:
    Qwen3ASRModel = None

try:
    import torch
except Exception:
    torch = None

# Reduce transformers telemetry and verbose logs when available.
# These environment variables and logger adjustments attempt to quiet HF/transformers
# so CLI output remains readable.
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
try:
    logging.getLogger("transformers").setLevel(logging.ERROR)
    logging.getLogger("transformers.generation_utils").setLevel(logging.ERROR)
    if transformers_logging is not None:
        transformers_logging.set_verbosity_error()
except Exception:
    # Do not fail if logging APIs are unavailable.
    pass

# Load HF_TOKEN from credentials if available
try:
    from modules.utils import load_credentials
    _creds = load_credentials()
    if "HF_TOKEN" in _creds and _creds["HF_TOKEN"]:
        os.environ["HF_TOKEN"] = _creds["HF_TOKEN"]
except Exception:
    pass

class _StderrFilter:
    """
    Filter specific stderr patterns emitted by HF transformers to reduce noise.
    """
    def __init__(self, orig):
        self._orig = orig
        # Patterns to suppress from stderr to avoid noisy HF warnings.
        self._patterns = [
            "Using custom `forced_decoder_ids`",
            "Transcription using a multilingual Whisper will default to language detection"
        ]

    def write(self, s):
        """
        Write filtered stderr output, skipping known noisy lines.
        """
        try:
            for part in s.splitlines(keepends=True):
                # Skip any lines that match noisy patterns; otherwise forward to original stderr.
                if any(p in part for p in self._patterns):
                    continue
                self._orig.write(part)
        except Exception:
            self._orig.write(s)

    def flush(self):
        """
        Flush the underlying stderr stream.
        """
        try:
            self._orig.flush()
        except Exception:
            pass

    def fileno(self):
        """
        Return the file descriptor of the underlying stderr, or 1 on failure.
        """
        try:
            return self._orig.fileno()
        except Exception:
            return 1

def _detect_device(requested: int) -> int:
    """
    Auto-detect device: prefer CUDA if available, otherwise fall back to CPU.
    """
    if torch is None:
        info("torch not available -> using CPU.")
        return -1
    try:
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            # Prefer the first CUDA device by default.
            try:
                count = torch.cuda.device_count()
                if count > 0:
                    info("Auto-detected CUDA available -> using cuda:0")
                    return 0
            except Exception:
                # If cuda APIs fail for some reason, fall back to CPU.
                dbg("torch.cuda APIs failed during device detection; falling back to CPU.")
                return -1
        info("CUDA not available -> using CPU.")
        return -1
    except Exception as e:
        dbg(f"_detect_device error: {e}")
        return -1

def _safe_pipeline_init(model_name: str, device: int, **kwargs):
    """
    Initialize a transformers ASR pipeline safely and return it, or None on failure.
    """
    if pipeline is None:
        dbg("transformers.pipeline unavailable.")
        return None
    orig_stderr = sys.stderr
    try:
        # Temporarily replace stderr to suppress known noisy messages during init.
        sys.stderr = _StderrFilter(orig_stderr)
        p = pipeline('automatic-speech-recognition', model=model_name, device=device, **kwargs)
        return p
    except Exception as e:
        dbg(f"pipeline init error for {model_name} on device {device}: {e}")
        return None
    finally:
        sys.stderr = orig_stderr

def _safe_pipeline_call(pipeline_obj, audio_path: str, **kwargs):
    """
    Invoke the pipeline on an audio file while filtering noisy stderr; return output or None.
    """
    if pipeline_obj is None:
        return None
    orig_stderr = sys.stderr
    try:
        sys.stderr = _StderrFilter(orig_stderr)
        out = pipeline_obj(audio_path, return_timestamps=True, **kwargs)
        return out
    except Exception as e:
        dbg(f"pipeline call error: {e}")
        return None
    finally:
        sys.stderr = orig_stderr

def is_qwen_model(model_name: str) -> bool:
    """
    Check if a model identifier corresponds to Alibaba's Qwen ASR architecture.
    """
    return "qwen" in model_name.lower()


def transcribe_with_qwen(model_name: str, audio_path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Run speech recognition using native Qwen3ASRModel from qwen-asr package with forced aligner timestamps.
    """
    if Qwen3ASRModel is None:
        err = "qwen-asr package is not installed."
        dbg(err)
        return None, err

    try:
        device_str = "cuda:0" if (torch and getattr(torch, "cuda", None) and torch.cuda.is_available()) else "cpu"
        dtype = torch.float16 if device_str.startswith("cuda") else torch.float32

        info(f"Loading Qwen ASR model '{model_name}' on {device_str}...")
        aligner_id = "Qwen/Qwen3-ForcedAligner-0.6B"
        aligner_kwargs = dict(dtype=dtype, device_map=device_str)

        try:
            model = Qwen3ASRModel.from_pretrained(
                model_name,
                device_map=device_str,
                dtype=dtype,
                max_new_tokens=512,
                forced_aligner=aligner_id,
                forced_aligner_kwargs=aligner_kwargs,
            )
            has_aligner = True
        except Exception as align_err:
            dbg(f"ForcedAligner failed to load ({align_err}); falling back to base model.")
            model = Qwen3ASRModel.from_pretrained(
                model_name,
                device_map=device_str,
                dtype=dtype,
                max_new_tokens=512,
            )
            has_aligner = False

        info(f"Transcribing '{os.path.basename(audio_path)}' with Qwen ASR (aligner={has_aligner})...")
        res = model.transcribe(audio=audio_path, return_time_stamps=has_aligner)

        chunks = []
        raw_text = ""

        if isinstance(res, list) and len(res) > 0:
            item = res[0]
            raw_text = getattr(item, "text", "")
            time_stamps = getattr(item, "time_stamps", None)
            if time_stamps:
                stamps_flat = time_stamps[0] if (isinstance(time_stamps, list) and len(time_stamps) > 0 and isinstance(time_stamps[0], list)) else time_stamps
                for stamp in stamps_flat:
                    s_start = getattr(stamp, "start_time", 0.0)
                    s_end = getattr(stamp, "end_time", 0.0)
                    s_text = getattr(stamp, "text", "")
                    chunks.append({"start": float(s_start), "end": float(s_end), "text": s_text})
        elif isinstance(res, dict):
            raw_text = res.get("text", "")
            if "chunks" in res:
                chunks = res["chunks"]
        else:
            raw_text = str(res)

        if not chunks and raw_text:
            chunks = [{"start": 0.0, "end": 9999.0, "text": raw_text}]

        return {"model": model_name, "result": {"chunks": chunks, "text": raw_text}}, None
    except Exception as e:
        err_msg = f"Qwen ASR error: {e}"
        dbg(err_msg)
        return None, err_msg


def transcribe_with_hf(model_name: str, audio_path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Run speech recognition using standard Hugging Face pipeline (Whisper, Distil-Whisper, etc.).
    """
    actual_device = _detect_device(None)
    try:
        p = _safe_pipeline_init(model_name, device=actual_device)
    except Exception as e:
        return None, f"Pipeline init error: {e}"

    if p is None:
        return None, f"Pipeline initialization failed for model '{model_name}'"

    try:
        out = _safe_pipeline_call(p, audio_path)
    except Exception as e:
        return None, f"Pipeline execution error: {e}"

    if out is None:
        return None, f"Pipeline returned no output for model '{model_name}'"

    return {"model": model_name, "result": out}, None


def transcribe_with_model(model_name: str, audio_path: str) -> Optional[Dict[str, Any]]:
    """
    Transcribe `audio_path` with `model_name`. Returns {'model':..., 'result':...} or None.
    Supports both standard Hugging Face transformers models and Qwen ASR models.
    """
    if is_qwen_model(model_name):
        res, _ = transcribe_with_qwen(model_name, audio_path)
        return res
    res, _ = transcribe_with_hf(model_name, audio_path)
    return res


def transcribe_iter_models(
    audio_path: str,
    run_folder: Optional[str] = None,
    models: Optional[List[str]] = None,
    status_hook: Optional[Callable[[str, str, str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Try each configured model in order and collect successful transcription results.
    Dispatches real-time model status events to status_hook if provided.
    """
    results = []
    models_to_run = models if models else TRANSCRIBE_MODELS
    for m in models_to_run:
        info(f"Transcribing with model: {m}")
        if status_hook:
            status_hook(m, "start", "")

        if is_qwen_model(m):
            res, err = transcribe_with_qwen(m, audio_path)
        else:
            res, err = transcribe_with_hf(m, audio_path)

        if res:
            results.append(res)
            chunk_count = len(res.get("result", {}).get("chunks", []))
            if status_hook:
                status_hook(m, "success", f"({chunk_count} segments)")
            dbg(f"Model {m} succeeded.")
        else:
            if status_hook:
                status_hook(m, "failed", err or "ASR inference failed")
            dbg(f"Model {m} failed: {err}")
    return results
