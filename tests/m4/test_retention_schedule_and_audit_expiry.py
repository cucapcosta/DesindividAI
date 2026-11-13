"""
Regra f (prazo do audit_trail, AUDIT_RETENTION_DAYS) e agendador do job de retenção.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.privacy.retention import run_retention
from src.privacy.retention_store import InMemoryRetentionStore, _matches
from src.privacy.retention_scheduler import (
    ScheduleConfig,
    retention_lifespan,
    retention_loop,
    run_once,
)

_NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


class _NoVault:
    def shred(self, pseudonym_id):  # pragma: no cover - sem cofre nestes testes
        return False


class _Audit:
    def __init__(self):
        self.events = []

    def __call__(self, event_type, **fields):
        self.events.append({"event_type": event_type, **fields})
        return "trace"


def _audit_doc(_id: str, days_ago: float) -> dict:
    ts = (_NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"_id": _id, "type": "audit_event", "event_type": "vault.reveal",
            "recorded_at": ts, "pseudonym_id": "a" * 64}


def _store() -> InMemoryRetentionStore:
    return InMemoryRetentionStore(audit=[
        _audit_doc("old", 400),
        _audit_doc("edge", 365.01),
        _audit_doc("recent", 10),
        {"_id": "other-type", "type": "something", "recorded_at": "2000-01-01T00:00:00Z"},
    ])


def _ids(store) -> set[str]:
    return {d["_id"] for d in store.iter_audit({})}


# ---------------------------------------------------------------------------
# Regra f
# ---------------------------------------------------------------------------

def test_audit_never_purged_by_default(monkeypatch):
    monkeypatch.delenv("AUDIT_RETENTION_DAYS", raising=False)
    store, audit = _store(), _Audit()
    report = run_retention(now=_NOW, store=store, vault=_NoVault(), audit=audit)
    assert report.audit_events_deleted == 0
    assert _ids(store) == {"old", "edge", "recent", "other-type"}
    assert audit.events[-1]["audit_events_deleted"] == 0


@pytest.mark.parametrize("raw", ["", "0", "-5", "abc"])
def test_audit_retention_disabled_values(monkeypatch, raw):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", raw)
    store = _store()
    report = run_retention(now=_NOW, store=store, vault=_NoVault(), audit=_Audit())
    assert report.audit_events_deleted == 0
    assert len(_ids(store)) == 4


def test_audit_older_than_limit_is_purged(monkeypatch):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "365")
    store, audit = _store(), _Audit()
    report = run_retention(now=_NOW, store=store, vault=_NoVault(), audit=audit)
    assert report.audit_events_deleted == 2
    assert _ids(store) == {"recent", "other-type"}
    run_event = audit.events[-1]
    assert run_event["event_type"] == "retention.run"
    assert run_event["audit_events_deleted"] == 2


def test_audit_purge_dry_run_counts_but_keeps(monkeypatch):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "365")
    store = _store()
    report = run_retention(now=_NOW, dry_run=True, store=store, vault=_NoVault(), audit=_Audit())
    assert report.audit_events_deleted == 2
    assert len(_ids(store)) == 4


def test_audit_purge_is_idempotent(monkeypatch):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "365")
    store = _store()
    run_retention(now=_NOW, store=store, vault=_NoVault(), audit=_Audit())
    second = run_retention(now=_NOW, store=store, vault=_NoVault(), audit=_Audit())
    assert second.audit_events_deleted == 0


def test_audit_purge_delete_error_is_counted(monkeypatch):
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "365")
    store = _store()

    def _boom(doc):
        raise RuntimeError("Cloudant 500")

    monkeypatch.setattr(store, "delete_audit", _boom)
    report = run_retention(now=_NOW, store=store, vault=_NoVault(), audit=_Audit())
    assert report.errors == 2 and report.audit_events_deleted == 0


def test_matches_supports_lt():
    assert _matches({"t": "2026-01-01T00:00:00Z"}, {"t": {"$lt": "2026-06-01T00:00:00Z"}})
    assert not _matches({"t": "2026-07-01T00:00:00Z"}, {"t": {"$lt": "2026-06-01T00:00:00Z"}})
    assert not _matches({}, {"t": {"$lt": "2026-06-01T00:00:00Z"}})


def test_cloudant_store_uses_audit_db():
    from unittest.mock import MagicMock

    from src.privacy.retention_store import CloudantRetentionStore

    client = MagicMock()
    client.post_find.return_value.get_result.return_value = {"docs": [{"_id": "x"}]}
    store = CloudantRetentionStore(client=client, audit_db="audit_trail_test")
    docs = list(store.iter_audit({"type": "audit_event"}))
    assert docs == [{"_id": "x"}]
    assert client.post_find.call_args.kwargs["db"] == "audit_trail_test"
    store.delete_audit({"_id": "x", "_rev": "1-a"})
    assert client.delete_document.call_args.kwargs == {"db": "audit_trail_test", "doc_id": "x", "rev": "1-a"}


def test_ensure_indexes_creates_recorded_at_index():
    from unittest.mock import MagicMock

    from src.privacy.retention_store import ensure_indexes

    client = MagicMock()
    ensure_indexes(client)
    names = {c.kwargs["name"] for c in client.post_index.call_args_list}
    assert "by-recorded" in names


# ---------------------------------------------------------------------------
# Agendador
# ---------------------------------------------------------------------------

def test_schedule_config_defaults(monkeypatch):
    for var in ("RETENTION_SCHEDULE_ENABLED", "RETENTION_INTERVAL_MINUTES",
                "RETENTION_INITIAL_DELAY_SECONDS", "RETENTION_SCHEDULE_DRY_RUN"):
        monkeypatch.delenv(var, raising=False)
    cfg = ScheduleConfig.from_env()
    assert cfg == ScheduleConfig(enabled=False, interval_seconds=86400,
                                 initial_delay_seconds=60, dry_run=False)


def test_schedule_config_from_env(monkeypatch):
    monkeypatch.setenv("RETENTION_SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("RETENTION_INTERVAL_MINUTES", "60")
    monkeypatch.setenv("RETENTION_INITIAL_DELAY_SECONDS", "0")
    monkeypatch.setenv("RETENTION_SCHEDULE_DRY_RUN", "yes")
    assert ScheduleConfig.from_env() == ScheduleConfig(True, 3600, 0, True)


@pytest.mark.parametrize("raw,expected", [("1", 300), ("abc", 86400), ("-10", 300)])
def test_schedule_interval_has_floor_and_fallback(monkeypatch, raw, expected):
    monkeypatch.setenv("RETENTION_INTERVAL_MINUTES", raw)
    assert ScheduleConfig.from_env().interval_seconds == expected


def test_loop_runs_job_at_interval():
    calls, sleeps = [], []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    cfg = ScheduleConfig(enabled=True, interval_seconds=3600, initial_delay_seconds=5, dry_run=True)
    runs = asyncio.run(retention_loop(
        cfg, runner=lambda dry_run: calls.append(dry_run), sleep=fake_sleep, max_runs=3,
    ))
    assert runs == 3
    assert calls == [True, True, True]
    assert sleeps == [5, 3600, 3600]


def test_loop_survives_job_failure():
    calls = []

    def flaky(dry_run):
        calls.append(dry_run)
        if len(calls) == 1:
            raise RuntimeError("Cloudant fora")

    async def fake_sleep(_):
        return None

    cfg = ScheduleConfig(True, 300, 0, False)
    assert asyncio.run(retention_loop(cfg, runner=flaky, sleep=fake_sleep, max_runs=2)) == 2
    assert len(calls) == 2


def test_run_once_skips_overlapping_execution():
    from src.privacy import retention_scheduler as rs

    rs._run_lock.acquire()
    try:
        called = []
        assert run_once(False, runner=lambda dry_run: called.append(1)) is False
        assert called == []
    finally:
        rs._run_lock.release()
    assert run_once(False, runner=lambda dry_run: None) is True


def test_run_once_reports_failure():
    def boom(dry_run):
        raise RuntimeError("x")

    assert run_once(False, runner=boom) is False


def test_run_once_calls_real_job(monkeypatch):
    seen = {}
    monkeypatch.setattr("src.privacy.retention.run_retention", lambda dry_run: seen.update(dry_run=dry_run))
    assert run_once(True) is True
    assert seen == {"dry_run": True}


def _scheduler_tasks():
    return [t for t in asyncio.all_tasks() if t.get_name() == "retention-scheduler"]


def test_lifespan_disabled_starts_nothing(monkeypatch):
    monkeypatch.delenv("RETENTION_SCHEDULE_ENABLED", raising=False)

    async def scenario():
        async with retention_lifespan(None):
            return _scheduler_tasks()

    assert asyncio.run(scenario()) == []


def test_lifespan_enabled_starts_and_cancels_task(monkeypatch):
    monkeypatch.setenv("RETENTION_SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("RETENTION_INITIAL_DELAY_SECONDS", "3600")  # não chega a rodar

    async def scenario():
        async with retention_lifespan(None):
            tasks = _scheduler_tasks()
            assert len(tasks) == 1 and not tasks[0].done()
        return tasks[0]

    task = asyncio.run(scenario())
    assert task.cancelled()


def test_app_startup_with_scheduler_enabled(monkeypatch):
    """A app sobe e desce com o agendador ligado (lifespan do router interno)."""
    from fastapi.testclient import TestClient

    from src.main import app

    monkeypatch.setenv("RETENTION_SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("RETENTION_INITIAL_DELAY_SECONDS", "3600")
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_app_lifespan_runs_job_when_enabled(monkeypatch):
    """Com atraso inicial zero, o job roda logo após a subida da app."""
    import threading

    from fastapi.testclient import TestClient

    from src.main import app

    ran = threading.Event()
    monkeypatch.setattr("src.privacy.retention.run_retention", lambda dry_run: ran.set())
    monkeypatch.setenv("RETENTION_SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("RETENTION_INITIAL_DELAY_SECONDS", "0")
    with TestClient(app):
        assert ran.wait(timeout=5)
