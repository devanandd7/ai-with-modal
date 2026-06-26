# AI Server - Commands

## Deploy / Redeploy

```bash
cd "d:\CrossEye startup\try projects\modal ai server"
python -m modal deploy app_test.py
```

## UI Tester (Recommended)

```bash
python ui.py
```

Deep testing UI with:
- Token-by-token streaming (like ChatGPT typing)
- Normal mode (full response at once)
- Ping, Status, Generate buttons
- Settings: max_tokens, temperature, top_p
- History with double-click detail view
- Quick Tests tab (6 pre-set prompts)
- **Token count** (input, output, total)
- **Cost tracking** per request
- **Remaining credits** estimation (~42M requests from $30)

## Test via Commands

```bash
# 1. Ping - server alive? + credit info
curl https://crosseye315--ai-server-web.modal.run/ping

# 2. Status - AI kaam kar raha hai?
curl https://crosseye315--ai-server-web.modal.run/status -m 180

# 3. Generate - token count + cost ke saath
curl -X POST https://crosseye315--ai-server-web.modal.run/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Python kya hai?","max_tokens":100}' -m 180

# 4. Stream - token by token (SSE format)
curl -N -X POST https://crosseye315--ai-server-web.modal.run/generate_stream \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Hi!","max_tokens":20}'
```

## Endpoints

| Endpoint | Response | Use |
|----------|----------|-----|
| `GET /ping` | `{"status","model","free_credit_usd"}` | Health + credit check |
| `GET /status` | `{"status":"pass"/"fail", "ai_response"}` | AI working? |
| `POST /generate` | `{"success","response","usage"}` | Full response + token count + cost |
| `POST /generate_stream` | SSE stream | Token-by-token + final usage |

## Usage Response (JSON)

```json
{
  "input_tokens": 39,
  "output_tokens": 17,
  "total_tokens": 56,
  "estimated_cost_usd": 0.000003,
  "remaining_requests_approx": 10714285
}
```

## Swagger UI

```
https://crosseye315--ai-server-web.modal.run/docs
```

## Logs

```bash
python -m modal app logs ai-server
```

## Delete

```bash
python -m modal app delete ai-server
```
