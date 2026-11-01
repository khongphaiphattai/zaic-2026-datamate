"""
zaic_2026_datamate: Zalo AI Challenge 2026 DataMate Agent package.
"""

from zaic_2026_datamate.agent import DataMateAgent
from zaic_2026_datamate.data_loader import BusinessContext, DataLoader
from zaic_2026_datamate.engine import DuckDBAgentEngine
from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.intent_parser import resolve_query
from zaic_2026_datamate.llm_client import LLMClient, MockLLMClient, OpenAICompatibleClient
from zaic_2026_datamate.slow_path import SlowPathEngine

__all__ = [
    "DataMateAgent",
    "DataLoader",
    "BusinessContext",
    "DuckDBAgentEngine",
    "HybridAgent",
    "resolve_query",
    "LLMClient",
    "MockLLMClient",
    "OpenAICompatibleClient",
    "SlowPathEngine",
]


def main() -> None:
    print("🚀 ZAIC 2026 DataMate Agent ready.")
