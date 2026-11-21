"""Unit tests for FleetJobScheduler, canonical sites, seasonal headroom, and atomic booking."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from digitalheat.fleet import CANONICAL_SITES, get_swiss_fleet_snapshot, resolve_site_id
from digitalheat.fleet_scheduler import (
    BookingConflictError,
    BookingNotFoundError,
    FleetJobScheduler,
    IdempotencyConflictError,
    QuoteExpiredError,
    ThermalCapacityConflictError,
    ValidationInputError,
)
from digitalheat.telemetry_log import TelemetryLogStore


def test_canonical_sites_and_immutable_fleet_snapshot() -> None:
    assert len(CANONICAL_SITES) == 18
    for expected_id in ("sole_uno", "ara_basel_biogas", "hotel_laundry_dhw", "brewery_dairy_cip"):
        assert expected_id in CANONICAL_SITES

    assert resolve_site_id("basel_pool_st_jakob") == "basel_st_jakob"
    assert resolve_site_id("house_basel") == "basel_klingental"
    assert resolve_site_id("davos_hotel") == "davos_alpine"
    assert resolve_site_id("basel_ref") == "basel_klingental"
    with pytest.raises(ValueError, match="Unknown site_id"):
        resolve_site_id("nonexistent_canton_node")

    snap1 = get_swiss_fleet_snapshot(day_of_year=196, hour=14, strategy="smart")
    orig_dem = snap1["nodes"][0]["live"]["heat_demand_kw"]
    snap1["nodes"][0]["live"]["heat_demand_kw"] = 99999.0
    snap1["fleet_annual"]["annual_dumped_kwh"] = 12345.0
    snap1["flows"].append({"from_id": "fake"})

    snap2 = get_swiss_fleet_snapshot(day_of_year=196, hour=14, strategy="smart")
    assert snap2["nodes"][0]["live"]["heat_demand_kw"] == orig_dem
    assert snap2["fleet_annual"]["annual_dumped_kwh"] == 0.0
    assert not any(f.get("from_id") == "fake" for f in snap2["flows"])


def test_fleet_scheduler_covers_all_models_and_gpus() -> None:
    sched = FleetJobScheduler(log_store=TelemetryLogStore(), seed_initial_workloads=False)
    tel = sched.get_fleet_telemetry()
    assert tel["summary"]["total_sites"] == 18

    seen_server_models: set[str] = set()
    seen_gpu_models: set[str] = set()
    for site in tel["sites"]:
        for srv in site["servers"]:
            seen_server_models.add(srv["server_model_id"])
            for slot in srv["gpu_slots"]:
                seen_gpu_models.add(slot["gpu_model_id"])

    assert {
        "hydronode_1kw",
        "ocp_winterfell_3kw",
        "b200_ai_10kw",
        "edge_micro_250w",
    }.issubset(seen_server_models)
    assert seen_gpu_models == {
        "b200_sxm_180gb",
        "h100_nvl_94gb",
        "h100_sxm_80gb",
        "a100_sxm_80gb",
        "rtx_pro_4500_blackwell",
    }


def test_seasonal_summer_vs_winter_headroom_enforcement() -> None:
    sched = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )

    # In July, Davos alpine has no space heating (only 6.8 kW spa), so 8x H100 NVL (~4 kW_th)
    # or high-density On-Demand must be rejected on zero-dump thermal saturation when tight
    # First saturate Davos's modest summer spa baseload with 4x H100 NVL
    q_davos_small = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=4,
        duration_hours=4.0,
        preferred_site_id="davos_alpine",
    )
    assert any("snowmaking is a cooling load" in w for w in q_davos_small["warnings"])

    # In July, sole_uno, ara_basel_biogas, and basel_st_jakob accept 8x B200 On-Demand (0 min wait)
    q_sole = sched.create_quote(
        tier="on_demand",
        unit_mode="server",
        gpu_or_sku="b200_ai_10kw",
        quantity=1,
        duration_hours=4.0,
        preferred_site_id="sole_uno",
    )
    assert q_sole["wait_time_min"] == 0
    assert q_sole["assigned_site_id"] == "sole_uno"

    q_pool = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="b200_sxm_180gb",
        quantity=4,
        duration_hours=3.0,
        preferred_site_id="basel_pool_st_jakob",
    )
    assert q_pool["assigned_site_id"] == "basel_st_jakob"
    assert q_pool["wait_time_min"] == 0

    # Oversized On-Demand request at a small residential node in July exceeds 35 kWh tank + DHW
    with pytest.raises(ThermalCapacityConflictError, match="thermal_headroom_saturated"):
        sched.create_quote(
            tier="on_demand",
            unit_mode="gpu",
            gpu_or_sku="rtx_pro_4500_blackwell",
            quantity=4,
            duration_hours=96.0,
            preferred_site_id="basel_klingental",
        )


def test_atomic_booking_idempotency_expiry_and_concurrency() -> None:
    store = TelemetryLogStore()
    sched = FleetJobScheduler(log_store=store, seed_initial_workloads=False)

    quote = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="b200_sxm_180gb",
        quantity=2,
        duration_hours=2.0,
        preferred_site_id="sole_uno",
    )
    qid = quote["quote_id"]

    with pytest.raises(ValidationInputError):
        sched.book_quote(quote_id=qid, idempotency_key="   ")
    with pytest.raises(BookingNotFoundError):
        sched.book_quote(quote_id="missing_quote_id", idempotency_key="k1")

    receipt1 = sched.book_quote(quote_id=qid, idempotency_key="idem-abc")
    assert receipt1["idempotent_replay"] is False
    assert receipt1["status"] == "running"

    receipt2 = sched.book_quote(quote_id=qid, idempotency_key="idem-abc")
    assert receipt2["idempotent_replay"] is True
    assert receipt2["booking_id"] == receipt1["booking_id"]

    quote2 = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="a100_sxm_80gb",
        quantity=1,
        duration_hours=1.0,
        preferred_site_id="ara_basel_biogas",
    )
    with pytest.raises(IdempotencyConflictError):
        sched.book_quote(quote_id=quote2["quote_id"], idempotency_key="idem-abc")

    # Force expiry on quote2 -> QuoteExpiredError (410)
    sched._quotes[quote2["quote_id"]].expires_at_mono -= 1000.0
    with pytest.raises(QuoteExpiredError):
        sched.book_quote(quote_id=quote2["quote_id"], idempotency_key="idem-expired")

    # Concurrency race: 2 quotes targeting the same 8 GPUs on thurgau_greenhouse
    q_race_a = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="h100_sxm_80gb",
        quantity=8,
        duration_hours=2.0,
        preferred_site_id="thurgau_greenhouse",
    )
    q_race_b = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="h100_sxm_80gb",
        quantity=8,
        duration_hours=2.0,
        preferred_site_id="thurgau_greenhouse",
    )

    def _try_book(args: tuple[str, str]) -> str:
        try:
            sched.book_quote(quote_id=args[0], idempotency_key=args[1])
            return "ok"
        except BookingConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                _try_book,
                [(q_race_a["quote_id"], "race-a"), (q_race_b["quote_id"], "race-b")],
            )
        )
    assert sorted(outcomes) == ["conflict", "ok"]


def test_proactive_migrate_failover_backfill_and_live_pid_overlay() -> None:
    store = TelemetryLogStore()
    sched = FleetJobScheduler(log_store=store, seed_initial_workloads=False)

    # Book an interruptible Spot job at basel_klingental
    q_spot = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=2,
        duration_hours=3.0,
        preferred_site_id="basel_klingental",
    )
    book = sched.book_quote(quote_id=q_spot["quote_id"], idempotency_key="spot-mig-1")
    job_id = book["job_id"]

    # Overheat basel_klingental above comfort_band_max_c -> triggers proactive_migrate
    upd = sched.update_site_demand("basel_klingental", indoor_temp_c=23.2, tank_soc_kwh=34.5)
    assert any(m["job_id"] == job_id for m in upd["migrations"])
    logs = store.query(category="thermal_rebalance", limit=20)
    assert logs["count"] >= 2

    # Tick advances clock and injects BACKFILL jobs
    tick = sched.dispatch_tick(hours=1.0)
    assert tick["backfill_jobs_added"] >= 1
    assert tick["telemetry"]["summary"]["backfill_kw_e"] > 0.0

    # Failover migrates jobs off an offline site
    q_f = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="a100_sxm_80gb",
        quantity=2,
        duration_hours=3.0,
        preferred_site_id="emmental_dairy",
    )
    sched.book_quote(quote_id=q_f["quote_id"], idempotency_key="failover-1")
    fo = sched.failover_site("emmental_dairy", offline_hours=4.0)
    assert fo["migrations"]

    # Live PID overlay marks basel_klingental slot 0 as live_pid while others remain simulated
    fake_worker = SimpleNamespace(
        state="running",
        pid=42424,
        job_type="archive_ocr",
        cpu_percent=91.5,
        estimated_cpu_power_w=75.0,
        scaled_1kw=SimpleNamespace(heat_recovered_kw=0.8),
    )
    tel = sched.get_fleet_telemetry(worker_status=fake_worker)
    basel = next(s for s in tel["sites"] if s["site_id"] == "basel_klingental")
    sole = next(s for s in tel["sites"] if s["site_id"] == "sole_uno")
    assert basel["servers"][0]["gpu_slots"][0]["source"] == "live_pid"
    assert basel["servers"][0]["gpu_slots"][0]["pid"] == 42424
    assert sole["servers"][0]["gpu_slots"][0]["source"] == "simulated"
