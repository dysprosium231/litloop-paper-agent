"""The model side of the desktop app's built-in agent (backend B).

The app can hand a task to an external agent (Claude Code, Codex, …) or talk to
a model API itself. This package is the second option: one call shape over both
protocols — Anthropic's own Messages API and the OpenAI-compatible one that
DeepSeek, 通义千问, Kimi, 智谱 GLM, OpenRouter, Ollama and vLLM speak.
"""

from .chat_agent import execute_action, stream_chat_turn
from .client import (
    PROVIDERS,
    PROVIDER_PRESETS,
    chat,
    chat_stream,
    estimate_cost,
    get_preset,
    model_config,
    normalize_usage,
    test_model,
)
from .runner import ToolError, run_builtin_resume, run_builtin_search, run_task
from .scorer import collect_user_feedback, score_papers
from .tools import FUNCTIONS, TOOLS

__all__ = [
    "FUNCTIONS",
    "PROVIDERS",
    "PROVIDER_PRESETS",
    "TOOLS",
    "ToolError",
    "chat",
    "chat_stream",
    "collect_user_feedback",
    "estimate_cost",
    "execute_action",
    "get_preset",
    "model_config",
    "normalize_usage",
    "run_builtin_resume",
    "run_builtin_search",
    "run_task",
    "score_papers",
    "stream_chat_turn",
    "test_model",
]
