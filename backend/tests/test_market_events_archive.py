"""Tests for market-event persistence and the events archive endpoint (P1 §3).

Covers:
- the ``market_events`` table and its indexes created idempotently by init_db
  (§3.1);
- ``persist_events_once`` / ``events_persist_loop`` — upsert semantics,
  dedup on re-persist, late narrative backfill, ring-buffer cap (§3.2);
- GET /api/market/events/archive — shape, ticker filter, ``before`` cursor
  pagination (strictly less-than), limit clamping, validation, empty DB
  (§3.3);
- a byte-invariance regression for GET /api/market/events (P1 hard gate):
  the existing news-ticker endpoint response is exactly the pre-P1 shape.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.connection import get_conn, init_db
from app.events_archive import events_persist_loop, persist_events_once
from app.market import PriceCache
from app.routes.market import create_market_router

ARCHIVE_EVENT_KEYS = {
    "id", "ticker", "headline", "change_percent", "direction", "timestamp",
    "narrative",
}

EXPECTED_COLUMNS = {
    "id": ("TEXT", 0, 1),  # (type, notnull, pk)
    "ticker": ("TEXT", 1, 0),
    "headline": ("TEXT", 1, 0),
    "narrative": ("TEXT", 0, 0),
    "change_percent": ("REAL", 1, 0),
    "direction": ("TEXT", 1, 0),
    "timestamp": ("REAL", 1, 0),
}


def _fire_event(cache: PriceCache, ticker: str, ts: float, up: bool = True) -> None:
    """Drive one qualifying (+/-3%) tick move for a fresh ticker."""
    cache.update(ticker, 100.00, timestamp=ts - 1.0)
    cache.update(ticker, 103.00 if up else 97.00, timestamp=ts)


def _insert_rows(db_file: str, rows: list[tuple]) -> None:
    """Insert raw (id, ticker, headline, narrative, change_percent, direction,
    timestamp) tuples directly into market_events."""
    conn = get_conn(db_file)
    try:
        conn.executemany(
            "INSERT INTO market_events "
            "(id, ticker, headline, narrative, change_percent, direction, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _make_row(ticker: str, ts: float, narrative: str | None = None) -> tuple:
    return (
        str(uuid.uuid4()),
        ticker,
        f"{ticker} surges +3.0% in sudden move",
        narrative,
        3.0,
        "up",
        ts,
    )


@pytest_asyncio.fixture
async def archive_app(tmp_path, monkeypatch):
    """Market router app with an isolated DB, explicit db_path injection."""
    db_file = str(tmp_path / "archive.db")
    monkeypatch.setenv("DB_PATH", db_file)
    init_db(db_file)

    price_cache = PriceCache()
    test_app = FastAPI()
    test_app.include_router(create_market_router(price_cache, db_path=db_file))

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, db_file=db_file, cache=price_cache)


class TestMarketEventsSchema:
    """§3.1 — market_events table and indexes, idempotent init_db."""

    def test_table_created_with_contract_columns(self, tmp_path):
        db_file = str(tmp_path / "schema.db")
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            rows = conn.execute("PRAGMA table_info(market_events)").fetchall()
        finally:
            conn.close()
        columns = {row["name"]: (row["type"], row["notnull"], row["pk"]) for row in rows}
        assert columns == EXPECTED_COLUMNS

    def test_indexes_created(self, tmp_path):
        db_file = str(tmp_path / "schema.db")
        init_db(db_file)
        conn = get_conn(db_file)
        try:
            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'market_events'"
                )
            }
        finally:
            conn.close()
        assert "idx_market_events_timestamp" in names
        assert "idx_market_events_ticker_timestamp" in names

    def test_init_db_idempotent_and_preserves_rows(self, tmp_path):
        """Re-running init_db (every startup) neither errors nor drops data."""
        db_file = str(tmp_path / "schema.db")
        init_db(db_file)
        _insert_rows(db_file, [_make_row("ZAA", 1_700_000_010.0)])
        init_db(db_file)  # second startup
        conn = get_conn(db_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
        finally:
            conn.close()
        assert count == 1


class TestPersistEventsOnce:
    """§3.2 — single archiver pass semantics."""

    def test_empty_buffer_returns_zero(self, tmp_path):
        db_file = str(tmp_path / "persist.db")
        init_db(db_file)
        cache = PriceCache()
        assert persist_events_once(cache, db_file) == 0
        conn = get_conn(db_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    def test_inserts_events_with_all_fields(self, tmp_path):
        db_file = str(tmp_path / "persist.db")
        init_db(db_file)
        cache = PriceCache()
        _fire_event(cache, "ZAA", ts=1_700_000_010.0, up=True)
        _fire_event(cache, "ZBB", ts=1_700_000_020.0, up=False)

        assert persist_events_once(cache, db_file) == 2

        events = {e.id: e for e in cache.get_events()}
        conn = get_conn(db_file)
        try:
            rows = conn.execute(
                "SELECT * FROM market_events ORDER BY timestamp"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 2
        for row in rows:
            event = events[row["id"]]
            assert row["ticker"] == event.ticker
            assert row["headline"] == event.headline
            assert row["narrative"] is None
            assert row["change_percent"] == event.change_percent
            assert row["direction"] == event.direction
            assert row["timestamp"] == event.timestamp

    def test_repersist_is_deduplicated(self, tmp_path):
        db_file = str(tmp_path / "persist.db")
        init_db(db_file)
        cache = PriceCache()
        _fire_event(cache, "ZAA", ts=1_700_000_010.0)

        assert persist_events_once(cache, db_file) == 1
        assert persist_events_once(cache, db_file) == 1  # upsert, not error

        conn = get_conn(db_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
        finally:
            conn.close()
        assert count == 1

    def test_late_narrative_backfills_existing_row(self, tmp_path):
        """An event persisted pre-enrichment gains its narrative next pass."""
        db_file = str(tmp_path / "persist.db")
        init_db(db_file)
        cache = PriceCache()
        _fire_event(cache, "ZAA", ts=1_700_000_010.0)
        persist_events_once(cache, db_file)

        event_id = cache.get_events()[0].id
        assert cache.set_event_narrative(event_id, "ZAA rallies on simulated chatter")
        persist_events_once(cache, db_file)

        conn = get_conn(db_file)
        try:
            row = conn.execute(
                "SELECT narrative FROM market_events WHERE id = ?", (event_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row["narrative"] == "ZAA rallies on simulated chatter"

    def test_evicted_events_survive_in_archive(self, tmp_path):
        """Rows already archived persist after the ring buffer evicts them."""
        db_file = str(tmp_path / "persist.db")
        init_db(db_file)
        cache = PriceCache()
        _fire_event(cache, "ZOLD", ts=1_600_000_000.0)
        persist_events_once(cache, db_file)

        # 100 fresh events push ZOLD out of the 100-slot ring buffer.
        for i in range(100):
            _fire_event(cache, f"Z{i:03d}", ts=1_700_000_000.0 + i * 100)
        assert all(e.ticker != "ZOLD" for e in cache.get_events())
        persist_events_once(cache, db_file)

        conn = get_conn(db_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
            zold = conn.execute(
                "SELECT COUNT(*) FROM market_events WHERE ticker = 'ZOLD'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 101
        assert zold == 1

    @pytest.mark.asyncio
    async def test_events_persist_loop_persists_and_cancels_cleanly(self, tmp_path):
        db_file = str(tmp_path / "loop.db")
        init_db(db_file)
        cache = PriceCache()
        _fire_event(cache, "ZAA", ts=1_700_000_010.0)

        task = asyncio.create_task(events_persist_loop(cache, db_file, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        conn = get_conn(db_file)
        try:
            count = conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]
        finally:
            conn.close()
        assert count == 1


@pytest.mark.asyncio
class TestEventsArchiveEndpoint:
    """§3.3 — GET /api/market/events/archive."""

    async def test_empty_database(self, archive_app):
        response = await archive_app.client.get("/api/market/events/archive")
        assert response.status_code == 200
        assert response.json() == {"events": [], "has_more": False}

    async def test_shape_and_newest_first_order(self, archive_app):
        _insert_rows(
            archive_app.db_file,
            [
                _make_row("ZAA", 1_700_000_010.0),
                _make_row("ZBB", 1_700_000_020.0, narrative="ZBB pops on sim news"),
                _make_row("ZCC", 1_700_000_030.0),
            ],
        )
        response = await archive_app.client.get("/api/market/events/archive")
        assert response.status_code == 200
        body = response.json()
        events = body["events"]
        assert body["has_more"] is False
        assert [e["ticker"] for e in events] == ["ZCC", "ZBB", "ZAA"]
        for event in events:
            assert set(event.keys()) == ARCHIVE_EVENT_KEYS
        assert events[1]["narrative"] == "ZBB pops on sim news"
        assert events[0]["narrative"] is None

    async def test_end_to_end_from_price_cache(self, archive_app):
        """Cache event -> persist pass -> archive endpoint round trip."""
        _fire_event(archive_app.cache, "ZAA", ts=1_700_000_010.0, up=False)
        persist_events_once(archive_app.cache, archive_app.db_file)

        response = await archive_app.client.get("/api/market/events/archive")
        events = response.json()["events"]
        assert len(events) == 1
        assert events[0] == archive_app.cache.get_events()[0].to_dict()

    async def test_ticker_filter_uppercase_normalized(self, archive_app):
        _insert_rows(
            archive_app.db_file,
            [
                _make_row("ZAA", 1_700_000_010.0),
                _make_row("ZBB", 1_700_000_020.0),
                _make_row("ZAA", 1_700_000_030.0),
            ],
        )
        response = await archive_app.client.get(
            "/api/market/events/archive?ticker=zaa"
        )
        events = response.json()["events"]
        assert len(events) == 2
        assert all(e["ticker"] == "ZAA" for e in events)
        assert events[0]["timestamp"] > events[1]["timestamp"]

    async def test_ticker_no_match_and_blank_ticker(self, archive_app):
        _insert_rows(archive_app.db_file, [_make_row("ZAA", 1_700_000_010.0)])

        miss = await archive_app.client.get("/api/market/events/archive?ticker=NOPE")
        assert miss.status_code == 200
        assert miss.json() == {"events": [], "has_more": False}

        # Blank ticker is treated as absent — no filter.
        blank = await archive_app.client.get("/api/market/events/archive?ticker=")
        assert blank.status_code == 200
        assert len(blank.json()["events"]) == 1

    async def test_limit_default_50_and_has_more(self, archive_app):
        _insert_rows(
            archive_app.db_file,
            [_make_row(f"Z{i:03d}", 1_700_000_000.0 + i) for i in range(55)],
        )
        response = await archive_app.client.get("/api/market/events/archive")
        body = response.json()
        assert len(body["events"]) == 50
        assert body["has_more"] is True
        # The 50 NEWEST (highest timestamps) come back.
        assert body["events"][0]["ticker"] == "Z054"
        assert body["events"][-1]["ticker"] == "Z005"

    async def test_limit_clamped_to_1_and_200(self, archive_app):
        _insert_rows(
            archive_app.db_file,
            [_make_row(f"Z{i:03d}", 1_700_000_000.0 + i) for i in range(205)],
        )
        high = await archive_app.client.get("/api/market/events/archive?limit=99999")
        assert high.status_code == 200
        assert len(high.json()["events"]) == 200
        assert high.json()["has_more"] is True

        for low in ("0", "-5"):
            response = await archive_app.client.get(
                f"/api/market/events/archive?limit={low}"
            )
            assert response.status_code == 200
            assert len(response.json()["events"]) == 1

    async def test_limit_non_integer_returns_400(self, archive_app):
        for bad in ("abc", "2.5", ""):
            response = await archive_app.client.get(
                f"/api/market/events/archive?limit={bad}"
            )
            assert response.status_code == 400
            assert "error" in response.json()

    async def test_before_is_strictly_less_than(self, archive_app):
        _insert_rows(
            archive_app.db_file,
            [_make_row(f"Z{i}", 1_700_000_000.0 + i) for i in range(1, 6)],
        )
        response = await archive_app.client.get(
            f"/api/market/events/archive?before={1_700_000_003.0}"
        )
        events = response.json()["events"]
        # ts == before is excluded; only ts 1 and 2 remain.
        assert [e["ticker"] for e in events] == ["Z2", "Z1"]

    async def test_before_cursor_paginates_without_overlap(self, archive_app):
        _insert_rows(
            archive_app.db_file,
            [_make_row(f"Z{i}", 1_700_000_000.0 + i) for i in range(1, 11)],
        )
        seen: list[str] = []
        page = await archive_app.client.get("/api/market/events/archive?limit=4")
        body = page.json()
        assert len(body["events"]) == 4
        assert body["has_more"] is True
        seen += [e["ticker"] for e in body["events"]]

        cursor = body["events"][-1]["timestamp"]
        page = await archive_app.client.get(
            f"/api/market/events/archive?limit=4&before={cursor}"
        )
        body = page.json()
        assert len(body["events"]) == 4
        assert body["has_more"] is True
        seen += [e["ticker"] for e in body["events"]]

        cursor = body["events"][-1]["timestamp"]
        page = await archive_app.client.get(
            f"/api/market/events/archive?limit=4&before={cursor}"
        )
        body = page.json()
        assert len(body["events"]) == 2
        assert body["has_more"] is False
        seen += [e["ticker"] for e in body["events"]]

        assert seen == [f"Z{i}" for i in range(10, 0, -1)]  # no overlap, no gaps

    async def test_before_non_numeric_returns_400(self, archive_app):
        for bad in ("abc", ""):
            response = await archive_app.client.get(
                f"/api/market/events/archive?before={bad}"
            )
            assert response.status_code == 400
            assert "error" in response.json()

    async def test_ticker_and_before_combined(self, archive_app):
        _insert_rows(
            archive_app.db_file,
            [
                _make_row("ZAA", 1_700_000_010.0),
                _make_row("ZBB", 1_700_000_020.0),
                _make_row("ZAA", 1_700_000_030.0),
                _make_row("ZAA", 1_700_000_040.0),
            ],
        )
        response = await archive_app.client.get(
            f"/api/market/events/archive?ticker=ZAA&before={1_700_000_040.0}"
        )
        events = response.json()["events"]
        assert [e["timestamp"] for e in events] == [1_700_000_030.0, 1_700_000_010.0]
        assert all(e["ticker"] == "ZAA" for e in events)


@pytest.mark.asyncio
class TestEventsEndpointRegression:
    """P1 hard gate: GET /api/market/events response is byte-identical."""

    async def test_events_response_exactly_pre_p1_shape(
        self, app_client, fake_market_source
    ):
        cache = fake_market_source.price_cache
        cache.update("ZAA", 100.00, timestamp=1_700_000_009.0)
        cache.update("ZAA", 103.00, timestamp=1_700_000_010.0)

        response = await app_client.get("/api/market/events")
        assert response.status_code == 200
        events = response.json()["events"]
        assert len(events) == 1
        event_id = events[0]["id"]
        assert response.json() == {
            "events": [
                {
                    "id": event_id,
                    "ticker": "ZAA",
                    "headline": "ZAA surges +3.0% in sudden move",
                    "change_percent": 3.0,
                    "direction": "up",
                    "timestamp": 1_700_000_010.0,
                    "narrative": None,
                }
            ]
        }
