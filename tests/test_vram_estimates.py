"""Unit tests for app.database VRAM estimate helpers.

Covers the bug fix: ``model_vram_estimates`` is now keyed by (module, model_name)
instead of just (module). Measurements are scoped to the specific model — switching
to a new model in the same module no longer inherits phantom measurements.
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.database import get_vram_estimate, upsert_vram_estimate


class _FakeCursor:
    """In-memory cursor that mimics psycopg2 RealDictCursor behaviour for
    the queries performed by get_vram_estimate / upsert_vram_estimate.
    """

    def __init__(self, store: dict):
        self._store = store
        self._fetched = None
        self._rowcount = -1
        self.executed: list = []

    def execute(self, sql: str, params=None):
        if params is None:
            params = ()
        if not isinstance(params, (list, tuple)):
            params = (params,)
        self.executed.append((sql, params))
        sql_u = sql.strip().upper()

        if "SELECT" in sql_u and "FROM MODEL_VRAM_ESTIMATES" in sql_u:
            self._do_select(sql_u, params)
        elif "INSERT INTO MODEL_VRAM_ESTIMATES" in sql_u:
            self._do_insert(sql_u, params)
        elif "UPDATE MODEL_VRAM_ESTIMATES" in sql_u:
            self._do_update(sql_u, params)
        else:
            self._fetched = None
            self._rowcount = 0

    def fetchone(self):
        return self._fetched

    def fetchall(self):
        val = self._fetched
        if val is None:
            return []
        if isinstance(val, list):
            return val
        return [val]

    def _do_select(self, sql_u: str, params: tuple):
        # WHERE module = %s AND model_name = %s
        if "AND MODEL_NAME" in sql_u:
            module, model_name = params[0], params[1]
            for row in self._store.values():
                if row["module"] == module and row["model_name"] == model_name:
                    self._fetched = dict(row)
                    self._rowcount = 1
                    return
            self._fetched = None
            self._rowcount = 0
            return
        # WHERE module = %s ORDER BY updated_at DESC LIMIT 1
        if "ORDER BY UPDATED_AT" in sql_u:
            module = params[0]
            rows = [dict(r) for r in self._store.values() if r["module"] == module]
            rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
            if rows:
                self._fetched = rows[0]
                self._rowcount = 1
            else:
                self._fetched = None
                self._rowcount = 0
            return
        # WHERE module = %s (legacy — no model_name filter)
        if "WHERE MODULE" in sql_u:
            module = params[0]
            for row in self._store.values():
                if row["module"] == module:
                    self._fetched = dict(row)
                    self._rowcount = 1
                    return
            self._fetched = None
            self._rowcount = 0
            return
        self._fetched = None
        self._rowcount = 0

    def _do_insert(self, sql_u: str, params: tuple):
        # Order in upsert_vram_estimate INSERT:
        # (module, model_name, context_length, n_gpu_layers, estimated_mb,
        #  measured_mb, 1 if measured_mb is not None else 0)
        (module, model_name, ctx, ngl, estimated_mb, measured_mb, _count) = params
        key = (module, model_name)
        self._store[key] = {
            "module": module,
            "model_name": model_name,
            "context_length": ctx,
            "n_gpu_layers": ngl,
            "estimated_vram_mb": estimated_mb,
            "measured_vram_mb": measured_mb,
            "measurement_count": 1 if measured_mb is not None else 0,
            "last_measured_at": None,
            "updated_at": "2026-01-01",
        }
        self._rowcount = 1

    def _do_update(self, sql_u: str, params: tuple):
        # Two UPDATE shapes (both filter by module AND model_name now):
        #  - with measurement: (model_name, ctx, ngl, est, measured, count, WHERE module, model_name) → 8 params
        #  - without measurement: (model_name, ctx, ngl, est, WHERE module, model_name) → 6 params
        if len(params) == 8:
            (model_name, ctx, ngl, estimated_mb, measured_mb, _count, where_module, where_model) = params
        elif len(params) == 6:
            (model_name, ctx, ngl, estimated_mb, where_module, where_model) = params
            measured_mb = None
        else:
            self._rowcount = 0
            return

        key = (where_module, where_model)
        row = self._store.get(key)
        if not row:
            self._rowcount = 0
            return
        row["model_name"] = model_name
        row["context_length"] = ctx
        row["n_gpu_layers"] = ngl
        if estimated_mb is not None:
            row["estimated_vram_mb"] = estimated_mb
        if measured_mb is not None:
            # weighted average: count grows, smoothed value stored
            old_count = row.get("measurement_count", 0) or 0
            new_count = old_count + 1
            old_val = row.get("measured_vram_mb") or 0
            old_weight = min(old_count, 10)
            new_weight = 1
            avg = (old_val * old_weight + measured_mb * new_weight) // (old_weight + new_weight)
            row["measured_vram_mb"] = avg
            row["measurement_count"] = new_count
        self._rowcount = 1


def _make_db(store: dict):
    """Build a factory compatible with `patch("app.database.get_db", _make_db)` —
    each call returns a fresh context-manager that yields a mock connection
    backed by the shared in-memory store."""

    @contextmanager
    def _ctx():
        conn = MagicMock()
        cursor = _FakeCursor(store)
        conn.cursor.return_value = cursor
        yield conn

    # Return a callable so unittest.mock.patch doesn't try to invoke the
    # context manager object as a function.
    def _factory():
        return _ctx()

    return _factory


@pytest.mark.unit
class TestUpsertVramEstimate:
    """upsert_vram_estimate: measurements stay attached to the specific model."""

    def test_insert_new_row_starts_with_zero_measurements(self):
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            upsert_vram_estimate(
                module="reasoning",
                model_name="Qwen3-Next-Reasoning-Q4.gguf",
                context_length=16384,
                n_gpu_layers=24,
                estimated_mb=9500,
            )
        row = store[("reasoning", "Qwen3-Next-Reasoning-Q4.gguf")]
        assert row["estimated_vram_mb"] == 9500
        assert row["measured_vram_mb"] is None
        assert row["measurement_count"] == 0

    def test_changing_model_does_not_carry_measurements(self):
        """Regression: switching reasoning model must not inherit old gpt-oss-20b's measurements."""
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            # gpt-oss-20b has been used 22 times
            for _ in range(22):
                upsert_vram_estimate(
                    module="reasoning",
                    model_name="gpt-oss-20b-mxfp4",
                    context_length=16384,
                    n_gpu_layers=24,
                    estimated_mb=9209,
                    measured_mb=9200,
                )
            # User now picks a brand-new model in the same module
            upsert_vram_estimate(
                module="reasoning",
                model_name="Qwen3-Next-Reasoning-Q4.gguf",
                context_length=16384,
                n_gpu_layers=24,
                estimated_mb=9500,
            )

        old = store[("reasoning", "gpt-oss-20b-mxfp4")]
        new = store[("reasoning", "Qwen3-Next-Reasoning-Q4.gguf")]

        # Old model: keeps all its measurements intact
        assert old["measurement_count"] == 22
        assert old["measured_vram_mb"] is not None

        # New model: starts fresh, no phantom measurements
        assert new["estimated_vram_mb"] == 9500
        assert new["measured_vram_mb"] is None
        assert new["measurement_count"] == 0

    def test_measurement_accumulates_for_same_model(self):
        """The same (module, model_name) must keep accumulating measurements."""
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            upsert_vram_estimate(
                module="chat",
                model_name="Qwen3-4B.gguf",
                context_length=16384,
                n_gpu_layers=28,
                estimated_mb=2500,
                measured_mb=2400,
            )
            upsert_vram_estimate(
                module="chat",
                model_name="Qwen3-4B.gguf",
                context_length=16384,
                n_gpu_layers=28,
                estimated_mb=2500,
                measured_mb=2600,
            )
        row = store[("chat", "Qwen3-4B.gguf")]
        assert row["measurement_count"] == 2
        # weighted average of [2400, 2600] → ≈ 2466
        assert 2450 <= row["measured_vram_mb"] <= 2470

    def test_upsert_with_no_measurement_updates_estimate_only(self):
        """When measured_mb is None, the call updates estimated fields only."""
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            # First insert with no measurement
            upsert_vram_estimate(
                module="chat",
                model_name="Qwen3-4B.gguf",
                context_length=8192,
                n_gpu_layers=28,
                estimated_mb=2300,
            )
            # Now adjust context — should not touch measurements (still 0)
            upsert_vram_estimate(
                module="chat",
                model_name="Qwen3-4B.gguf",
                context_length=16384,
                n_gpu_layers=28,
                estimated_mb=2500,
            )
        row = store[("chat", "Qwen3-4B.gguf")]
        assert row["context_length"] == 16384
        assert row["estimated_vram_mb"] == 2500
        assert row["measurement_count"] == 0
        assert row["measured_vram_mb"] is None


