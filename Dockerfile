# Speech Parallel Data API
FROM python:3.12-slim

WORKDIR /app

# ffmpeg for pydub (mp3 -> wav). Optional: espeak for pyttsx3 fallback.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY app/ ./app/

# Run the API (host 0.0.0.0 so container accepts external connections; use PORT if set, e.g. Railway)
EXPOSE 8000
ENV PORT=8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
