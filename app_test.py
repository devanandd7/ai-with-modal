"""
AI Server - With Streaming + Real Cost Tracking + Truncation Detection
Model: Qwen/Qwen2.5-7B-Instruct (4-bit quantized)
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
MODEL_ID = "Qwen/Qwen2.5-7B-Instruct"
COST_PER_1K = 0.00005        # $0.05 per 1K tokens
FREE_CREDIT = 30.0           # $30 free credit
MAX_TOKENS_DEFAULT = 1536    # enough for multi-step reasoning (was 512 — caused truncation)
MAX_TOKENS_LIMIT = 4096      # absolute ceiling
API_KEY = os.environ.get("API_KEY", "")  # if empty, auth is disabled

app = modal.App("ai-server")
vol = modal.Volume.from_name("model-cache", create_if_missing=True)
# Real persistent ledger for cumulative cost tracking (fixes fake cost issue)
cost_ledger = modal.Dict.from_name("cost-ledger", create_if_missing=True)

img = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "transformers==4.49.0",      # pin for reproducibility (⚠️ older versions changed
        "torch==2.6.0",              #   apply_chat_template return_dict default)
        "accelerate==1.4.0",
        "bitsandbytes==0.45.4",
        "fastapi==0.115.12",
    )
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

# ── Schemas ────────────────────────────────────────────────────

class Ask(BaseModel):
    prompt: str = Field(..., description="Your question")
    max_tokens: int = Field(MAX_TOKENS_DEFAULT, ge=1, le=MAX_TOKENS_LIMIT)
    temperature: float = Field(0.7, ge=0.0, le=1.5)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    system_prompt: str = Field("", description="System prompt baked into chat template")


class Reply(BaseModel):
    success: bool
    response: str = ""
    error: str = ""
    time_sec: float = 0.0
    model: str = MODEL_ID
    truncated: bool = False       # new: tells frontend if answer was cut mid-way
    usage: dict = {}


# ── Web API ────────────────────────────────────────────────────

@app.function(
    image=img, gpu="T4",
    volumes={"/cache": vol},
    scaledown_window=300,
    # ⚠️ Keep-warm tradeoff: set container_idle_timeout=600 to reduce cold-starts,
    #    but that costs ~$0.35/hr idle on T4. 300s = 5min is the default.
)
@modal.concurrent(max_inputs=2)   # T4 16GB VRAM can't safely do 4 parallel 7B gens
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
                    return JSONResponse(
                        {"error": "Unauthorized — provide X-API-Key header"},
                        status_code=401,
                    )
                return await call_next(request)

    # ── Load model ─────────────────────────────────────────────
    print("Loading model...")
    tok = AutoTokenizer.from_pretrained(MODEL_ID, cache_dir="/cache")
    quant = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True, bnb_4bit_quant_type="nf4",
    )
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID, cache_dir="/cache", device_map="auto",
        quantization_config=quant,
    )
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    print(f"Ready on {torch.cuda.get_device_name(0)}")

    # ── Helpers ────────────────────────────────────────────────

    def _build_messages(prompt, system_prompt=""):
        """Build chat messages list, always returning (msgs, inp_len)."""
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        return msgs

    def _apply_template(msgs):
        """Apply chat template and return (input_ids, inp_len)."""
        tokens = tok.apply_chat_template(msgs, return_tensors="pt", add_generation_prompt=True)
        inp_ids = tokens.input_ids.to(model.device)
        return inp_ids, inp_ids.shape[1]

    def est_cost(inp, out):
        return (inp + out) * COST_PER_1K / 1000

    def _check_budget(cost):
        """Check cumulative budget against FREE_CREDIT. Returns (allowed, total_spent, remaining_approx).
        Uses Modal Dict as persistent ledger."""
        total_spent = cost_ledger.get("total_spent", 0.0)
        remaining = round(FREE_CREDIT - total_spent, 6)
        if remaining <= 0:
            return False, round(total_spent, 6), 0.0

        # Simulate after this request
        remaining_after = remaining - cost
        if remaining_after <= 0:
            return False, round(total_spent, 6), 0.0

        return True, round(total_spent, 6), round(remaining, 6)

    def _record_cost(cost):
        """Atomically add cost to the persistent ledger."""
        for _ in range(5):  # retry loop for concurrent safety
            current = cost_ledger.get("total_spent", 0.0)
            new_total = current + cost
            if cost_ledger.put("total_spent", new_total, if_value=current):
                return round(new_total, 6)
        # Fallback: force-write
        cost_ledger.put("total_spent", cost_ledger.get("total_spent", 0.0) + cost)
        return round(cost_ledger.get("total_spent", 0.0), 6)

    def generate(prompt, max_tokens, temp=0.7, top_p=0.9, system_prompt=""):
        msgs = _build_messages(prompt, system_prompt)
        inp_ids, inp_len = _apply_template(msgs)
        with torch.inference_mode():
            out = model.generate(
                inp_ids, max_new_tokens=max_tokens,
                temperature=temp, top_p=top_p, do_sample=(temp > 0),
                pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id,
            )
        out_len = out.shape[1] - inp_len
        truncated = out_len >= max_tokens  # hit the limit without EOS
        text = tok.decode(out[0][inp_len:], skip_special_tokens=True).strip()
        return text, inp_len, out_len, truncated

    def stream_gen(prompt, max_tokens, temp=0.7, top_p=0.9, system_prompt=""):
        msgs = _build_messages(prompt, system_prompt)
        inp_ids, inp_len = _apply_template(msgs)
        streamer = TextIteratorStreamer(tok, skip_prompt=True, skip_special_tokens=True)
        kw = dict(input_ids=inp_ids, max_new_tokens=max_tokens,
                  temperature=temp, top_p=top_p, do_sample=(temp > 0),
                  pad_token_id=tok.pad_token_id, eos_token_id=tok.eos_token_id,
                  streamer=streamer)
        Thread(target=model.generate, kwargs=kw).start()
        output = ""
        for t in streamer:
            output += t
            yield t, inp_len

    # ── FastAPI app ────────────────────────────────────────────
    api = FastAPI(title="AI Server", version="3.0")
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"], allow_methods=["*"])
    if API_KEY:
        api.add_middleware(AuthMiddleware)

    @api.get("/")
    def home():
        return {"service": "AI Server", "model": MODEL_ID,
                "endpoints": ["/ping", "/status", "/generate", "/generate_stream", "/docs"],
                "auth": bool(API_KEY),
                "max_tokens_default": MAX_TOKENS_DEFAULT}

    @api.get("/ping")
    def ping():
        total_spent = round(cost_ledger.get("total_spent", 0.0), 6)
        remaining = round(max(0, FREE_CREDIT - total_spent), 6)
        return {"status": "alive", "model": MODEL_ID,
                "free_credit_usd": FREE_CREDIT,
                "total_spent_usd": total_spent,
                "remaining_credit_usd": remaining}

    @api.get("/status")
    def status():
        t0 = time.time()
        try:
            text = generate("Say just 'ok'", 10)[0]
            return {"status": "pass", "ai_response": text, "ai_error": None,
                    "response_time_sec": round(time.time() - t0, 2), "model": MODEL_ID}
        except Exception as e:
            return {"status": "fail", "ai_response": None,
                    "ai_error": f"{type(e).__name__}: {e}",
                    "response_time_sec": round(time.time() - t0, 2), "model": MODEL_ID}

    @api.post("/generate")
    def generate_endpoint(req: Ask) -> Reply:
        t0 = time.time()
        try:
            # Estimate cost upfront (input only; output unknown yet)
            # We check budget before running
            msgs = _build_messages(req.prompt, req.system_prompt)
            inp_ids, inp_tok = _apply_template(msgs)

            text, _, out_tok, truncated = generate(
                req.prompt, req.max_tokens, req.temperature, req.top_p, req.system_prompt
            )

            cost = est_cost(inp_tok, out_tok)

            # Real budget check + record
            allowed, spent, remaining = _check_budget(cost)
            if not allowed:
                return Reply(success=False, error="Credit exhausted",
                             usage={"total_spent_usd": spent, "remaining_credit_usd": 0.0})
            new_total = _record_cost(cost)

            return Reply(success=True, response=text,
                         truncated=truncated,
                         time_sec=round(time.time() - t0, 2),
                         usage={
                             "input_tokens": inp_tok,
                             "output_tokens": out_tok,
                             "total_tokens": inp_tok + out_tok,
                             "estimated_cost_usd": round(cost, 6),
                             "total_spent_usd": new_total,
                             "remaining_credit_usd": round(max(0, FREE_CREDIT - new_total), 6),
                         })
        except Exception as e:
            return Reply(success=False, error=f"{type(e).__name__}: {e}")

    @api.post("/generate_stream")
    async def generate_stream(req: Ask, request: Request):
        async def event_stream():
            try:
                # Consistent token counting via chat template (same as /generate)
                msgs = _build_messages(req.prompt, req.system_prompt)
                inp_ids, inp_tok = _apply_template(msgs)

                # start event
                start = json.dumps({"type": "start", "input_tokens": inp_tok, "max_output": req.max_tokens})
                yield f"data: {start}\n\n"

                full_text = ""
                out_count = 0
                t0 = time.time()

                for token_text, _ in stream_gen(req.prompt, req.max_tokens, req.temperature, req.top_p, req.system_prompt):
                    if await request.is_disconnected():
                        break
                    full_text += token_text
                    out_count += 1
                    tk = json.dumps({"type": "token", "text": token_text})
                    yield f"data: {tk}\n\n"

                elapsed = round(time.time() - t0, 2)
                truncated = out_count >= req.max_tokens  # hit limit before EOS

                cost = est_cost(inp_tok, out_count)
                allowed, spent, remaining = _check_budget(cost)
                new_total = _record_cost(cost) if allowed else spent

                done = json.dumps({
                    "type": "done",
                    "full_text": full_text,
                    "truncated": truncated,              # ← new flag
                    "input_tokens": inp_tok,
                    "output_tokens": out_count,
                    "total_tokens": inp_tok + out_count,
                    "time_sec": elapsed,
                    "estimated_cost_usd": round(cost, 6),
                    "total_spent_usd": new_total,
                    "remaining_credit_usd": round(max(0, FREE_CREDIT - new_total), 6),
                    "model": MODEL_ID,
                })
                yield f"data: {done}\n\n"

            except Exception as e:
                err = json.dumps({"type": "error", "error": f"{type(e).__name__}: {str(e)}"})
                yield f"data: {err}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return api
