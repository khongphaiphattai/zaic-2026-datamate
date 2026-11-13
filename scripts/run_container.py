"""Container entrypoint for ZAIC 2026 DataMate offline evaluation (--network none)."""

from __future__ import annotations

import argparse
import os
import sys

from zaic_2026_datamate.container_runner import run_container


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ZAIC 2026 DataMate offline container entrypoint (/data RO -> /result)"
    )
    parser.add_argument("--data-dir", default=os.getenv("ZAIC_DATA_DIR", "/data"))
    parser.add_argument("--result-dir", default=os.getenv("ZAIC_RESULT_DIR", "/result"))
    parser.add_argument("--temp-dir", default=os.getenv("ZAIC_TEMP_DIR", "/tmp"))
    parser.add_argument("--enable-slow-path", action="store_true")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default="datamate-business24")
    parser.add_argument("--confidence-threshold", type=float, default=0.95)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-overwrite", action="store_true")
    args = parser.parse_args(argv)

    stats = run_container(
        data_dir=args.data_dir,
        result_dir=args.result_dir,
        temp_dir=args.temp_dir,
        overwrite=not args.no_overwrite,
        enable_slow_path=args.enable_slow_path,
        base_url=args.base_url,
        model=args.model,
        confidence_threshold=args.confidence_threshold,
        limit=args.limit,
    )
    print(
        f"[ZAIC Container] Done: {stats['total']} Qs | "
        f"Answered={stats['answered']} | Abstain={stats['abstain']} | "
        f"Elapsed={stats['total_elapsed_seconds']:.2f}s | "
        f"SHA256={stats['submission_sha256']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
