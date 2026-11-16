"""Unit tests for image build monitor pure helpers."""
from datetime import UTC, datetime

from image_build_monitor.service import (
    _as_utc,
    _normalize_emails,
    _previous_fire_time,
)


def test_normalize_emails_handles_list_and_string():
    assert _normalize_emails(["a@x.com", " b@x.com "]) == ["a@x.com", "b@x.com"]
    assert _normalize_emails("a@x.com; b@x.com\nc@x.com") == [
        "a@x.com",
        "b@x.com",
        "c@x.com",
    ]
    assert _normalize_emails(None) == []


def test_as_utc_attaches_utc_for_naive_datetime():
    naive = datetime(2026, 1, 1, 8, 0, 0)
    assert _as_utc(naive) == datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)
    assert _as_utc(None) is None


def test_previous_fire_time_returns_utc_time_before_now():
    now = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)
    prev = _previous_fire_time("0 2 * * *", "Asia/Shanghai", now)
    assert prev is not None
    assert prev.tzinfo is not None
    # 2026-11-16 02:00 +08:00 == 2026-11-15 18:00 UTC
    assert prev == datetime(2026, 11, 15, 18, 0, tzinfo=UTC)


def test_previous_fire_time_invalid_cron_returns_none():
    assert _previous_fire_time("not-a-cron", "Asia/Shanghai", datetime.now(UTC)) is None
    assert _previous_fire_time("", "Asia/Shanghai", datetime.now(UTC)) is None
