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
CHUNK_MAX_CHARS=400
CHAPTER_INTRO_PAUSE_SECONDS=1.0
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
  "chapter_intro_pause_seconds": 1.0,
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
  "audio_url": "/download/salmos/23",
  "metadata_path": "/outputs/default/salmos/metadata/salmos_023.json",
  "metadata_url": "/download/salmos/23/metadata",
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

Baixar áudio gerado:

```bash
curl -L "http://127.0.0.1:8000/download/salmos/23" -o salmos_023.mp3
```

Baixar metadata:

```bash
curl -L "http://127.0.0.1:8000/download/salmos/23/metadata" -o salmos_023.json
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

## Pausa Após Título

Quando `include_chapter_intro=true`, a aplicação narra o título do capítulo, por exemplo `Salmos, capítulo 2.`, insere uma pausa e depois começa o texto bíblico.

O valor padrão é configurado por:

```env
CHAPTER_INTRO_PAUSE_SECONDS=1.0
```

Você também pode sobrescrever por chamada:

```json
{
  "include_chapter_intro": true,
  "chapter_intro_pause_seconds": 1.25
}
```

Use `0` para não inserir pausa artificial. Valores negativos ou acima de `10` segundos são rejeitados.

## RunPod

### Pod Manual

Para desenvolvimento e depuração, comece com um RunPod Pod normal usando uma imagem PyTorch/CUDA. Esse fluxo é mais fácil para validar dependências, modelo, assets por URL e geração de capítulos antes de migrar para Serverless.

Instale dependências do sistema dentro do Pod:

```bash
apt-get update
apt-get install -y git ffmpeg sox libsndfile1 sqlite3
```

Essas dependências são necessárias porque:

- `git`: clonar e atualizar o repositório.
- `ffmpeg`: concatenar chunks e codificar o MP3 final.
- `sox`: usado pela stack de áudio do `qwen-tts`.
- `libsndfile1`: necessário para leitura/escrita de áudio via `soundfile`.
- `sqlite3`: útil para inspecionar e validar o banco bíblico no Pod.

Clone e instale dependências Python:

```bash
cd /workspace
git clone https://github.com/App2Sales/spoken_bible_generator.git
cd spoken_bible_generator
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Configure variáveis de ambiente para o Pod:

```bash
export OUTPUT_DIR=/workspace/outputs
export ASSET_CACHE_DIR=/workspace/assets
export MODEL_ID=Qwen/Qwen3-TTS-12Hz-1.7B-Base
export TTS_MODE=voice_clone
export VOICE_ID=narrador_principal
export DEFAULT_LANGUAGE=Portuguese
export X_VECTOR_ONLY_MODE=false
export CHUNK_MAX_CHARS=400
export CHAPTER_INTRO_PAUSE_SECONDS=1.0
```

Inicie a API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Para manter rodando em background no Pod:

```bash
nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 > /workspace/uvicorn.log 2>&1 &
```

Teste:

```bash
curl http://127.0.0.1:8000/health
```

Se usar o proxy público do RunPod, chamadas longas para `/generate` podem retornar `524` por timeout do Cloudflare mesmo que o worker continue processando. Para capítulos longos, gere dentro do Pod via `curl http://127.0.0.1:8000/generate` ou use um fluxo assíncrono/serverless depois.

### Serverless

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
    "chapter_intro_pause_seconds": 1.0,
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
