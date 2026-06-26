# AI Server Deploy Guide (Hinglish)

## Pehle Baar Setup

```bash
pip install modal
python -m modal token new          # browser khulega, signup karo
```

## Deploy / Redeploy

```bash
cd "d:\CrossEye startup\try projects\modal ai server"
python -m modal deploy app_test.py
```

## Endpoints

| Endpoint | Kya Hai |
|----------|---------|
| `GET /ping` | Server alive? (turant) |
| `GET /status` | AI test - model ko prompt bhejkar verify karta hai |
| `POST /generate` | AI se jawab lo |

## Test Commands

```bash
# Server alive check
curl https://crosseye315--ai-server-web.modal.run/ping

# AI working check (cold start ~60-90 sec pehli baar)
curl https://crosseye315--ai-server-web.modal.run/status -m 180

# Apna prompt do
curl -X POST https://crosseye315--ai-server-web.modal.run/generate \
  -H "Content-Type: application/json" \
  -d "{\"prompt\": \"Namaste! Kaise ho?\"}" -m 180
```

Browser me `/docs` kholo (Swagger UI se bhi test kar sakte ho).

## Status Response

Agar AI sahi kam kar raha hai:
```json
{"status": "pass", "ai_response": "ok", ...}
```

Agar fail hai:
```json
{"status": "fail", "ai_error": "...", ...}
```

## Logs

```bash
python -m modal app logs ai-server
```

## Delete

```bash
python -m modal app delete ai-server
```

## Cost

$30 free credit → ~60,000 requests free. 4 users ke liye basically muft hai.
