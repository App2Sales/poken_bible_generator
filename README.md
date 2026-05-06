# Spoken Bible Generator

MVP para gerar capítulos bíblicos narrados com voice cloning usando `Qwen/Qwen3-TTS-12Hz-1.7B-Base`.

## Padrão de TTS

O modo principal é `voice_clone`. Não use `CustomVoice` como caminho principal.

Variáveis recomendadas para produção:

```env
BIBLE_DB_PATH=/data/bible.sqlite
OUTPUT_DIR=/outputs
ASSET_CACHE_DIR=/data/assets
MODEL_ID=Qwen/Qwen3-TTS-12Hz-1.7B-Base
TTS_MODE=voice_clone
REF_AUDIO_PATH=/data/voices/narrador.wav
REF_TEXT_PATH=/data/voices/narrador.txt
VOICE_ID=narrador_principal
DEFAULT_LANGUAGE=Portuguese
X_VECTOR_ONLY_MODE=false
```

No startup, a aplicação carrega o modelo uma vez. O `voice_clone_prompt` é criado uma vez por conjunto de assets, identificado por `voice_id`, SHA-256 do áudio de referência, SHA-256 da transcrição e `X_VECTOR_ONLY_MODE`.

Quando os assets são enviados no request, a aplicação baixa os arquivos, salva em cache local em `ASSET_CACHE_DIR` e cria ou reutiliza o prompt correspondente. Quando os assets não são enviados, usa `BIBLE_DB_PATH`, `REF_AUDIO_PATH` e `REF_TEXT_PATH` como fallback.

O prompt é criado com:

```python
model.create_voice_clone_prompt(
    ref_audio=REF_AUDIO_PATH,
    ref_text=REF_TEXT,
    x_vector_only_mode=False,
)
```

Esse prompt é reutilizado em todos os chunks e capítulos que usam os mesmos assets. Ele não é recriado por chunk nem por capítulo.

## Áudio de Referência

Para melhor qualidade, use um áudio de referência com 20 a 60 segundos, uma única voz, fala natural, sem música, sem ruído e com transcrição exata em `REF_TEXT_PATH`.

Se o áudio tiver música, ruído, reverberação forte ou múltiplos falantes, a qualidade do clone pode cair. O MVP não faz limpeza avançada de áudio.

`X_VECTOR_ONLY_MODE=true` é permitido apenas para teste e registra aviso em log, porque a qualidade pode ser menor. Para produção, use `X_VECTOR_ONLY_MODE=false`; nesse modo `REF_TEXT_PATH` é obrigatório e não pode estar vazio.

## API

Inicie localmente:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`POST /generate`:

```json
{
  "book": "Salmos",
  "chapter": 23,
  "voice_id": "narrador_principal",
  "language": "Portuguese",
  "format": "mp3",
  "bitrate": "192k",
  "include_headings": false,
  "include_verse_numbers": false,
  "include_chapter_intro": true,
  "force": false,
  "upload": true,
  "assets": {
    "bible_db_url": "https://exemplo.com/bible.sqlite",
    "ref_audio_url": "https://exemplo.com/narrador.wav",
    "ref_text_url": "https://exemplo.com/narrador.txt"
  }
}
```

O campo `assets` é opcional. Se ele for omitido, a API usa os paths das variáveis de ambiente.

Resposta esperada:

```json
{
  "status": "completed",
  "book_id": 19,
  "book": "Salmos",
  "chapter": 23,
  "voice_id": "narrador_principal",
  "audio_path": "/outputs/default/salmos/salmos_023.mp3",
  "audio_url": "https://...",
  "metadata_path": "/outputs/default/salmos/metadata/salmos_023.json",
  "duration_seconds": 123.45,
  "sha256": "...",
  "input_hash": "...",
  "bible_db_sha256": "...",
  "ref_audio_sha256": "...",
  "ref_text_sha256": "...",
  "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
  "tts_mode": "voice_clone"
}
```

`GET /voice`:

