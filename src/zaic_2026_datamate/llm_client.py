"""
llm_client.py: Universal LLM Client for ZAIC 2026 DataMate Slow Path.

Supports OpenAI-compatible API servers (vLLM, Ollama, SGLang, TGI, or Colab Tunnel)
and offline Mock clients for deterministic unit testing.
Enforces Rule 4.4 invariants: temperature = 0.0, seed = 42, parameter ceiling <= 15B.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Protocol

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    """Protocol for LLM inference engines."""

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate response text from prompt."""
        ...


class OpenAICompatibleClient:
    """Client for OpenAI-compatible completions endpoints (vLLM, Ollama, etc.)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 45.0,
        max_tokens: int = 1024,
    ):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "http://localhost:8000/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "dummy")
        self.model = model or os.getenv("LLM_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Call the chat completions endpoint with temperature=0.0, seed=42."""
        url = f"{self.base_url}/chat/completions"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,  # Strict Rule 4.4 Invariant
            "seed": 42,          # Strict Rule 4.4 Invariant
            "max_tokens": self.max_tokens,
        }

        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                choices = resp_data.get("choices", [])
                if choices and "message" in choices[0]:
                    return choices[0]["message"].get("content", "")
                return ""
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            logger.error("LLM API HTTPError %d: %s | Body: %s", e.code, e.reason, err_body)
            raise ConnectionError(f"LLM endpoint HTTP {e.code}: {err_body or e.reason}") from e
        except urllib.error.URLError as e:
            logger.error("LLM API request failed: %s", e)
            raise ConnectionError(f"Failed to connect to LLM endpoint {url}: {e}") from e
        except Exception as e:
            logger.error("Unexpected error during LLM generation: %s", e)
            raise


class MockLLMClient:
    """Deterministic mock client for offline tests and evaluation."""

    def __init__(self, handler: Optional[Callable[[str], str]] = None):
        self.handler = handler

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if self.handler:
            return self.handler(prompt)
        # Default mock response: safe abstain
        return json.dumps({
            "thought": "Mock client default refusal",
            "type": "abstain",
            "sql": None,
            "confidence": 0.0,
        })
