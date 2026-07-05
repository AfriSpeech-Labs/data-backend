# Speech Parallel Data API
FROM python:3.12-slim

WORKDIR /app

# ffmpeg for pydub; libgomp1 for torch CPU runtime.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-mms.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-mms.txt \
    && python -c "import sentencepiece; import torch; from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('facebook/mms-tts-aka', use_fast=False); print('MMS tokenizer OK', torch.__version__)"

COPY app/ ./app/
COPY run.py .

EXPOSE 8000
ENV PORT=8000
CMD ["python", "run.py"]
