# LEGACY (Railway / container deploy). The API now targets Vercel serverless
# (see vercel.json + api/index.py). This Dockerfile is kept for self-hosting /
# running the offline TTS toolchain in a container. MMS (torch) was removed.
#
# Speech Parallel Data API
FROM python:3.12-slim

WORKDIR /app

# ffmpeg for pydub (edge-tts MP3 -> WAV). Only needed when running the optional
# TTS toolchain (requirements-tts.txt) in-container.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-tts.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -r requirements-tts.txt

COPY app/ ./app/
COPY run.py .

EXPOSE 8000
ENV PORT=8000
CMD ["python", "run.py"]
