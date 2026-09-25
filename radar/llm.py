"""Unified LLM interface supporting Claude (Anthropic), Ollama (Local), and Heuristic Fallbacks."""

import os

import httpx


class LLMClient:
    """Provides access to Claude, Ollama, and heuristic scoring mechanisms."""

    def __init__(self):
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2")
        # Probed lazily on first call; cached so a down Ollama doesn't retry a
        # slow connection for every single opening in a run.
        self._ollama_available: bool | None = None

    def call_ollama(self, prompt: str, system: str | None = None) -> str | None:
        """Make call to local Ollama instance if available."""
        if self._ollama_available is False:
            return None
        try:
            url = f"{self.ollama_host}/api/generate"
            payload = {
                "model": self.ollama_model,
                "prompt": prompt,
                "system": system or "",
                "stream": False,
                "format": "json",
            }
            # Short connect timeout: a down Ollama should fail fast, not stall a run.
            timeout = httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=5.0)
            with httpx.Client(timeout=timeout) as client:
                res = client.post(url, json=payload)
                self._ollama_available = True
                if res.status_code == 200:
                    data = res.json()
                    return data.get("response")
        except Exception:
            self._ollama_available = False
            return None
        return None

    def call_claude(self, prompt: str, system: str | None = None) -> str | None:
        """Make call to Anthropic Claude API."""
        if not self.anthropic_key:
            return None

        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": self.anthropic_model,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                payload["system"] = system

            with httpx.Client(timeout=30.0) as client:
                res = client.post(url, json=payload, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    content = data.get("content", [])
                    if content and isinstance(content, list):
                        return content[0].get("text")
        except Exception:
            return None
        return None

    def complete(self, prompt: str, system: str | None = None, prefer_quality: bool = True) -> tuple[str | None, str]:
        """
        Attempts execution using quality LLM (Claude) if prefer_quality is True,
        falling back to Ollama. Returns (response_text, provider_name).
        """
        if prefer_quality and self.anthropic_key:
            res = self.call_claude(prompt, system)
            if res:
                return res, "claude"

        # Try Ollama
        res = self.call_ollama(prompt, system)
        if res:
            return res, "ollama"

        # Try Claude if preferred_quality was False but key is there
        if self.anthropic_key:
            res = self.call_claude(prompt, system)
            if res:
                return res, "claude"

        return None, "none"
