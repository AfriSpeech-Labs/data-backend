"""MMS (Meta VITS) TTS for languages without Edge voices."""
from __future__ import annotations

import io
from typing import Any

_model_cache: dict[str, tuple[Any, Any]] = {}

# Hub repos with missing/broken tokenizer configs → load tokenizer from donor MMS checkpoint.
_TOKENIZER_FALLBACK_FOR_MODEL: Final[dict[str, str]] = {
    "waxal-benchmarking/mms-tts-twi": "facebook/mms-tts-aka",
}
# Empty or stub Hub repos → use a working MMS checkpoint instead.
_EFFECTIVE_MMS_MODEL: Final[dict[str, str]] = {
    "waxal-benchmarking/mms-tts-twi": "facebook/mms-tts-aka",
}


class MmsTtsNotInstalledError(RuntimeError):
    """Raised when torch/transformers are not installed."""


def mms_available() -> bool:
    return mms_import_diagnostics()["available"]


def mms_import_diagnostics() -> dict:
    """Report whether MMS deps import and why not (for /health debugging)."""
    out: dict = {
        "available": False,
        "torch": None,
        "transformers": None,
        "sentencepiece": None,
        "error": None,
    }
    try:
        import torch

        out["torch"] = torch.__version__
    except ImportError as e:
        out["error"] = f"torch: {e}"
        return out
    try:
        import transformers
        from transformers import VitsModel  # noqa: F401

        out["transformers"] = transformers.__version__
    except ImportError as e:
        out["error"] = f"transformers: {e}"
        return out
    try:
        import sentencepiece

        out["sentencepiece"] = sentencepiece.__version__
    except ImportError as e:
        out["error"] = f"sentencepiece: {e}"
        return out
    out["available"] = True
    return out


def _load_tokenizer(model_id: str):
    from transformers import AutoTokenizer

    try:
        return AutoTokenizer.from_pretrained(model_id, use_fast=False)
    except Exception as primary_err:
        fallback_id = _TOKENIZER_FALLBACK_FOR_MODEL.get(model_id)
        if not fallback_id:
            raise primary_err
        return AutoTokenizer.from_pretrained(fallback_id, use_fast=False)


def _load_model(model_id: str) -> tuple[Any, Any]:
    model_id = _EFFECTIVE_MMS_MODEL.get(model_id, model_id)
    if model_id in _model_cache:
        return _model_cache[model_id]
    try:
        import torch
        from transformers import VitsModel
    except ImportError as e:
        raise MmsTtsNotInstalledError(
            "MMS TTS requires torch and transformers. "
            "Install optional deps: pip install torch transformers scipy"
        ) from e
    tokenizer = _load_tokenizer(model_id)
    try:
        model = VitsModel.from_pretrained(model_id)
    except OSError as e:
        fallback_id = _TOKENIZER_FALLBACK_FOR_MODEL.get(model_id)
        if not fallback_id:
            raise ValueError(
                f"MMS model '{model_id}' has no weights on HuggingFace Hub. "
                f"Set tts_target_mms_model on the language pair (e.g. {fallback_id or 'facebook/mms-tts-aka'})."
            ) from e
        model = VitsModel.from_pretrained(fallback_id)
    model.eval()
    _model_cache[model_id] = (model, tokenizer)
    return model, tokenizer


def _float_wav_to_bytes(waveform, sample_rate: int) -> bytes:
    import numpy as np
    from scipy.io import wavfile

    arr = waveform
    if hasattr(arr, "detach"):
        arr = arr.detach().cpu().numpy()
    arr = np.asarray(arr, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.squeeze()
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    wavfile.write(buf, sample_rate, pcm)
    return buf.getvalue()


def generate_wav_mms(text: str, model_id: str) -> bytes:
    """Synthesize WAV bytes from text using a HuggingFace MMS VITS checkpoint."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Text is required for MMS synthesis")
    model_id = (model_id or "").strip()
    if not model_id:
        raise ValueError("MMS model id is required")

    model, tokenizer = _load_model(model_id)
    import torch

    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        output = model(**inputs)
    waveform = output.waveform
    if waveform.ndim == 3:
        waveform = waveform[0, 0]
    elif waveform.ndim == 2:
        waveform = waveform[0]
    sample_rate = int(getattr(model.config, "sampling_rate", 16000) or 16000)
    return _float_wav_to_bytes(waveform, sample_rate)
