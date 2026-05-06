FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BIBLE_DB_PATH=/data/bible.sqlite \
    OUTPUT_DIR=/outputs \
    ASSET_CACHE_DIR=/data/assets \
    MODEL_ID=Qwen/Qwen3-TTS-12Hz-1.7B-Base \
    TTS_MODE=voice_clone \
    REF_AUDIO_PATH=/data/voices/narrador.wav \
    REF_TEXT_PATH=/data/voices/narrador.txt \
    VOICE_ID=narrador_principal \
    DEFAULT_LANGUAGE=Portuguese \
    X_VECTOR_ONLY_MODE=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip ffmpeg libsndfile1 sox \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 -m pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY runpod_handler.py /app/runpod_handler.py
COPY scripts /app/scripts
COPY bibles/naa.db /data/bible.sqlite

RUN mkdir -p /data/voices /data/assets /outputs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