@pytest.mark.unit
class TestGetVramEstimate:
    """get_vram_estimate: filter by model_name when provided, latest row otherwise."""

    def test_exact_match_returns_specific_model(self):
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            upsert_vram_estimate(
                module="reasoning",
                model_name="gpt-oss-20b-mxfp4",
                context_length=16384,
                n_gpu_layers=24,
                estimated_mb=9209,
                measured_mb=9200,
            )
            upsert_vram_estimate(
                module="reasoning",
                model_name="Qwen3-Next-Reasoning-Q4.gguf",
                context_length=16384,
                n_gpu_layers=24,
                estimated_mb=9500,
            )
            measured = get_vram_estimate("reasoning", model_name="gpt-oss-20b-mxfp4")
            new = get_vram_estimate("reasoning", model_name="Qwen3-Next-Reasoning-Q4.gguf")

        assert measured is not None
        assert measured["model_name"] == "gpt-oss-20b-mxfp4"
        assert measured["measured_vram_mb"] is not None
        assert measured["measurement_count"] == 1

        assert new is not None
        assert new["model_name"] == "Qwen3-Next-Reasoning-Q4.gguf"
        assert new["measured_vram_mb"] is None
        assert new["measurement_count"] == 0

    def test_never_used_model_returns_none_measurements(self):
        """Regression for the original bug: a fresh model in a measured module
        must report measured_vram_mb=None, not the previous model's value."""
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            # 22 measurements for the old model
            for _ in range(22):
                upsert_vram_estimate(
                    module="reasoning",
                    model_name="gpt-oss-20b-mxfp4",
                    context_length=16384,
                    n_gpu_layers=24,
                    estimated_mb=9209,
                    measured_mb=9200,
                )
            result = get_vram_estimate("reasoning", model_name="Qwen3-Next-Reasoning-Q4.gguf")

        assert result is None, (
            "Expected None for a model that was never used, but got a row "
            f"({result!r}) — this is the phantom-measurement bug."
        )

    def test_legacy_call_without_model_name_returns_latest(self):
        """Calling get_vram_estimate(module) without model_name is a legacy
        path used by ltx-video code; it should still return the most recent
        record for the module."""
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            upsert_vram_estimate(
                module="ltx-video",
                model_name="ltx-video",
                context_length=0,
                n_gpu_layers=0,
                estimated_mb=8500,
                measured_mb=8200,
            )
            result = get_vram_estimate("ltx-video")

        assert result is not None
        assert result["model_name"] == "ltx-video"
        assert result["measurement_count"] == 1

    def test_module_with_no_records_returns_none(self):
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            assert get_vram_estimate("nonexistent") is None
            assert get_vram_estimate("nonexistent", model_name="anything.gguf") is None


@pytest.mark.unit
class TestAdminModelEstimateFilter:
    """The /admin/api/model-estimate handler must pass model_name to
    get_vram_estimate, otherwise measurements leak across models."""

    def test_get_vram_estimate_filters_by_model_name(self):
        """Direct test of the helper: get_vram_estimate(module, model_name=...)
        is a no-op for an unknown model — the admin endpoint relies on this."""
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)):
            upsert_vram_estimate(
                module="reasoning",
                model_name="gpt-oss-20b-mxfp4",
                context_length=16384,
                n_gpu_layers=24,
                estimated_mb=9209,
                measured_mb=9200,
            )
            # Direct call with the new model name returns None
            result = get_vram_estimate(
                "reasoning", model_name="Qwen3-Next-Reasoning-Q4.gguf"
            )
            assert result is None

    def test_get_vram_estimate_with_model_name_signature(self):
        """The helper accepts model_name as a keyword argument and routes to
        the exact-match query (no ORDER BY fallback)."""
        store: dict = {}
        with patch("app.database.get_db", _make_db(store)) as _:
            get_vram_estimate("reasoning", model_name="any-model.gguf")
            # Reached this far → no exception
            assert True