```json
{
  "voice_id": "narrador_principal",
  "ref_audio_path_exists": true,
  "ref_text_path_exists": true,
  "ref_audio_sha256": "...",
  "ref_text_sha256": "...",
  "x_vector_only_mode": false,
  "cached_voice_prompts": 1
}
```

## Assets por URL

Você pode trocar o SQLite, o áudio e a transcrição por chamada REST usando `assets`:

```json
{
  "assets": {
    "bible_db_url": "https://exemplo.com/bible.sqlite",
    "ref_audio_url": "https://exemplo.com/narrador.wav",
    "ref_text_url": "https://exemplo.com/narrador.txt"
  }
}
```

Regras:

- URLs precisam ser `http` ou `https` e acessíveis pelo worker RunPod.
- Arquivos baixados são armazenados por SHA-256 em `ASSET_CACHE_DIR`, com índice por URL para evitar novo download a cada capítulo.
- Jobs com os mesmos arquivos reutilizam os assets já baixados e o mesmo `voice_clone_prompt` em memória.
- Se você trocar o arquivo mantendo a mesma URL, envie `force=true` para baixar novamente e atualizar o cache.
- Se `X_VECTOR_ONLY_MODE=false`, `ref_text_url` ou `REF_TEXT_PATH` precisa existir e conter a transcrição exata.
- Se `assets.bible_db_url` não for enviado, usa `BIBLE_DB_PATH`.
- Se `assets.ref_audio_url` não for enviado, usa `REF_AUDIO_PATH`.
- Se `assets.ref_text_url` não for enviado, usa `REF_TEXT_PATH`.

## Cache e Metadata

O `input_hash` considera `book_id`, `chapter`, texto completo do capítulo, `model_id`, `tts_mode`, `voice_id`, SHA-256 do SQLite, SHA-256 do áudio de referência, SHA-256 da transcrição, idioma, flags de inclusão e `bitrate`.

O metadata JSON é salvo em `/outputs/default/<livro>/metadata/<livro>_<capitulo>.json` e inclui `bible_db_sha256`, `ref_audio_sha256`, `ref_text_sha256`, URLs dos assets, chunks, duração, SHA-256 do áudio e `input_hash`.

## RunPod

Formato de chamada:

```json
{
  "input": {
    "book": "Salmos",
    "chapter": 23,
    "voice_id": "narrador_principal",
    "language": "Portuguese",
    "include_headings": false,
    "include_verse_numbers": false,
    "include_chapter_intro": true,
    "force": false,
    "upload": true,
    "assets": {
      "bible_db_url": "https://exemplo.com/bible.sqlite",
      "ref_audio_url": "https://exemplo.com/narrador.wav",
      "ref_text_url": "https://exemplo.com/narrador.txt"
    }
  },
  "policy": {
    "executionTimeout": 1800000,
    "ttl": 7200000
  }
}
```

O handler em `runpod_handler.py` usa o mesmo `GenerationService` global, carrega o modelo uma vez por worker e reutiliza prompts cacheados por voz/assets.

## Docker

A imagem pode receber os arquivos por URL no request. Se você não enviar `assets`, ela espera estes arquivos ou volumes:

```text
/data/bible.sqlite
/data/voices/narrador.wav
/data/voices/narrador.txt
```

Build:

```bash
docker build -t spoken-bible-generator .
```

Run:

```bash
docker run --gpus all --rm -p 8000:8000 \
  -v /caminho/data:/data \
  -v /caminho/outputs:/outputs \
  spoken-bible-generator
```

## Gerar Salmos 1 a 150

Com a API rodando:

```bash
python scripts/generate_psalms.py --api-url http://127.0.0.1:8000/generate --start 1 --end 150
```

Com assets por URL:

```bash
python scripts/generate_psalms.py \
  --api-url http://127.0.0.1:8000/generate \
  --start 1 \
  --end 150 \
  --bible-db-url https://exemplo.com/bible.sqlite \
  --ref-audio-url https://exemplo.com/narrador.wav \
  --ref-text-url https://exemplo.com/narrador.txt
```
