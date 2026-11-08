"""Validate served checkpoint identity against declared training provenance."""

import hashlib
import json
import urllib.request
from pathlib import Path


def verify_training_artifacts(
    metadata,
    model,
    *,
    split_path="artifacts/selective-evaluation/split.json",
    data_path="artifacts/train-only-sft.jsonl",
):
    """Compare declared provenance to the local business-isolated training files."""
    split_bytes = Path(split_path).read_bytes()
    verify_model_provenance(
        metadata,
        hashlib.sha256(Path(data_path).read_bytes()).hexdigest(),
        hashlib.sha256(split_bytes).hexdigest(),
        json.loads(split_bytes)["train_businesses"],
        model,
    )


def fetch_model_provenance(base_url, model):
    with urllib.request.urlopen(
        base_url.rstrip("/") + "/model-provenance", timeout=20
    ) as response:
        served = json.load(response)
    verify_training_artifacts(served, model)
    return served


def verify_model_provenance(metadata, data_sha256, split_sha256, businesses, model):
    expected = {
        "model": model,
        "training_complete": True,
        "merge_complete": True,
        "data_sha256": data_sha256,
        "split_sha256": split_sha256,
        "base_model": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "base_revision": "c03e6d358207e414f1eca0bb1891e29f1db0e242",
    }
    if not isinstance(metadata, dict) or any(
        metadata.get(k) != v for k, v in expected.items()
    ):
        raise ValueError(
            "Served model provenance does not match the requested trained checkpoint"
        )
    if sorted(metadata.get("businesses", [])) != sorted(businesses):
        raise ValueError(
            "Served model training businesses differ from the training partition"
        )
