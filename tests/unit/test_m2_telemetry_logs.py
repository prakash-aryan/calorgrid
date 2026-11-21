"""Unit tests for TelemetryLogStore ring buffer, sub-caps, concurrency, and module hooks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from digitalheat.configurator import compute_configuration_quote
from digitalheat.fleet import get_swiss_fleet_snapshot
from digitalheat.physics import simulate_verbund_scene
from digitalheat.scheduler import ReferenceScheduler, run_annual_simulation
from digitalheat.telemetry_log import (
    ALL_LOG_CATEGORIES,
    TelemetryLogStore,
    get_log_store,
    log_event,
)
from digitalheat.worker import WorkerManager


def test_telemetry_log_store_subcap_eviction_and_floors() -> None:
    store = TelemetryLogStore(maxlen=500)
    booking_seqs: list[int] = []
    for idx in range(5):
        entry = store.append(
            "customer_booking",
            "booking_confirmed",
            f"Booking {idx}",
            site_id="sole_uno",
            metrics={"idx": idx},
        )
        booking_seqs.append(entry["seq_id"])

    for idx in range(600):
        store.append(
            "analysis_task",
            "host_heatledger_quote",
            f"Slider quote {idx}",
            site_id="basel_klingental",
            metrics={"idx": idx},
        )

    snap = store.query(limit=500, order="asc")
    assert snap["total_buffered"] <= 500
    assert snap["total_logged"] == 605
    assert snap["evicted_by_subcap"] > 0
    assert snap["category_counts"]["customer_booking"] == 5
    assert snap["category_counts"]["analysis_task"] <= 110

    bookings = store.query(category="customer_booking", limit=50, order="asc")
    assert [e["seq_id"] for e in bookings["entries"]] == booking_seqs


def test_telemetry_log_store_monotonic_seq_after_clear_and_filters() -> None:
    store = TelemetryLogStore(maxlen=50)
    e1 = store.append(
        "simulation_run",
        "annual_simulation_completed",
        "Run 1",
        site_id="basel_st_jakob",
    )
    e2 = store.append(
        "thermal_rebalance",
        "proactive_thermal_migration",
        "Rebalance 1",
        site_id="davos_alpine",
    )
    assert e2["seq_id"] > e1["seq_id"]

    # Query after_sequence & site_id filter
    q = store.query(after_sequence=e1["seq_id"], site_id="davos_alpine")
    assert q["count"] == 1
    assert q["entries"][0]["seq_id"] == e2["seq_id"]

    store.clear()
    e3 = store.append(
        "worker_lifecycle",
        "worker_start",
        "Worker restarted",
        site_id="basel_klingental",
    )
    assert e3["seq_id"] > e2["seq_id"]


def test_telemetry_log_store_validation_and_exception_safe_helper() -> None:
    store = TelemetryLogStore(maxlen=20)
    with pytest.raises(ValueError, match="Unknown log category"):
        store.append("not_a_category", "evt", "bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Invalid log level"):
        store.append("simulation_run", "evt", "bad", level="critical")  # type: ignore[arg-type]

    # log_event must never raise even on invalid arguments
    res = log_event("invalid_cat", "evt", "safe", store=store)  # type: ignore[arg-type]
    assert res is None


def test_telemetry_log_store_concurrent_writers_and_readers() -> None:
    store = TelemetryLogStore(maxlen=500)

    def _writer(tid: int) -> None:
        for i in range(40):
            cat = ALL_LOG_CATEGORIES[(tid + i) % len(ALL_LOG_CATEGORIES)]
            store.append(cat, f"evt_{tid}_{i}", f"msg {tid}:{i}", metrics={"i": i})

    def _reader(_: int) -> int:
        total = 0
        for _ in range(25):
            snap = store.query(limit=100)
            seqs = [e["seq_id"] for e in snap["entries"]]
            assert seqs == sorted(seqs, reverse=True)
            total += snap["count"]
        return total

    with ThreadPoolExecutor(max_workers=12) as pool:
        futs = [pool.submit(_writer, i) for i in range(6)] + [
            pool.submit(_reader, i) for i in range(6)
        ]
        for fut in futs:
            fut.result()

    final = store.query(limit=500)
    assert final["total_logged"] == 240
    assert final["total_buffered"] == 240


def test_module_hooks_emit_into_telemetry_log_store() -> None:
    store = get_log_store()
    before_seq = store.query(limit=1)["latest_seq_id"]

    # 1. ReferenceScheduler.decide_live_step only logs on state transitions
    sched = ReferenceScheduler(sla_override=False)
    assert sched.decide_live_step(10.0, 6.0, 2.0, queue_depth=1) is True
    assert sched.decide_live_step(10.0, 6.0, 2.0, queue_depth=1) is True
    assert sched.decide_live_step(34.8, 0.2, 20.0, queue_depth=1) is False

    # 2. run_annual_simulation, simulate_verbund_scene, fleet snapshot, configurator quote
    run_annual_simulation(strategy="smart", n_servers=2, site="basel", include_hourly=False)
    simulate_verbund_scene(scene_id="sole_uno", n_servers=4)
    get_swiss_fleet_snapshot(day_of_year=15, hour=12, strategy="smart")
    compute_configuration_quote(deployment_id="house_basel", server_model_id="hydronode_1kw")

    # 3. WorkerManager lifecycle hook
    mgr = WorkerManager(log_store=store)
    try:
        mgr.start(job_type="iiif_embeddings", max_units=1)
        mgr.stop()
    finally:
        mgr.shutdown()

    after = store.query(after_sequence=before_seq, limit=200, order="asc")
    events = {(e["category"], e["event_type"]) for e in after["entries"]}
    assert ("scheduler_dispatch", "live_heat_signal_evaluated") in events
    assert ("simulation_run", "annual_simulation_completed") in events
    assert ("simulation_run", "verbund_scene_simulated") in events
    assert ("analysis_task", "host_heatledger_quote") in events
    assert ("worker_lifecycle", "worker_start") in events
    # decide_live_step fired 2 transitions (True -> False), not 3
    live_step_events = [
        e for e in after["entries"] if e["event_type"] == "live_heat_signal_evaluated"
    ]
    assert len(live_step_events) == 2
