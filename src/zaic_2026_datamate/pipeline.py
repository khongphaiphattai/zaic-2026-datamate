"""Shared, explicit pipeline options for evaluation and submission generation."""
from dataclasses import dataclass
import math

from zaic_2026_datamate.hybrid_agent import HybridAgent
from zaic_2026_datamate.llm_client import LLMClient
from zaic_2026_datamate.paraphrase_fast import DailyOrderParaphraseAgent
from zaic_2026_datamate.monthly_growth import MonthlyGrowthAgent
from zaic_2026_datamate.mtd_growth import MTDGrowthAgent


@dataclass(frozen=True)
class PipelineConfig:
    enable_slow_path: bool = False
    order_paraphrases: bool = False
    model: str = 'datamate-business24'
    confidence_threshold: float = 0.95
    monthly_growth: bool = False
    mtd_growth: bool = False

    def __post_init__(self):
        if not math.isfinite(self.confidence_threshold) or not 0 <= self.confidence_threshold <= 1:
            raise ValueError('confidence_threshold must be finite and between 0 and 1')


def build_agent(config: PipelineConfig, llm_client: LLMClient | None = None) -> HybridAgent | MonthlyGrowthAgent | MTDGrowthAgent:
    if config.enable_slow_path and llm_client is None:
        raise ValueError('Slow Path requires an explicit model client')
    agent_class = DailyOrderParaphraseAgent if config.order_paraphrases else HybridAgent
    base = agent_class(enable_slow_path=config.enable_slow_path,
                       llm_client=llm_client, confidence_threshold=config.confidence_threshold)
    base = MonthlyGrowthAgent(base) if config.monthly_growth else base
    return MTDGrowthAgent(base) if config.mtd_growth else base
