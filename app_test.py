"""
AI Server v4 — Multi-Model (Groq → Gemini → Qwen) with Priority Fallback
GPU: T4 | API-Key Auth | Max 2 concurrent
"""

import modal, time, torch, json, os
from pydantic import BaseModel, Field
from transformers import (
    AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
    TextIteratorStreamer
)
from threading import Thread

# ── Config ─────────────────────────────────────────────────────
# All values can be overridden via env vars (Modal Secrets or .env)
MODEL_ID = os.environ.get("QWEN_MODEL_ID", "Qwen/Qwen2.5-7B-Instruct")
COST_PER_1K = 0.00005
FREE_CREDIT = 30.0
MAX_TOKENS_DEFAULT = 1536
MAX_TOKENS_LIMIT = 4096
API_KEY = os.environ.get("API_KEY", "")

# Provider API keys (from Modal secrets / .env)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Model names (configurable via env vars)
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.1-70b-versatile")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Provider config (labels auto-update with model names)
PROVIDER_PRIORITY = ["groq", "gemini", "qwen"]
PROVIDER_LABELS = {
    "groq": f"Groq ({GROQ_MODEL})",
    "gemini": f"Gemini ({GEMINI_MODEL})",
    "qwen": f"{MODEL_ID.split('/')[-1]} (Self-hosted)",
}

# Approximate free-tier limits for auto-fallback
PROVIDER_LIMITS = {
    "groq": {"tokens": 1_000_000, "type": "tokens"},
    "gemini": {"tokens": 1_000_000, "type": "tokens"},
    "qwen": {"tokens": int(FREE_CREDIT / COST_PER_1K * 1000), "type": "tokens"},
}
SWITCH_THRESHOLD = 0.15  # switch when <15% remaining

app = modal.App("ai-server")
vol = modal.Volume.from_name("model-cache", create_if_missing=True)

# Persistent ledgers
cost_ledger = modal.Dict.from_name("cost-ledger", create_if_missing=True)
provider_usage = modal.Dict.from_name("provider-usage", create_if_missing=True)

img = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "transformers==4.49.0",
        "torch==2.6.0",
        "accelerate==1.4.0",
        "bitsandbytes==0.45.4",
        "fastapi==0.115.12",
        "openai==1.68.0",            # Groq (OpenAI-compatible)
        "google-genai==1.8.0",       # Gemini (newer SDK)
    )
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)


# ── Schemas ────────────────────────────────────────────────────

class Ask(BaseModel):
    prompt: str = Field(..., description="Your question")
    model: str = Field("auto", description="Model to use: auto, groq, gemini, qwen")
    max_tokens: int = Field(MAX_TOKENS_DEFAULT, ge=1, le=MAX_TOKENS_LIMIT)
    temperature: float = Field(0.7, ge=0.0, le=1.5)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    system_prompt: str = Field("", description="System prompt baked into chat template")


class Reply(BaseModel):
    success: bool
    response: str = ""
    error: str = ""
    time_sec: float = 0.0
    model: str = ""
    model_used: str = ""
    truncated: bool = False
    fallback_chain: list = []
    usage: dict = {}


# ── Web API ────────────────────────────────────────────────────

