#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
LOG_PATH="${LOG_PATH:-/workspace/uvicorn_omnivoice.log}"
PYTHON_BIN="${PYTHON_BIN:-python}"
DAEMON=false

if [[ "${1:-}" == "--daemon" ]]; then
  DAEMON=true
fi

export BIBLE_DB_PATH="${BIBLE_DB_PATH:-bibles/naa.db}"
export OUTPUT_DIR="${OUTPUT_DIR:-/workspace/outputs}"
export ASSET_CACHE_DIR="${ASSET_CACHE_DIR:-/workspace/assets}"
export TTS_BACKEND="${TTS_BACKEND:-omnivoice}"
export MODEL_ID="${MODEL_ID:-k2-fsa/OmniVoice}"
export TTS_MODE="${TTS_MODE:-voice_clone}"
export VOICE_ID="${VOICE_ID:-narrador_principal}"
export DEFAULT_LANGUAGE="${DEFAULT_LANGUAGE:-Portuguese}"
export X_VECTOR_ONLY_MODE="${X_VECTOR_ONLY_MODE:-false}"
export GENERATION_UNIT="${GENERATION_UNIT:-pericope}"
export CHAPTER_INTRO_PAUSE_SECONDS="${CHAPTER_INTRO_PAUSE_SECONDS:-1.0}"
export PERICOPE_PAUSE_SECONDS="${PERICOPE_PAUSE_SECONDS:-0.3}"
export EXTRACT_VERSE_TIMINGS="${EXTRACT_VERSE_TIMINGS:-true}"
export WHISPER_MODEL="${WHISPER_MODEL:-medium}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p "${OUTPUT_DIR}" "${ASSET_CACHE_DIR}"

if [[ "${DAEMON}" == "true" ]]; then
  nohup "${PYTHON_BIN}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}" > "${LOG_PATH}" 2>&1 &
  echo "API started on ${HOST}:${PORT}. Logs: ${LOG_PATH}"
  echo "PID: $!"
else
  echo "Starting API on ${HOST}:${PORT} with GENERATION_UNIT=${GENERATION_UNIT}"
  exec "${PYTHON_BIN}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}"
fi
