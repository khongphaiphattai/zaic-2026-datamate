"""
generate_public_submission.py: Run the DataMate pipeline on data/public_test/public_test/questions.jsonl
and generate a strictly compliant submission.jsonl for evaluation.

Supports:
- Pure Fast Path (deterministic rule-based)
- Hybrid Fast-Slow Path (Fast Path -> LoRA SLM -> Safe Abstain)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from zaic_2026_datamate.container_runner import run_pipeline_to_result
from zaic_2026_datamate.data_loader import DataLoader
from zaic_2026_datamate.llm_client import OpenAICompatibleClient
from zaic_2026_datamate.pipeline import PipelineConfig
from zaic_2026_datamate.provenance import fetch_model_provenance

logging.basicConfig(level=logging.WARNING)


def generate_submission(
    questions_path: str | Path = "data/public_test/public_test/questions.jsonl",
    data_dir: str | Path = "data/public_test/public_test/data",
    calendar_path: str | Path = "data/public_test/public_test/calendar.csv",
    output_path: str | Path = "submission.jsonl",
    enable_slow_path: bool = False,
    base_url: Optional[str] = None,
    model: str = "datamate-business24",
    confidence_threshold: float = 0.95,
    limit: Optional[int] = None,
    overwrite: bool = False,
    order_paraphrases: bool = False,
    monthly_growth: bool = False,
    mtd_growth: bool = False,
    log_dir: str | Path | None = None,
) -> Dict[str, Any]:
    config = PipelineConfig(
        enable_slow_path=enable_slow_path,
        order_paraphrases=order_paraphrases,
        model=model,
        confidence_threshold=confidence_threshold,
        monthly_growth=monthly_growth,
        mtd_growth=mtd_growth,
    )

    print(f"🚀 BẮT ĐẦU CHẠY PIPELINE TRÊN TẬP PUBLIC TEST: {questions_path}")
    print("=" * 75)

    def _on_progress(done: int, total_q: int, stats: dict[str, int], elapsed: float) -> None:
        print(
            f"[{done:04d}/{total_q}] Đã xử lý {done} câu | "
            f"Fast: {stats['fast_path']} | Slow: {stats['slow_path']} | "
            f"Abstain: {stats['abstain']} | Thời gian: {elapsed:.1f}s",
            flush=True,
        )

    stats = run_pipeline_to_result(
        questions_path=questions_path,
        data_dir=data_dir,
        calendar_path=calendar_path,
        output_path=output_path,
        config=config,
        log_dir=log_dir,
        base_url=base_url,
        limit=limit,
        overwrite=overwrite,
        progress_callback=_on_progress,
        loader_cls=DataLoader,
        provenance_fn=fetch_model_provenance,
        client_cls=OpenAICompatibleClient,
    )

    total_time = stats["total_elapsed_seconds"]
    total = max(1, stats["total"])
    print("\n" + "=" * 75)
    print("✅ ĐÃ XUẤT FILE NỘP BÀI THÀNH CÔNG:")
    print(f"📁 Đường dẫn: {Path(output_path).resolve()}")
    print(f"🔑 SHA256:    {stats['submission_sha256']}")
    print(f"Tổng số câu:       {stats['total']}")
    print(f"🎯 Số câu trả lời:  {stats['answered']} ({stats['answered'] / total * 100:.1f}%)")
    print(f"   ⚡ Fast Path:    {stats['fast_path']}")
    print(f"   🧠 Slow Path:    {stats['slow_path']}")
    print(f"   Count fallback: {stats['paraphrase_fast_path']}")
    print(f"   Monthly growth: {stats['monthly_growth_path']}")
    print(f"   - number:        {stats['number']}")
    print(f"   - percent:       {stats['percent']}")
    print(f"   - string:        {stats['string']}")
    print(f"   - date:          {stats['date']}")
    print(f"   - list:          {stats['list']}")
    print(f"   - unanswerable:  {stats['unanswerable']}")
    print(f"⚪ Số câu từ chối:  {stats['abstain']} ({stats['abstain'] / total * 100:.1f}%) [0 điểm - không phạt]")
    print(f"⏱️ Tổng thời gian:   {total_time:.2f}s (Trung bình: {total_time / total * 1000:.1f}ms/câu)")
    print("=" * 75)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", default="data/public_test/public_test/questions.jsonl")
    parser.add_argument("--data-dir", default="data/public_test/public_test/data")
    parser.add_argument("--calendar", default="data/public_test/public_test/calendar.csv")
    parser.add_argument("--output", default="submission.jsonl")
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--enable-slow-path", action="store_true")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default="datamate-business24")
    parser.add_argument("--order-paraphrases", "--daily-order-paraphrases", action="store_true")
    parser.add_argument("--monthly-growth", action="store_true")
    parser.add_argument("--mtd-growth", action="store_true")
    parser.add_argument(
        "--no-production",
        action="store_true",
        help="Disable default production flags (use explicit feature flags only)",
    )
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    use_prod = not args.no_production
    generate_submission(
        questions_path=args.questions,
        data_dir=args.data_dir,
        calendar_path=args.calendar,
        output_path=args.output,
        enable_slow_path=args.enable_slow_path,
        base_url=args.base_url,
        model=args.model,
        confidence_threshold=args.confidence_threshold,
        limit=args.limit,
        overwrite=args.overwrite,
        order_paraphrases=True if use_prod else args.order_paraphrases,
        monthly_growth=True if use_prod else args.monthly_growth,
        mtd_growth=True if use_prod else args.mtd_growth,
        log_dir=args.log_dir,
    )
