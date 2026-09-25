"""Unified LLM interface supporting Claude (Anthropic), Ollama (Local), and Heuristic Fallbacks."""

import os
import json
import httpx
from typing import Dict, Any, Optional, Tuple


class LLMClient:
    """Provides access to Claude, Ollama, and heuristic scoring mechanisms."""

    def __init__(self):
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "llama3.2")

    def call_ollama(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        """Make call to local Ollama instance if available."""
        try:
            url = f"{self.ollama_host}/api/generate"
            payload = {
                "model": self.ollama_model,
                "prompt": prompt,
                "system": system or "",
                "stream": False,
                "format": "json"
            }
            with httpx.Client(timeout=30.0) as client:
                res = client.post(url, json=payload)
                if res.status_code == 200:
                    data = res.json()
                    return data.get("response")
        except Exception:
            return None
        return None

    def call_claude(self, prompt: str, system: Optional[str] = None) -> Optional[str]:
        """Make call to Anthropic Claude API."""
        if not self.anthropic_key:
            return None
        
        try:
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            payload = {
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
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

    def complete(self, prompt: str, system: Optional[str] = None, prefer_quality: bool = True) -> Tuple[Optional[str], str]:
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
