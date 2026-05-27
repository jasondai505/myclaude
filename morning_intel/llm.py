"""共享 LLM 调用 helper — Anthropic SDK 直调 DeepSeek，禁用 thinking。"""
import os
from anthropic import Anthropic

API_KEY = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
BASE_URL = "https://api.deepseek.com/anthropic"


def call(model: str, prompt: str, max_tokens: int = 4000, timeout: int = 120) -> str:
    try:
        client = Anthropic(api_key=API_KEY, base_url=BASE_URL)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "disabled"},
            timeout=timeout,
        )
        parts = []
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                parts.append(block.text)
        return "\n".join(parts)
    except Exception as e:
        return f"[ERROR] LLM 调用失败: {e}"
