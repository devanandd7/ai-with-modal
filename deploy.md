# AI Server Deploy Guide

## 🔐 Security First — API Keys Kabhi Expose Nahi Hote

**Architecture (secure by design):**

```
Your Browser (UI)
    ↓  HTTPS request (no API keys, sirf prompt)
Modal Server (app_test.py)
    ↓  reads API keys from Modal Secrets (encrypted env vars)
Groq API / Gemini API / self-hosted Qwen
    ↓  response wapas Modal server ko
Your Browser (UI)
```

- API keys **sirf Modal server ke andar** rehte hain — kabhi client/browser tak nahi pahunchte
- Keys **code me nahi likhni** — Modal Secrets me store hoti hain, encrypted
- Git me kuch nahi jaata (`.gitignore` me `.env` already hai)
- UI sirf prompt bhejta hai, API keys ki zaroorat nahi

---

## Pehle Baar Setup

```bash
pip install modal
python -m modal token new          # browser khulega, signup karo
```

## API Keys Set Karna (Modal Secrets)

**Step 1: Secret create karo (ek baar)**

```bash
modal secret create ai-server-keys \
  GROQ_API_KEY="gsk_your_groq_api_key_here" \
  GEMINI_API_KEY="AIza_your_gemini_api_key_here"
```

> 💡 **Free API keys kahan se milein:**
> - **Groq:** https://console.groq.com/keys → "Create API Key" (free tier: ~30 req/min)
> - **Gemini:** https://aistudio.google.com/apikey → "Create API Key" (free tier: 60 req/min)
> - **Qwen (OSS):** koi API key nahi chahiye — Modal ke T4 GPU pe self-hosted hai

**Optional — Auth bhi enable karna chahte ho to:**

```bash
modal secret create ai-server-keys \
  GROQ_API_KEY="gsk_..." \
  GEMINI_API_KEY="AIza_..." \
  API_KEY="kuch-bhi-password-daalo"
```

`API_KEY` set karoge to har request me `X-API-Key` header dena hoga (avoid public abuse).

**Step 2: Secret verify karo**

```bash
modal secret list
# → ai-server-keys dikhna chahiye
```

## Deploy / Redeploy

```bash
cd "D:\CrossEye startup\try projects\modal ai server"
modal deploy app_test.py
```

> ⚠️ **Important:** Deploy karte waqt Modal automatically `ai-server-keys` secret 
> ko detect karega (kyunki `app_test.py` me `GROQ_API_KEY` / `GEMINI_API_KEY` 
> environment variables use ho rahe hain). Agar nahi detect kare to manually 
> attach karo:
> ```bash
> modal deploy app_test.py --secret ai-server-keys
> ```

## Test Karna

```bash
# Server alive?
curl https://crosseye315--ai-server-web.modal.run/ping

# Available models check
curl https://crosseye315--ai-server-web.modal.run/models

# Auto mode (Groq → Gemini → Qwen fallback)
curl -X POST https://crosseye315--ai-server-web.modal.run/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain quantum computing in 3 lines"}' -m 180

# Specific model choose karo
curl -X POST https://crosseye315--ai-server-web.modal.run/generate_stream \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hi!", "model": "gemini"}' -m 180

# Agar API_KEY set hai to header dena hoga:
curl -X POST https://crosseye315--ai-server-web.modal.run/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: kuch-bhi-password-daalo" \
  -d '{"prompt": "Hello"}' -m 180
```

Browser me `/docs` kholo (Swagger UI se bhi test kar sakte ho).

## Models & Auto-Fallback

| Model | API Key | Free Tier | Priority |
|-------|---------|-----------|----------|
| **Groq** (Llama 3.1 70B) | `GROQ_API_KEY` ✅ | ~1M tokens/day | 1st (try first) |
| **Gemini** (Gemini 2.0 Flash) | `GEMINI_API_KEY` ✅ | ~1M tokens/day | 2nd (fallback) |
| **Qwen** (Qwen 2.5 7B) | None (self-hosted) | $30 Modal credit | 3rd (last resort) |

Auto mode me: Groq → Gemini → Qwen. Har provider ka usage track hota hai, 
jab <15% tokens bache to next provider pe switch ho jata hai.

## Logs

```bash
modal app logs ai-server
```

## Delete

```bash
modal app delete ai-server
modal secret delete ai-server-keys
```

## Cost

- **Groq/Gemini:** Free (API key based, unlimited free tier requests)
- **Qwen:** Modal ke $30 free credit se chalta hai (~60,000 requests)
