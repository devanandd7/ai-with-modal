"""
🚀 NO-AUTH TEST SERVER – Bina API Key ke turant test karo!
===========================================================
Sirf test ke liye hai. Production me API key wala use karo.

Deploy:   modal deploy app_test.py
Test:     curl -X POST <URL>/generate -H "Content-Type: application/json" \
               -d '{"prompt": "Namaste, aap kaun ho?"}'
"""

import modal
from pydantic import BaseModel, Field

# ── Config ────────────────────────────────────────────────────────
MODEL_ID = "google/gemma-2-9b-it"
GPU_TYPE = "T4"

app = modal.App("personal-ai-server-test")

model_cache = modal.Volume.from_name("hf-model-cache", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("transformers>=4.45.0", "torch>=2.4.0", "accelerate>=0.33.0", "bitsandbytes>=0.44.0", "fastapi>=0.115.0")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)


# ── API Models ────────────────────────────────────────────────────
class AskRequest(BaseModel):
    prompt: str = Field(..., description="Tumhara sawaal / prompt")
    max_tokens: int = Field(default=512, ge=1, le=2048)


class AskResponse(BaseModel):
    response: str
    model: str


# ── Model Class ───────────────────────────────────────────────────
@app.cls(image=image, gpu=GPU_TYPE, volumes={"/cache": model_cache}, allow_concurrent_inputs=4, container_idle_timeout=300)
class GemmaModel:
    @modal.enter()
    def load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        print(f"⏳ Loading {MODEL_ID} (4-bit quantized)...")
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, cache_dir="/cache")
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, cache_dir="/cache", device_map="auto",
            load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True, bnb_4bit_quant_type="nf4",
        )
        print(f"✅ Ready! GPU: {torch.cuda.get_device_name(0)}")

    @modal.method()
    def ask(self, req: AskRequest) -> AskResponse:
        import torch
        msgs = [{"role": "user", "content": req.prompt}]
        tokens = self.tokenizer.apply_chat_template(msgs, return_tensors="pt", add_generation_prompt=True).to(self.model.device)
        input_len = tokens.shape[1]

        with torch.inference_mode():
            out = self.model.generate(tokens, max_new_tokens=req.max_tokens, temperature=0.7, top_p=0.9, do_sample=True, pad_token_id=self.tokenizer.pad_token_id, eos_token_id=self.tokenizer.eos_token_id)

        text = self.tokenizer.decode(out[0][input_len:], skip_special_tokens=True).strip()
        return AskResponse(response=text, model=MODEL_ID)


# ── FastAPI Web Endpoint (NO AUTH) ────────────────────────────────
@app.function(image=image, allow_concurrent_inputs=4)
@modal.asgi_app()
def web():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    api = FastAPI(title="AI Server (TEST MODE – No Auth)", version="1.0")
    api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_headers=["*"], allow_methods=["*"])

    @api.get("/")
    def root():
        return {"message": "🟢 AI Server Running – Bina API Key ke test kar sakte ho!", "endpoints": ["/generate", "/docs"]}

    @api.post("/generate", response_model=AskResponse)
    def generate(req: AskRequest):
        return GemmaModel().ask.remote(req)

    return api


# ── Local Test ────────────────────────────────────────────────────
@app.local_entrypoint()
def main(prompt: str = "Ek chutkula sunao AI par"):
    result = GemmaModel().ask.remote(AskRequest(prompt=prompt))
    print(f"\n🧠 Sawaal: {prompt}")
    print(f"🤖 Jawaab:\n{result.response}")
