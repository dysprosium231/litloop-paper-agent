"""The model side of the desktop app's built-in agent (backend B).

The app can hand a task to an external agent (Claude Code, Codex, …) or talk to
a model API itself. This package is the second option: one call shape over both
protocols — Anthropic's own Messages API and the OpenAI-compatible one that
DeepSeek, 通义千问, Kimi, 智谱 GLM, OpenRouter, Ollama and vLLM speak.
"""

from .client import PROVIDERS, chat, model_config, test_model

__all__ = ["PROVIDERS", "chat", "model_config", "test_model"]
