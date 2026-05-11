# Spoken Bible Generator

MVP para gerar capítulos bíblicos narrados com voice cloning usando OmniVoice.

## Padrão de TTS

O único backend suportado agora é OmniVoice. O modelo é carregado uma vez no startup e o `voice_clone_prompt` é criado uma vez por conjunto de assets, identificado por `voice_id`, SHA-256 do áudio de referência e SHA-256 da transcrição.

Variáveis recomendadas:

```env
BIBLE_DB_PATH=/data/bible.sqlite
OUTPUT_DIR=/outputs
ASSET_CACHE_DIR=/data/assets
TTS_BACKEND=omnivoice
MODEL_ID=k2-fsa/OmniVoice
TTS_MODE=voice_clone
REF_AUDIO_PATH=/data/voices/narrador.wav
REF_TEXT_PATH=/data/voices/narrador.txt
VOICE_ID=narrador_principal
DEFAULT_LANGUAGE=Portuguese
X_VECTOR_ONLY_MODE=false
GENERATION_UNIT=chapter
CHAPTER_INTRO_PAUSE_SECONDS=1.0
PERICOPE_PAUSE_SECONDS=0.3
```

Config OmniVoice base usada nos melhores testes até agora:

```json
{
  "num_step": 32,
  "guidance_scale": 2.0,
  "denoise": true,
  "speed": 1.0,
  "duration": null,
  "preprocess_prompt": true,
  "postprocess_output": true,
  "instruct": ""
}
```

Ela é o baseline atual porque é o default do demo oficial e venceu os testes subjetivos feitos até agora. Ajuste uma variável por vez ao experimentar.

## Referência de Voz

OmniVoice recomenda áudio de referência com 3 a 10 segundos, voz única, fala natural, sem música e com transcrição exata. Áudios longos podem causar uso excessivo de VRAM ou queda de qualidade.

`X_VECTOR_ONLY_MODE=true` é mantido apenas por compatibilidade de configuração; OmniVoice não usa esse modo. Para produção, use `X_VECTOR_ONLY_MODE=false` e forneça transcrição.

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
  "tts_backend": "omnivoice",
  "model_id": "k2-fsa/OmniVoice",
  "language": "Portuguese",
  "format": "mp3",
  "bitrate": "192k",
  "include_headings": false,
  "include_verse_numbers": false,
  "include_chapter_intro": true,
  "chapter_intro_pause_seconds": 1.0,
  "pericope_pause_seconds": 0.3,
  "generation_unit": "chapter",
  "force": false,
  "upload": true,
  "omnivoice": {
    "num_step": 32,
    "guidance_scale": 2.0,
    "denoise": true,
    "speed": 1.0,
    "preprocess_prompt": true,
    "postprocess_output": true,
    "instruct": ""
  },
  "assets": {
    "bible_db_url": "https://exemplo.com/bible.sqlite",
    "ref_audio_url": "https://exemplo.com/narrador.wav",
    "ref_text": "Texto exato do áudio de referência"
  }
}
```

`generation_unit` é opcional por request. Se omitido, usa `GENERATION_UNIT`. Valores aceitos: `chapter` e `pericope`.

`pericope_pause_seconds` é opcional por request. Se omitido, usa `PERICOPE_PAUSE_SECONDS`, com default `0.3`. A pausa é inserida apenas entre perícopes quando o modo efetivo é `pericope` e o capítulo tem mais de uma perícope.

`extract_verse_timings` controla a extração de timings por versículo com Whisper. Se omitido, usa `EXTRACT_VERSE_TIMINGS`, com default `true`. O idioma do Whisper é derivado do campo `language` da request; não há detecção automática de idioma. `whisper_model` é opcional e usa `WHISPER_MODEL`, com default `medium`.

Resposta inclui `requested_generation_unit`, `generation_unit`, `generation_units`, `tts_backend`, `omnivoice_options`, hashes dos assets, chunks de áudio, pausas, duração, SHA-256 do MP3 e `input_hash`.

Quando `generation_unit=pericope`, cada item de `generation_units` registra `title`, `start_verse`, `end_verse`, `text_chars` e `sample_rate`.

`chapter` envia o corpo inteiro do capítulo em uma chamada `model.generate()`. `pericope` agrupa pelos versículos iniciais registrados na tabela opcional `pericopes`; se o banco não tiver perícopes, o modo efetivo cai para `chapter` e a metadata registra `requested_generation_unit` diferente de `generation_unit`.

Para `pericope`, o app lê a tabela opcional `pericopes`. A tabela `texts` não é alterada, preservando compatibilidade com apps existentes que usam o mesmo SQLite.

Baixar áudio gerado:

```bash
curl -L "http://127.0.0.1:8000/download/salmos/23?backend=omnivoice" -o salmos_023.mp3
```

Baixar metadata:

```bash
curl -L "http://127.0.0.1:8000/download/salmos/23/metadata?backend=omnivoice" -o salmos_023.json
```

Baixar timings por versículo:

```bash
curl -L "http://127.0.0.1:8000/download/salmos/23/timings?backend=omnivoice&format=json" -o salmos_023.timings.json
curl -L "http://127.0.0.1:8000/download/salmos/23/timings?backend=omnivoice&format=whisper-json" -o salmos_023.whisper.json
curl -L "http://127.0.0.1:8000/download/salmos/23/timings?backend=omnivoice&format=srt" -o salmos_023.srt
curl -L "http://127.0.0.1:8000/download/salmos/23/timings?backend=omnivoice&format=vtt" -o salmos_023.vtt
```

Gerar timings para áudio já existente, sem regenerar TTS:

```bash
curl -X POST "http://127.0.0.1:8000/timings/salmos/23" \
  -H "Content-Type: application/json" \
  -d '{"language":"Portuguese","whisper_model":"medium"}'