@app.function(
    image=img, gpu="T4",
    volumes={"/cache": vol},
    scaledown_window=300,
)
@modal.concurrent(max_inputs=2)
@modal.asgi_app()
def web():
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse
    from fastapi.middleware.cors import CORSMiddleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    # ── Auth middleware ────────────────────────────────────────
    if API_KEY:
        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if request.url.path in ("/", "/docs", "/openapi.json"):
                    return await call_next(request)
                key = request.headers.get("X-API-Key", "")
                if key != API_KEY:
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
                return await call_next(request)

    # ── Load Qwen model (lazy — only when needed) ─────────────
    tok, model = None, None

    def _ensure_qwen():
        nonlocal tok, model
        if model is not None:
            return
        print("Loading Qwen model...")
        _tok = AutoTokenizer.from_pretrained(MODEL_ID, cache_dir="/cache")
        _quant = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True, bnb_4bit_quant_type="nf4",
        )
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, cache_dir="/cache", device_map="auto",
            quantization_config=_quant,
        )
        if _tok.pad_token is None:
            _tok.pad_token = _tok.eos_token
        tok, model = _tok, _model
        print(f"Qwen ready on {torch.cuda.get_device_name(0)}")

    # ── Provider clients ──────────────────────────────────────
    groq_client = None
    if GROQ_API_KEY:
        from openai import OpenAI
        groq_client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

    gemini_client = None
    if GEMINI_API_KEY:
        from google import genai
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    # ── Usage tracking ────────────────────────────────────────
    def _get_provider_usage(provider):
        return provider_usage.get(f"{provider}_tokens", 0)

    def _add_provider_usage(provider, tokens):
        key = f"{provider}_tokens"
        for _ in range(5):
            cur = provider_usage.get(key, 0)
            if provider_usage.put(key, cur + tokens, if_value=cur):
                return cur + tokens
        provider_usage.put(key, provider_usage.get(key, 0) + tokens)
        return provider_usage.get(key, 0)

    def _provider_remaining(provider):
        used = _get_provider_usage(provider)
        limit = PROVIDER_LIMITS[provider]["tokens"]
        return max(0, limit - used)

    def _select_model(requested):
        """Select which provider to use based on request + availability + budget."""
        if requested != "auto":
            # Manual selection: just check if provider is configured
            if requested == "groq" and not GROQ_API_KEY:
                return "qwen", ["groq(skip:no-key)"]
            if requested == "gemini" and not GEMINI_API_KEY:
                return "qwen", ["gemini(skip:no-key)"]
            return requested, []

        # Auto: try priority order, check remaining budget
        fallback = []
        for prov in PROVIDER_PRIORITY:
            if prov == "groq" and not GROQ_API_KEY:
                fallback.append("groq(skip:no-key)")
                continue
            if prov == "gemini" and not GEMINI_API_KEY:
                fallback.append("gemini(skip:no-key)")
                continue
            remaining = _provider_remaining(prov)
            limit = PROVIDER_LIMITS[prov]["tokens"]
            ratio = remaining / limit if limit > 0 else 0
            if ratio > SWITCH_THRESHOLD:
                return prov, fallback
            fallback.append(f"{prov}(low:{int(ratio*100)}%)")
        # Everything is low — use last available
        for prov in reversed(PROVIDER_PRIORITY):
            if prov == "groq" and GROQ_API_KEY:
                return prov, fallback
            if prov == "gemini" and GEMINI_API_KEY:
                return prov, fallback
            if prov == "qwen":
                return prov, fallback
        return "qwen", fallback

    # ── Provider implementations ───────────────────────────────

    def _groq_generate(prompt, max_tokens, temp, top_p, system_prompt):
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temp,
            top_p=top_p,
            stream=False,
        )
        text = resp.choices[0].message.content.strip()
        usage = resp.usage
        inp_tok = usage.prompt_tokens if usage else 0
        out_tok = usage.completion_tokens if usage else 0
        truncated = resp.choices[0].finish_reason == "length"
        _add_provider_usage("groq", inp_tok + out_tok)
        return text, inp_tok, out_tok, truncated

    def _groq_stream(prompt, max_tokens, temp, top_p, system_prompt):
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        stream = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=temp,
            top_p=top_p,
            stream=True,
            stream_options={"include_usage": True},
        )
        inp_tok = 0
        full = ""
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                full += delta.content
                yield delta.content, None
            if chunk.usage:
                inp_tok = chunk.usage.prompt_tokens or 0
                out_tok = chunk.usage.completion_tokens or 0
                _add_provider_usage("groq", inp_tok + out_tok)
                truncated = chunk.choices[0].finish_reason == "length" if chunk.choices else False
                yield ("__DONE__", {
                    "input_tokens": inp_tok,
                    "output_tokens": out_tok,
                    "truncated": truncated,
                })

    def _gemini_generate(prompt, max_tokens, temp, top_p, system_prompt):
        contents = prompt
        if system_prompt:
            contents = f"{system_prompt}\n\n{prompt}"
        resp = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "max_output_tokens": max_tokens,
                "temperature": temp,
                "top_p": top_p,
            },
        )
        text = resp.text.strip()
        # Gemini doesn't return token counts in the same way — estimate
        inp_tok = max(1, len(prompt.split()) * 2)
        out_tok = max(1, len(text.split()) * 2)
        truncated = bool(getattr(resp, 'finish_reason', '') == 'MAX_TOKENS' or
                         getattr(resp.candidates[0], 'finish_reason', '') == 2)
        _add_provider_usage("gemini", inp_tok + out_tok)
        return text, inp_tok, out_tok, truncated

    def _gemini_stream(prompt, max_tokens, temp, top_p, system_prompt):
        contents = prompt
        if system_prompt:
            contents = f"{system_prompt}\n\n{prompt}"
        stream = gemini_client.models.generate_content_stream(
            model=GEMINI_MODEL,
            contents=contents,
            config={
                "max_output_tokens": max_tokens,
                "temperature": temp,
                "top_p": top_p,
            },
        )
        inp_tok = max(1, len(prompt.split()) * 2)
        out_tok = 0
        full = ""
        for chunk in stream:
            if chunk.text:
                full += chunk.text
                out_tok += max(1, len(chunk.text.split()))
                yield chunk.text, None
        _add_provider_usage("gemini", inp_tok + out_tok)
        truncated = False
        yield ("__DONE__", {
            "input_tokens": inp_tok,
            "output_tokens": out_tok,
            "truncated": truncated,
        })

    def _qwen_generate(prompt, max_tokens, temp, top_p, system_prompt):
        _ensure_qwen()
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        tokenized = tok.apply_chat_template(msgs, return_tensors="pt",
                                            add_generation_prompt=True)
        inp_ids = tokenized.input_ids.to(model.device)
        inp_len = inp_ids.shape[1]
        with torch.inference_mode():
            out = model.generate(
                inp_ids, max_new_tokens=max_tokens,
                temperature=temp, top_p=top_p, do_sample=(temp > 0),
                pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id,
            )
        out_len = out.shape[1] - inp_len
        truncated = out_len >= max_tokens
        text = tok.decode(out[0][inp_len:], skip_special_tokens=True).strip()

        cost = (inp_len + out_len) * COST_PER_1K / 1000
        for _ in range(5):
            cur = cost_ledger.get("total_spent", 0.0)
            if cost_ledger.put("total_spent", cur + cost, if_value=cur):
                break

        _add_provider_usage("qwen", inp_len + out_len)
        return text, inp_len, out_len, truncated

    def _qwen_stream(prompt, max_tokens, temp, top_p, system_prompt):
        _ensure_qwen()
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        tokenized = tok.apply_chat_template(msgs, return_tensors="pt",
                                            add_generation_prompt=True)
        inp_ids = tokenized.input_ids.to(model.device)
        inp_len = inp_ids.shape[1]
        streamer = TextIteratorStreamer(tok, skip_prompt=True, skip_special_tokens=True)
        kw = dict(input_ids=inp_ids, max_new_tokens=max_tokens,
                  temperature=temp, top_p=top_p, do_sample=(temp > 0),
                  pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id,
                  streamer=streamer)
        Thread(target=model.generate, kwargs=kw).start()
        out_tok = 0
        for t in streamer:
            out_tok += 1
            yield t, None
        cost = (inp_len + out_tok) * COST_PER_1K / 1000
        for _ in range(5):
            cur = cost_ledger.get("total_spent", 0.0)
            if cost_ledger.put("total_spent", cur + cost, if_value=cur):
                break
        _add_provider_usage("qwen", inp_len + out_tok)
        yield ("__DONE__", {
            "input_tokens": inp_len,
            "output_tokens": out_tok,
            "truncated": out_tok >= max_tokens,
        })

    # ── Router ────────────────────────────────────────────────
    def _route(provider, prompt, max_tokens, temp, top_p, system_prompt, mode="generate"):
        """Route to the right provider. Returns result in a standard format."""
        try:
            if provider == "groq":
                if mode == "generate":
                    return _groq_generate(prompt, max_tokens, temp, top_p, system_prompt)
                else:
                    return _groq_stream(prompt, max_tokens, temp, top_p, system_prompt)
            elif provider == "gemini":
                if mode == "generate":
                    return _gemini_generate(prompt, max_tokens, temp, top_p, system_prompt)
                else:
                    return _gemini_stream(prompt, max_tokens, temp, top_p, system_prompt)
            else:
                if mode == "generate":
                    return _qwen_generate(prompt, max_tokens, temp, top_p, system_prompt)
                else:
                    return _qwen_stream(prompt, max_tokens, temp, top_p, system_prompt)
        except Exception as e:
            raise RuntimeError(f"{provider} failed: {type(e).__name__}: {e}")

    # ── FastAPI app ────────────────────────────────────────────
    api = FastAPI(title="AI Server", version="4.0")
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"], allow_methods=["*"])
    if API_KEY:
        api.add_middleware(AuthMiddleware)

    @api.get("/")
    def home():
        return {
            "service": "AI Server",
            "version": "4.0",
            "endpoints": ["/ping", "/status", "/models", "/generate", "/generate_stream", "/docs"],
            "auth": bool(API_KEY),
            "models": {
                k: {"label": PROVIDER_LABELS[k], "configured": bool(
                    (k == "groq" and GROQ_API_KEY) or
                    (k == "gemini" and GEMINI_API_KEY) or
                    (k == "qwen")
                )}
                for k in PROVIDER_PRIORITY
            },
            "priority": PROVIDER_PRIORITY,
        }

    @api.get("/ping")
    def ping():
        return {"status": "alive", "model": MODEL_ID, "free_credit_usd": FREE_CREDIT}

    @api.get("/models")
    def get_models():
        models_info = {}
        for prov in PROVIDER_PRIORITY:
            remaining = _provider_remaining(prov)
            limit = PROVIDER_LIMITS[prov]["tokens"]
            ratio = (remaining / limit * 100) if limit > 0 else 0
            models_info[prov] = {
                "label": PROVIDER_LABELS[prov],
                "configured": bool(
                    (prov == "groq" and GROQ_API_KEY) or
                    (prov == "gemini" and GEMINI_API_KEY) or
                    (prov == "qwen")
                ),
                "tokens_used": _get_provider_usage(prov),
                "tokens_limit": limit,
                "tokens_remaining": remaining,
                "usage_pct": round(100 - ratio, 1),
                "available": ratio > (SWITCH_THRESHOLD * 100),
            }
        return {"models": models_info}

    @api.get("/status")
    def status():
        t0 = time.time()
        provider, _ = _select_model("auto")
        try:
            text, _, _, _ = _route(provider, "Say just 'ok'", 10, 0.7, 0.9, "", "generate")
            return {"status": "pass", "ai_response": text, "ai_error": None,
                    "response_time_sec": round(time.time() - t0, 2),
                    "model_used": provider, "model": MODEL_ID}
        except Exception as e:
            return {"status": "fail", "ai_response": None,
                    "ai_error": f"{type(e).__name__}: {e}",
                    "response_time_sec": round(time.time() - t0, 2), "model": MODEL_ID}

    @api.post("/generate")
    def generate_endpoint(req: Ask) -> Reply:
        t0 = time.time()
        try:
            provider, fallback = _select_model(req.model)
            text, inp_tok, out_tok, truncated = _route(
                provider, req.prompt, req.max_tokens,
                req.temperature, req.top_p, req.system_prompt, "generate"
            )
            return Reply(success=True, response=text,
                         model=provider,
                         model_used=PROVIDER_LABELS.get(provider, provider),
                         truncated=truncated,
                         fallback_chain=fallback,
                         time_sec=round(time.time() - t0, 2),
                         usage={
                             "input_tokens": inp_tok,
                             "output_tokens": out_tok,
                             "total_tokens": inp_tok + out_tok,
                             "provider": provider,
                             "provider_label": PROVIDER_LABELS.get(provider, provider),
                             "tokens_remaining": _provider_remaining(provider),
                         })
        except Exception as e:
            return Reply(success=False, error=f"{type(e).__name__}: {e}")

    @api.post("/generate_stream")
    async def generate_stream(req: Ask, request: Request):
        async def event_stream():
            try:
                provider, fallback = _select_model(req.model)

                # Start event
                start = json.dumps({
                    "type": "start",
                    "provider": provider,
                    "provider_label": PROVIDER_LABELS.get(provider, provider),
                    "fallback_chain": fallback,
                })
                yield f"data: {start}\n\n"

                full_text = ""
                out_tok = 0
                inp_tok = 0
                truncated = False
                t0 = time.time()

                gen = _route(provider, req.prompt, req.max_tokens,
                             req.temperature, req.top_p, req.system_prompt, "stream")

                for token_text, meta in gen:
                    if await request.is_disconnected():
                        break
                    if token_text == "__DONE__":
                        if meta:
                            inp_tok = meta.get("input_tokens", inp_tok)
                            out_tok = meta.get("output_tokens", out_tok)
                            truncated = meta.get("truncated", False)
                        break
                    full_text += token_text
                    out_tok += 1
                    yield f"data: {json.dumps({'type': 'token', 'text': token_text})}\n\n"

                elapsed = round(time.time() - t0, 2)
                remaining = _provider_remaining(provider)

                done = json.dumps({
                    "type": "done",
                    "full_text": full_text,
                    "provider": provider,
                    "provider_label": PROVIDER_LABELS.get(provider, provider),
                    "fallback_chain": fallback,
                    "truncated": truncated,
                    "input_tokens": inp_tok,
                    "output_tokens": out_tok,
                    "total_tokens": inp_tok + out_tok,
                    "time_sec": elapsed,
                    "tokens_remaining": remaining,
                    "model": MODEL_ID,
                })
                yield f"data: {done}\n\n"

            except Exception as e:
                err = json.dumps({"type": "error", "error": f"{type(e).__name__}: {str(e)}"})
                yield f"data: {err}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return api
