"""Generate English WAV from text using TTS. Prefers edge-tts (no system deps); fallback pyttsx3."""
import io
import tempfile
import time
from pathlib import Path


# Edge TTS English voice (works in Docker, no espeak)
EDGE_VOICE = "en-US-JennyNeural"


def generate_wav_edge_tts(text: str, voice: str = EDGE_VOICE) -> bytes:
    """Use edge-tts (Microsoft) with a specific neural voice short name; returns WAV bytes."""
    import edge_tts
    from pydub import AudioSegment

    voice = (voice or EDGE_VOICE).strip() or EDGE_VOICE
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        mp3_path = f.name
    try:
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice)
                communicate.save_sync(mp3_path)
                if Path(mp3_path).stat().st_size == 0:
                    raise RuntimeError(
                        "No audio was received. Please verify that your parameters are correct."
                    )
                seg = AudioSegment.from_mp3(mp3_path)
                buf = io.BytesIO()
                seg.export(buf, format="wav")
                return buf.getvalue()
            except Exception as e:
                last_err = e
                if "no audio was received" in str(e).lower() and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise
        if last_err is not None:
            raise last_err
        raise RuntimeError("Edge TTS failed without a specific error")
    finally:
        Path(mp3_path).unlink(missing_ok=True)


def _generate_wav_pyttsx3(text: str) -> bytes:
    """Use pyttsx3 + espeak (requires espeak in container). Avoids gmw/en."""
    try:
        import pyttsx3
    except ImportError:
        raise RuntimeError(
            "Backend TTS not installed. Install pyttsx3 or use POST /api/audio/english/with-text with client-generated WAV."
        ) from None

    voices = None
    try:
        engine = pyttsx3.init()
        voices = engine.getProperty("voices") or []
    except Exception as e:
        raise RuntimeError(str(e)) from e

    # Prefer en-us, en-gb; never use gmw/en (fails in many containers)
    voice_set = False
    for preferred in ("en-us", "en-gb", "en", "english"):
        for v in voices:
            vid = (getattr(v, "id", None) or "").lower()
            if vid == preferred or vid.startswith(preferred + "/") or f"/{preferred}" in vid:
                try:
                    engine.setProperty("voice", v.id)
                    voice_set = True
                    break
                except Exception:
                    continue
        if voice_set:
            break
    if not voice_set:
        for v in voices:
            vid = (getattr(v, "id", None) or "").lower()
            if "gmw" not in vid:
                try:
                    engine.setProperty("voice", v.id)
                    voice_set = True
                    break
                except Exception:
                    continue
        if not voice_set:
            raise RuntimeError(
                "No working TTS voice (gmw/en fails here). Use edge-tts or install espeak with en-us."
            )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        path = f.name
    try:
        engine.save_to_file(text, path)
        engine.runAndWait()
        return Path(path).read_bytes()
    finally:
        Path(path).unlink(missing_ok=True)


def generate_wav_from_text(text: str) -> bytes:
    """Generate a WAV file (bytes) from English text. Prefers edge-tts; fallback pyttsx3."""
    text = (text or "").strip()
    if not text:
        raise ValueError("text_english is required")

    try:
        return generate_wav_edge_tts(text, EDGE_VOICE)
    except Exception:
        return _generate_wav_pyttsx3(text)
