"""大模型接入层 —— Provider 抽象、OpenAI 兼容实现与工厂。"""

from app.llm.base import ChatMessage, LLMProvider
from app.llm.factory import create_llm_provider
from app.llm.openai_compat import OpenAICompatProvider

__all__ = ["ChatMessage", "LLMProvider", "OpenAICompatProvider", "create_llm_provider"]
