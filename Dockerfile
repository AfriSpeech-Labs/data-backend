# Speech Parallel Data API
FROM python:3.12-slim

WORKDIR /app

# ffmpeg for pydub (mp3 -> wav). Optional: espeak for pyttsx3 fallback.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies (CPU torch wheel — avoids huge CUDA packages on Railway).
COPY requirements.txt .
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Application code
COPY app/ ./app/
COPY run.py .

# Run the API (PORT from env; run.py avoids shell expansion issues on Railway)
EXPOSE 8000
ENV PORT=8000
CMD ["python", "run.py"]
