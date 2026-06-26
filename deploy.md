# 🚀 Modal AI Server Deploy Guide (Hinglish)

---

## Step 0: Systemd Ready ho?

**Pehle yeh install karo:**
```bash
pip install modal
```

---

## Step 1: Modal Account Banao (FREE, $30 Credit Milega)

```bash
modal token new
```

> Yeh command browser khol degi → GitHub/Google se signup karo → done!  
> **$30 FREE credit milta hai**, matlab ~2 saal tak free chalega 4 users ke liye.

---

## Step 2: Deploy Karo

Ek hi command:
```bash
modal deploy app.py
```

Pehli baar deploy hone me **2-3 minute** lagenge (model download hoga 9GB).  
Baad me fast hoga (cache se load hoga).

---

## Step 3: URL Lo

Deploy hone ke baad terminal me aisa kuchh dikhega:
```
✓ Created Function: personal-ai-server-generate
✓ Created Function: personal-ai-server-web
  Web Endpoint: https://YOUR-NAME--personal-ai-server-web.modal.run
```

**Yehi tumhari server ki public URL hai!** 🎉

---

## Step 4: Test Karo

```bash
# cURL se:
curl -X POST https://YOUR-NAME--personal-ai-server-web.modal.run/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Python me fibonacci function likho"}'

# Ya Python client se:
python client.py "Mumbai ka famous food kya hai?"
```

---

## Step 5 (Optional): Friends Ko Share Karo

URL + unka API key de do. Bas ho gaya.

---

## 🧹 Useful Commands

| Kaam | Command |
|------|---------|
| Deploy | `modal deploy app.py` |
| Test locally | `modal run app.py` |
| Logs dekhna | `modal app logs personal-ai-server` |
| Server band karna | `modal app stop personal-ai-server` |
| Sab delete karna | `modal app delete personal-ai-server` |

---

## 💸 Cost (Kitna Paisa Lagega?)

| Use Case | Requests/Month | Cost |
|----------|---------------|------|
| 4 log × 10 req/day | 300 | **₹2-3** |
| 4 log × 50 req/day | 1500 | **₹10-15** |
| Always ON | — | ₹50/month |

> **Modal ke $30 credit se ~60,000+ FREE requests ho jayenge!**  
> Matlab 4 log normal use karenge to **2 saal tak muft** chalega.

---

## ❓ Problems?

| Problem | Solution |
|---------|----------|
| `modal: command not found` | `pip install modal` karo |
| `model download slow` | Pehli baar 2-5 min lagega, baad me fast |
| `out of memory` | Model already 4-bit quantized hai, T4 pe fit hoga |
| `cold start slow` | 5 min idle ke baad band hota hai, agli request pe 60s lagta hai |

---

**Bas itna hi! Koi dikkat aaye to batao.** 👇
