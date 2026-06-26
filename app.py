"""
Modal AI Server – Personal Gemma Model Serving (4 Users)
=========================================================
• Model: google/gemma-2-9b-it (9B params, 4-bit quantized → ~5.5 GB VRAM)
• GPU:   T4 (16 GB) – plenty of headroom for 4 concurrent requests
• Auth:  4 API keys, one per user
• Concurrency: capped at 4 simultaneous inputs
• Cost:  ~$0.50–$2/month on Modal's $30 free credit

Deploy once:
    modal deploy app.py

Your friends call it like any REST API:
    curl -X POST https://YOUR-WORKSPACE--personal-ai-server-generate.modal.run \
      -H "Content-Type: application/json" \
      -H "X-API-Key: sk-user-1-xxxx" \
      -d '{"prompt": "Explain recursion in simple terms"}'
"""

import os
import modal
from pydantic import BaseModel, Field
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration – tweak these to your liking
# ---------------------------------------------------------------------------

MODEL_ID = "google/gemma-2-9b-it"       # Gemma 2 9B instruct – solid coding + reasoning
GPU_TYPE = "T4"                          # cheapest GPU that fits the 4-bit model
MAX_CONCURRENT = 4                       # one per user
ALLOWED_API_KEYS = {                     # one key per user – change these!
    "sk-user-1-change-me-abc123",
    "sk-user-2-change-me-def456",
    "sk-user-3-change-me-ghi789",
    "sk-user-4-change-me-jkl012",
}

# ---------------------------------------------------------------------------
# Modal plumbing
# ---------------------------------------------------------------------------

app = modal.App("personal-ai-server")

# Cached volume so the 9 GB model isn't re-downloaded on every cold start
model_cache = modal.Volume.from_name(
    "hf-model-cache", create_if_missing=True
)

# Build the container image once
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "transformers>=4.45.0",
        "torch>=2.4.0",
        "accelerate>=0.33.0",
        "bitsandbytes>=0.44.0",
        "fastapi>=0.115.0",
    )
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})  # faster downloads via HF transfer
)


# ---------------------------------------------------------------------------
# Pydantic models for the API
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="Your prompt / question")
    max_tokens: int = Field(default=512, ge=1, le=2048, description="Max new tokens to generate")
    temperature: float = Field(default=0.7, ge=0.0, le=1.5)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)


class GenerateResponse(BaseModel):
    generated_text: str
    model: str
    usage: dict


class HealthResponse(BaseModel):
    status: str
    model: str
    gpu: str


# ---------------------------------------------------------------------------
# Stateful class – model loaded once, stays warm between requests
# ---------------------------------------------------------------------------

@app.cls(
    image=image,
    gpu=GPU_TYPE,
    volumes={"/cache": model_cache},
    allow_concurrent_inputs=MAX_CONCURRENT,
    container_idle_timeout=300,   # shut down after 5 min idle → save $$$
    scaledown_window=60,
)
class GemmaModel:
    """Holds the quantized Gemma model in GPU memory across requests."""

    @modal.enter()
    def load_model(self):
        """Download & load model on container start (cold-start, ~60-90 s)."""
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        print(f"🚀 Loading {MODEL_ID} with 4-bit quantization …")

        self.tokenizer = AutoTokenizer.from_pretrained(
            MODEL_ID,
            cache_dir="/cache",
        )

        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            cache_dir="/cache",
            device_map="auto",
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,          # squeeze even more
            bnb_4bit_quant_type="nf4",               # NormalFloat4 – best for LLMs
        )

        print(f"✅ Model ready on {torch.cuda.get_device_name(0)}")
        print(f"   VRAM used: {torch.cuda.memory_allocated(0) / 1e9:.2f} GB")

    @modal.method()
    def generate(self, req: GenerateRequest) -> GenerateResponse:
        """Run inference.  Called once per request."""
        import torch

        messages = [{"role": "user", "content": req.prompt}]

        # Gemma 2 uses the standard chat template
        tokenized = self.tokenizer.apply_chat_template(
            messages,
            return_tensors="pt",
            add_generation_prompt=True,
        ).to(self.model.device)

        input_len = tokenized.shape[1]

        with torch.inference_mode():
            outputs = self.model.generate(
                tokenized,
                max_new_tokens=req.max_tokens,
                temperature=req.temperature,
                top_p=req.top_p,
                do_sample=True if req.temperature > 0 else False,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        # Decode only the newly-generated part
        generated_ids = outputs[0][input_len:]
        generated_text = self.tokenizer.decode(
            generated_ids, skip_special_tokens=True
        ).strip()

        return GenerateResponse(
            generated_text=generated_text,
            model=MODEL_ID,
            usage={
                "prompt_tokens": input_len,
                "completion_tokens": generated_ids.shape[0],
                "total_tokens": outputs.shape[1],
            },
        )


# ---------------------------------------------------------------------------
# FastAPI web endpoint  (called by your friends via HTTP)
# ---------------------------------------------------------------------------

@app.function(image=image, allow_concurrent_inputs=MAX_CONCURRENT)
@modal.asgi_app()
def web():
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware

    api = FastAPI(title="Personal AI Server", version="1.0.0")

    # Allow browsers / local apps to call the API
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_headers=["*"],
        allow_methods=["*"],
    )

    # ---- Auth middleware --------------------------------------------------
    @api.middleware("http")
    async def api_key_auth(request: Request, call_next):
        if request.url.path in ("/", "/health"):
            return await call_next(request)

        key = request.headers.get("X-API-Key", "")
        if key not in ALLOWED_API_KEYS:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")
        return await call_next(request)

    # ---- Routes ----------------------------------------------------------
    @api.get("/")
    async def root():
        return {"message": "Personal AI Server – Gemma 2 9B", "docs": "/docs"}

    @api.get("/health")
    async def health():
        return HealthResponse(status="ok", model=MODEL_ID, gpu=GPU_TYPE)

    @api.post("/generate", response_model=GenerateResponse)
    async def generate(req: GenerateRequest):
        model = GemmaModel()            # reuses the warm cls instance
        return model.generate.remote(req)

    return api


# ---------------------------------------------------------------------------
# Local smoke-test  (run with:  modal run app.py)
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(prompt: str = "Write a haiku about programming"):
    model = GemmaModel()
    result = model.generate.remote(
        GenerateRequest(prompt=prompt, max_tokens=128)
    )
    print(f"\n📝 Prompt: {prompt}")
    print(f"🤖 Response:\n{result.generated_text}")
    print(f"📊 Usage: {result.usage}")
