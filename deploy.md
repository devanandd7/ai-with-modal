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
- Keys **code me nahi likhni** — `.env` file me locally rakho, Modal Secrets me deploy hota hai
- `.env` **`.gitignore` me hai** — kabhi commit nahi hoga
- UI sirf prompt bhejta hai, API keys ki zaroorat nahi

---

## Quick Deploy (Sabse Aasan)

Sirf **2 cheezein** karni hain:

### 1. Pehli Baar Setup

```bash
pip install modal
python -m modal token new    # browser khulega, signup karo
```

### 2. `.env` File Banao

```bash
copy .env.example .env
```

Ab `.env` file kholo aur apni API keys daalo:
```
GROQ_API_KEY=gsk_...        # https://console.groq.com/keys
GEMINI_API_KEY=AIza_...     # https://aistudio.google.com/apikey
API_KEY=                    # optional: rakho empty agar auth nahi chahiye
```

### 3. Deploy Karo (Ek Hi Command)

```bash
deploy.bat
```

Ye **ek saath** karega:
1. `.env` file padhega
2. `Modal Secret` create karega (encrypted)
3. Server deploy karega

**Bas!** API keys manually Modal dashboard me nahi dalni padti.

---

## Manual Deploy (Bina deploy.bat ke)

Agar `deploy.bat` use nahi karna, to manual bhi kar sakte ho:

### Step 1: Modal Secret Create Karo

```bash
modal secret create ai-server-keys GROQ_API_KEY=gsk_... GEMINI_API_KEY=AIza_...
```

Optional auth:
```bash
modal secret create ai-server-keys GROQ_API_KEY=gsk_... GEMINI_API_KEY=AIza_... API_KEY=mypassword
```

### Step 2: Deploy Karo

```bash
modal deploy app_test.py
```

### Secret Update Karna

```bash
modal secret create ai-server-keys GROQ_API_KEY=new_key GEMINI_API_KEY=new_key
# Same command se overwrite ho jayega
```

> 💡 **Free API keys kahan se milein:**
> - **Groq:** https://console.groq.com/keys → "Create API Key" (free tier: ~30 req/min)
> - **Gemini:** https://aistudio.google.com/apikey → "Create API Key" (free tier: 60 req/min)
> - **Qwen (OSS):** koi API key nahi chahiye — Modal ke T4 GPU pe self-hosted hai

---

## Test Karna

```bash
# Server alive?
curl https://crosseye315--ai-server-web.modal.run/ping

# Available models + remaining tokens check
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
  -H "X-API-Key: mypassword" \
  -d '{"prompt": "Hello"}' -m 180
```

Browser me `/docs` kholo (Swagger UI se bhi test kar sakte ho).

---

## Models & Auto-Fallback

| Model | API Key | Free Tier | Priority |
|-------|---------|-----------|----------|
| **Groq** (Llama 3.1 70B) | `GROQ_API_KEY` ✅ | ~1M tokens/day | 1st (try first) |
| **Gemini** (Gemini 2.0 Flash) | `GEMINI_API_KEY` ✅ | ~1M tokens/day | 2nd (fallback) |
| **Qwen** (Qwen 2.5 7B) | None (self-hosted) | $30 Modal credit | 3rd (last resort) |

Auto mode me: Groq → Gemini → Qwen. Har provider ka usage track hota hai,
jab <15% tokens bache to next provider pe switch ho jata hai.

---

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
