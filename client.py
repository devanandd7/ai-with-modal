"""
Minimal Python client – give this to your 4 friends.

Usage:
    python client.py "Explain quantum computing in simple terms"
"""

import sys
import requests

# ---- CONFIGURE THESE ----
SERVER_URL = "https://YOUR-WORKSPACE--personal-ai-server-web.modal.run"
API_KEY    = "sk-user-1-change-me-abc123"   # each friend gets their own key
# -------------------------


def ask(prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> dict:
    resp = requests.post(
        f"{SERVER_URL}/generate",
        json={
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        headers={
            "X-API-Key": API_KEY,
            "Content-Type": "application/json",
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Write a Python function to reverse a linked list"
    result = ask(prompt)
    print(f"\n🤖 Response:\n{result['generated_text']}")
    print(f"\n📊 Tokens: {result['usage']}")
