"""Session-scoped DataLoader cache for @pytest.mark.slow tests."""

from __future__ import annotations

from pathlib import Path
import pytest

from zaic_2026_datamate.data_loader import BusinessContext, DataLoader

TRAIN_DATA_DIR = Path("data/train/train/data")
TRAIN_CALENDAR_PATH = Path("data/train/train/calendar.csv")

SESSION_TRAIN_CONTEXT_CACHE: dict[str, BusinessContext] = {}


@pytest.fixture(scope="session", autouse=True)
def session_cached_train_dataloader():
    """Patch DataLoader.load_business to reuse session-cached BusinessContext for train data."""
    orig_load_business = DataLoader.load_business
    resolved_train_dir = TRAIN_DATA_DIR.resolve() if TRAIN_DATA_DIR.exists() else None
    resolved_cal_path = (
        TRAIN_CALENDAR_PATH.resolve() if TRAIN_CALENDAR_PATH.exists() else None
    )

    def cached_load_business(self: DataLoader, business_id: str) -> BusinessContext:
        if (
            resolved_train_dir is not None
            and self.temp_dir is None
            and self.data_dir.resolve() == resolved_train_dir
            and (
                self.calendar_path is None
                or (
                    resolved_cal_path is not None
                    and self.calendar_path.resolve() == resolved_cal_path
                )
            )
        ):
            ctx = SESSION_TRAIN_CONTEXT_CACHE.get(business_id)
            if ctx is None:
                ctx = orig_load_business(self, business_id)
                SESSION_TRAIN_CONTEXT_CACHE[business_id] = ctx
            ctx.duckdb_conn.execute("SELECT 1").fetchall()
            ctx.reset_trace()
            return ctx
        return orig_load_business(self, business_id)

    DataLoader.load_business = cached_load_business  # type: ignore[method-assign]
    try:
        yield SESSION_TRAIN_CONTEXT_CACHE
    finally:
        DataLoader.load_business = orig_load_business  # type: ignore[method-assign]


@pytest.fixture(scope="session")
def train_loader() -> DataLoader:
    """Shared session-scoped DataLoader for train data."""
    if not TRAIN_DATA_DIR.exists():
        pytest.skip("Train data directory not available")
    return DataLoader(TRAIN_DATA_DIR, TRAIN_CALENDAR_PATH)