```

Batch para áudios existentes:

```bash
python scripts/generate_verse_timings.py --api-base-url http://127.0.0.1:8000 --bible-db bibles/naa.db
```

O JSON canônico usa o texto da NAA como fonte de verdade e usa o Whisper apenas como fonte de timestamps. O alinhamento é fuzzy/global por capítulo, ignora a introdução do capítulo e registra `confidence`, `matched_words`, `total_words` e `method` para cada versículo.

## Assets por Request

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

Também é possível enviar a transcrição diretamente como texto:

```json
{
  "assets": {
    "bible_db_url": "https://exemplo.com/bible.sqlite",
    "ref_audio_url": "https://exemplo.com/narrador.wav",
    "ref_text": "Texto exato do áudio de referência"
  }
}
```

Regras:

- URLs precisam ser `http` ou `https` e acessíveis pelo worker.
- Arquivos baixados são armazenados por SHA-256 em `ASSET_CACHE_DIR`.
- Envie apenas um de `assets.ref_text` ou `assets.ref_text_url`.
- Se `assets.bible_db_url` não for enviado, usa `BIBLE_DB_PATH`.
- Se `assets.ref_audio_url` não for enviado, usa `REF_AUDIO_PATH`.
- Se nem `assets.ref_text` nem `assets.ref_text_url` forem enviados, usa `REF_TEXT_PATH`.

## Cache e Metadata

O `input_hash` considera `book_id`, `chapter`, texto completo do capítulo, `model_id`, `tts_mode`, `tts_backend`, `voice_id`, SHA-256 do SQLite, SHA-256 do áudio de referência, SHA-256 da transcrição, idioma, flags de inclusão, pausa do título, pausa entre perícopes, `bitrate`, `requested_generation_unit` e opções OmniVoice normalizadas.

O metadata JSON é salvo em `/outputs/omnivoice/<livro>/metadata/<livro>_<capitulo>.json`. Áudios são salvos em `/outputs/omnivoice/<livro>/<livro>_<capitulo>.mp3`. Timings são salvos em `/outputs/omnivoice/<livro>/timings/<livro>_<capitulo>.*`.

## Enriquecer Perícopes

O enriquecimento adiciona apenas uma tabela nova e leve ao SQLite:

```sql
create table if not exists pericopes (
  _id integer primary key,
  book_id integer not null,
  chapter_num integer not null,
  verse integer not null,
  title text not null,
  ntitle text
);

create unique index if not exists pericopes_unique_start
on pericopes (book_id, chapter_num, verse);
```

Gerar a tabela usando o scraper local e a versão NAA `1840`:

```bash
python scripts/enrich_pericopes.py \
  --db-path bibles/naa.db \
  --scraper-dir /Users/samuelbezerrab/Developer/node/bible-scraper-fork \
  --version-id 1840
```

Use `--dry-run` para validar sem escrever. Por padrão, o script faz upsert incremental; use `--force` para recriar as perícopes dos livros selecionados. O script cria um backup antes de modificar o banco.

## RunPod Pod Manual

Clone o projeto:

```bash
cd /workspace
git clone https://github.com/App2Sales/spoken_bible_generator.git
cd spoken_bible_generator
```

Instale dependências e valide CUDA/OmniVoice:

```bash
bash scripts/runpod_install.sh
```

Inicie a API em foreground:

```bash
bash scripts/runpod_start_api.sh
```

Ou instale e inicie em um único comando:

```bash
bash scripts/runpod_bootstrap.sh
```

Para rodar em background:

```bash
bash scripts/runpod_start_api.sh --daemon
```

Os scripts usam `GENERATION_UNIT=pericope` e `PERICOPE_PAUSE_SECONDS=0.3` por padrão. Para testar capítulo inteiro, rode `GENERATION_UNIT=chapter bash scripts/runpod_start_api.sh`.

Teste:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/voice
```

## Serverless

Formato de chamada:

```json
{
  "input": {
    "book": "Salmos",
    "chapter": 23,
    "voice_id": "narrador_principal",
    "tts_backend": "omnivoice",
    "language": "Portuguese",
    "generation_unit": "chapter",
    "include_headings": false,
    "include_verse_numbers": false,
    "include_chapter_intro": true,
    "chapter_intro_pause_seconds": 1.0,
    "pericope_pause_seconds": 0.3,
    "force": false,
    "upload": true,
    "assets": {
      "bible_db_url": "https://exemplo.com/bible.sqlite",
      "ref_audio_url": "https://exemplo.com/narrador.wav",
      "ref_text": "Texto exato do áudio de referência"
    }
  },
  "policy": {
    "executionTimeout": 1800000,
    "ttl": 7200000
  }
}
```

## Docker

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

```bash
python scripts/generate_psalms.py \
  --api-url http://127.0.0.1:8000/generate \
  --start 1 \
  --end 150 \
  --tts-backend omnivoice \
  --model-id k2-fsa/OmniVoice \
  --generation-unit chapter \
  --pericope-pause-seconds 0.3 \
  --bible-db-url https://exemplo.com/bible.sqlite \
  --ref-audio-url https://exemplo.com/narrador.wav \
  --ref-text "Texto exato do áudio de referência"
```

## TODO: Segmentação Interna OmniVoice

OmniVoice já faz segmentação interna para texto longo, usando controles como `audio_chunk_duration` e `audio_chunk_threshold`. Ainda não expomos esses controles na API.

Vantagens de expor depois: ajustar VRAM/latência para capítulos longos e usar o crossfade nativo do modelo. Riscos: sair dos defaults oficiais que performaram melhor nos testes, piorar prosódia/continuidade e depender de parâmetros internos menos estáveis.
