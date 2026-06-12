"""共享 LLM 调用 helper — 统一走角色化配置，禁用 thinking。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "daily_review"))
from roles import get_client as _get_client, get_model


def call(role: str, prompt: str, max_tokens: int = 4000, timeout: int = 120) -> str:
    try:
        client = _get_client(role, timeout=timeout)
        model = get_model(role)
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
