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


def test_schedule_bitmap_no_double_booking_or_wait_drift_across_ticks() -> None:
    sched = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )

    # 1. Queue j1 [0,1), j2 [1,2), j3 [2,4) on sole_uno's single 8x H100 NVL server
    q1 = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=1.0,
        preferred_site_id="sole_uno",
        job_name="j1",
    )
    b1 = sched.book_quote(quote_id=q1["quote_id"], idempotency_key="seq-1")
    assert b1["wait_time_min"] == 0
    assert b1["status"] == "running"

    q2 = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=1.0,
        preferred_site_id="sole_uno",
        job_name="j2",
    )
    b2 = sched.book_quote(quote_id=q2["quote_id"], idempotency_key="seq-2")
    assert b2["wait_time_min"] == 60
    assert b2["status"] == "queued"

    q3 = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=2.0,
        preferred_site_id="sole_uno",
        job_name="j3",
    )
    b3 = sched.book_quote(quote_id=q3["quote_id"], idempotency_key="seq-3")
    assert b3["wait_time_min"] == 120
    assert b3["status"] == "queued"

    # Advance 1h: j1 completes, j2 starts, j3 stays queued [2,4)
    tick1 = sched.dispatch_tick(hours=1.0)
    assert tick1["completed_jobs_count"] == 1

    # New 2h Spot quote at t=1 must wait until t=4 (180 min), never colliding with j3
    q4 = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=2.0,
        preferred_site_id="sole_uno",
        job_name="j4",
    )
    assert q4["wait_time_min"] == 180
    sched.book_quote(quote_id=q4["quote_id"], idempotency_key="seq-4")

    # Advance 1h to t=2: only j3 is running on sole_uno H100 NVL
    tick2 = sched.dispatch_tick(hours=1.0)
    running_h100 = [
        j
        for j in tick2["telemetry"]["active_jobs"]
        if j["site_id"] == "sole_uno"
        and j["gpu_or_sku"] == "h100_nvl_94gb"
        and j["status"] == "running"
    ]
    assert len(running_h100) == 1
    assert running_h100[0]["job_id"] == b3["job_id"]

    # 2. Stale wait inflation check: 4h job after 3 ticks (1h left) gives 60 min wait, not 240 min
    sched_wait = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    q_long = sched_wait.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=4,
        duration_hours=4.0,
        preferred_site_id="basel_erdsonde",
    )
    sched_wait.book_quote(quote_id=q_long["quote_id"], idempotency_key="long-rtx-1")
    for _ in range(3):
        sched_wait.dispatch_tick(hours=1.0)
    q_after = sched_wait.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=4,
        duration_hours=1.0,
        preferred_site_id="basel_erdsonde",
    )
    assert q_after["wait_time_min"] == 60

    # 3. Multi-hour tick (hours > 1.0) adjusts queued job remaining_hours & guards offline sites
    sched2 = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-01-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    qa = sched2.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="h100_sxm_80gb",
        quantity=8,
        duration_hours=1.0,
        preferred_site_id="thurgau_greenhouse",
    )
    sched2.book_quote(quote_id=qa["quote_id"], idempotency_key="jump-a")
    qb = sched2.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="h100_sxm_80gb",
        quantity=8,
        duration_hours=3.0,
        preferred_site_id="thurgau_greenhouse",
    )
    bb = sched2.book_quote(quote_id=qb["quote_id"], idempotency_key="jump-b")
    tick_jump = sched2.dispatch_tick(hours=2.0)
    assert tick_jump["completed_jobs_count"] == 1
    jb = sched2._jobs[bb["job_id"]]
    assert jb.status == "running"
    assert jb.remaining_hours == pytest.approx(2.0, abs=1e-6)

    # Offline site queued job must not flip to running while site remains offline
    qc = sched2.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="a100_sxm_80gb",
        quantity=2,
        duration_hours=2.0,
        preferred_site_id="emmental_dairy",
    )
    bc = sched2.book_quote(quote_id=qc["quote_id"], idempotency_key="off-q-1")
    sched2._jobs[bc["job_id"]].status = "queued"
    sched2._jobs[bc["job_id"]].start_hour = sched2._sim_hours_elapsed + 1.0
    sched2._sites["emmental_dairy"].offline_until_hour = sched2._sim_hours_elapsed + 5.0
    for srv_id in sched2._site_servers["emmental_dairy"]:
        sched2._servers[srv_id].status = "offline"
    sched2._jobs[bc["job_id"]].interruptible = False
    sched2.dispatch_tick(hours=1.5)
    assert sched2._jobs[bc["job_id"]].status == "queued"


def test_consumed_quote_cannot_be_rebooked_after_completion_or_migration() -> None:
    sched = FleetJobScheduler(log_store=TelemetryLogStore(), seed_initial_workloads=False)

    # Completion path
    q1 = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="b200_sxm_180gb",
        quantity=2,
        duration_hours=1.0,
        preferred_site_id="sole_uno",
    )
    r1 = sched.book_quote(quote_id=q1["quote_id"], idempotency_key="cons-1")
    assert r1["idempotent_replay"] is False

    sched.dispatch_tick(hours=1.0)
    assert sched._jobs[r1["job_id"]].status == "completed"

    # Same Idempotency-Key still replays cleanly
    r1_replay = sched.book_quote(quote_id=q1["quote_id"], idempotency_key="cons-1")
    assert r1_replay["idempotent_replay"] is True
    assert r1_replay["booking_id"] == r1["booking_id"]

    # Different Idempotency-Key must raise BookingConflictError (HTTP 409)
    with pytest.raises(BookingConflictError, match="already consumed"):
        sched.book_quote(quote_id=q1["quote_id"], idempotency_key="cons-2")

    # Migration path
    q_mig = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=2,
        duration_hours=3.0,
        preferred_site_id="basel_klingental",
    )
    rm = sched.book_quote(quote_id=q_mig["quote_id"], idempotency_key="mig-cons-1")
    upd = sched.update_site_demand("basel_klingental", indoor_temp_c=23.2, tank_soc_kwh=34.5)
    assert any(m["job_id"] == rm["job_id"] for m in upd["migrations"])
    with pytest.raises(BookingConflictError, match="already consumed"):
        sched.book_quote(quote_id=q_mig["quote_id"], idempotency_key="mig-cons-2")


def test_live_pid_unit_consistency_and_no_slot_zero_masking() -> None:
    sched = FleetJobScheduler(log_store=TelemetryLogStore(), seed_initial_workloads=False)
    real_like_worker = SimpleNamespace(
        state="running",
        pid=51515,
        job_type="iiif_embeddings",
        cpu_percent=88.0,
        estimated_cpu_power_w=75.0,
        scaled_1kw=SimpleNamespace(reference_server_power_kw=1.0, heat_recovered_kw=0.8),
    )

    tel_idle = sched.get_fleet_telemetry(worker_status=real_like_worker)
    basel_idle = next(s for s in tel_idle["sites"] if s["site_id"] == "basel_klingental")
    slot0 = basel_idle["servers"][0]["gpu_slots"][0]
    assert slot0["source"] == "live_pid"
    assert slot0["kw_e"] == pytest.approx(1.0, abs=1e-4)
    assert slot0["kw_th"] == pytest.approx(0.8, abs=1e-4)
    assert slot0["kw_th"] / slot0["kw_e"] == pytest.approx(0.80, abs=0.02)

    # Without scaled_1kw, kw_e and kw_th remain consistent at eta ~0.80
    unscaled_worker = SimpleNamespace(
        state="running",
        pid=51516,
        job_type="archive_ocr",
        cpu_percent=70.0,
        estimated_cpu_power_w=75.0,
        scaled_1kw=None,
    )
    tel_unscaled = sched.get_fleet_telemetry(worker_status=unscaled_worker)
    basel_unscaled = next(s for s in tel_unscaled["sites"] if s["site_id"] == "basel_klingental")
    slot0_u = basel_unscaled["servers"][0]["gpu_slots"][0]
    assert slot0_u["kw_e"] == pytest.approx(0.075, abs=1e-4)
    assert slot0_u["kw_th"] == pytest.approx(0.06, abs=1e-4)

    # Occupying slot 0 with a scheduled job must not be masked by live_pid
    q_occ = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=1,
        duration_hours=2.0,
        preferred_site_id="basel_klingental",
        job_name="scheduled-slot0",
    )
    b_occ = sched.book_quote(quote_id=q_occ["quote_id"], idempotency_key="occ-slot0-1")
    tel_busy = sched.get_fleet_telemetry(worker_status=real_like_worker)
    basel_busy = next(s for s in tel_busy["sites"] if s["site_id"] == "basel_klingental")
    slot0_busy = basel_busy["servers"][0]["gpu_slots"][0]
    assert slot0_busy["source"] == "simulated"
    assert slot0_busy["job_id"] == b_occ["job_id"]


def test_conserved_tank_trajectory_prevents_stacked_hydronode_dumping() -> None:
    sched = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )

    # First 30h hydronode_1kw (0.8 kW_th, 24.0 kWh) fits inside basel_klingental free tank + DHW
    q1 = sched.create_quote(
        tier="on_demand",
        unit_mode="server",
        gpu_or_sku="hydronode_1kw",
        quantity=1,
        duration_hours=30.0,
        preferred_site_id="basel_klingental",
    )
    assert q1["wait_time_min"] == 0
    assert q1["total_heat_kwh"] == pytest.approx(24.0, abs=1e-4)
    sched.book_quote(quote_id=q1["quote_id"], idempotency_key="tank-traj-1")

    # 2nd and 3rd consecutive 30h hydronode_1kw quotes must be rejected (tank_free committed)
    for _ in range(2):
        with pytest.raises(ThermalCapacityConflictError, match="thermal_headroom_saturated"):
            sched.create_quote(
                tier="on_demand",
                unit_mode="server",
                gpu_or_sku="hydronode_1kw",
                quantity=1,
                duration_hours=30.0,
                preferred_site_id="basel_klingental",
            )

    # Stepping 30 hours yields zero dumped heat at basel_klingental and fleet-wide
    last_tick = None
    for _ in range(30):
        last_tick = sched.dispatch_tick(hours=1.0)
    assert last_tick is not None
    assert sched._sites["basel_klingental"].cumulative_dumped_kwh == pytest.approx(0.0, abs=1e-6)
    assert last_tick["telemetry"]["summary"]["total_dumped_kwh"] == pytest.approx(0.0, abs=1e-6)

    # Near-full tank instant headroom must reject kw_th > instant_demand + (cap - soc)
    sched_full = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    sched_full.update_site_demand("basel_klingental", tank_soc_kwh=34.8)
    assert sched_full._instant_headroom_kw_locked("basel_klingental") < 0.8
    with pytest.raises(ThermalCapacityConflictError, match="thermal_headroom_saturated"):
        sched_full.create_quote(
            tier="on_demand",
            unit_mode="server",
            gpu_or_sku="hydronode_1kw",
            quantity=1,
            duration_hours=1.0,
            preferred_site_id="basel_klingental",
        )


def test_summer_on_demand_strict_sink_filtering_and_davos_warning() -> None:
    # A. 8x h100_nvl_94gb On-Demand saturates summer sinks without Davos spillover
    sched_h100 = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=True,
    )
    assigned_h100_sites: list[str] = []
    for idx in range(2):
        q = sched_h100.create_quote(
            tier="on_demand",
            unit_mode="gpu",
            gpu_or_sku="h100_nvl_94gb",
            quantity=8,
            duration_hours=4.0,
            preferred_site_id=None,
        )
        assert q["wait_time_min"] == 0
        assigned_h100_sites.append(q["assigned_site_id"])
        sched_h100.book_quote(quote_id=q["quote_id"], idempotency_key=f"sum-h100-{idx}")
    assert set(assigned_h100_sites) == {"sole_uno", "basel_st_jakob"}

    with pytest.raises(ThermalCapacityConflictError, match="thermal_headroom_saturated"):
        sched_h100.create_quote(
            tier="on_demand",
            unit_mode="gpu",
            gpu_or_sku="h100_nvl_94gb",
            quantity=8,
            duration_hours=4.0,
            preferred_site_id=None,
        )

    # B. 4x rtx_pro_4500_blackwell On-Demand uses basel_erdsonde then rejects
    sched_rtx = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=True,
    )
    q_erd = sched_rtx.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=4,
        duration_hours=8.0,
        preferred_site_id=None,
    )
    assert q_erd["assigned_site_id"] == "basel_erdsonde"
    sched_rtx.book_quote(quote_id=q_erd["quote_id"], idempotency_key="sum-rtx-0")

    with pytest.raises(ThermalCapacityConflictError, match="thermal_headroom_saturated"):
        sched_rtx.create_quote(
            tier="on_demand",
            unit_mode="gpu",
            gpu_or_sku="rtx_pro_4500_blackwell",
            quantity=4,
            duration_hours=8.0,
            preferred_site_id=None,
        )

    # C. Davos summer spa warning triggers whenever placement['site_id'] == 'davos_alpine'
    q_davos_spot_auto = sched_h100.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=4,
        duration_hours=2.0,
        preferred_site_id=None,
    )
    assert q_davos_spot_auto["assigned_site_id"] == "davos_alpine"
    assert any("snowmaking is a cooling load" in w for w in q_davos_spot_auto["warnings"])


@pytest.mark.parametrize("tail_hours", [1.0, 24.0])
def test_checkpointed_standby_never_double_books_schedule_bitmap(tail_hours: float) -> None:
    sched = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-01-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    for alt_sid in (
        "basel_erdsonde",
        "zurich_oerlikon",
        "bern_matte",
        "geneva_carouge",
        "lugano_lakeside",
    ):
        sched.failover_site(alt_sid, offline_hours=96.0)

    q_24h = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=4,
        duration_hours=24.0,
        preferred_site_id="basel_klingental",
        job_name="spot-24h",
    )
    b_24h = sched.book_quote(quote_id=q_24h["quote_id"], idempotency_key=f"chk-24-{tail_hours}")

    q_tail = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=4,
        duration_hours=tail_hours,
        preferred_site_id="basel_klingental",
        job_name="spot-tail",
    )
    b_tail = sched.book_quote(
        quote_id=q_tail["quote_id"], idempotency_key=f"chk-tail-{tail_hours}"
    )

    upd = sched.update_site_demand("basel_klingental", indoor_temp_c=22.6, tank_soc_kwh=32.5)
    assert any(m["reason"] == "checkpointed_standby" for m in upd["migrations"])

    # Disjoint bitmap reservations right after checkpointing
    h_24 = set(sched._job_abs_hours_locked(sched._jobs[b_24h["job_id"]]))
    h_tail = set(sched._job_abs_hours_locked(sched._jobs[b_tail["job_id"]]))
    assert h_24 and h_tail
    assert h_24.isdisjoint(h_tail)

    # No slot on basel_klingental ever has >1 running job across full execution horizon
    total_ticks = int(24 + tail_hours + 26)
    for _ in range(total_ticks):
        sched.dispatch_tick(hours=1.0)
        slot_owners: dict[tuple[str, int], list[str]] = {}
        for job in sched._jobs.values():
            if job.site_id != "basel_klingental" or job.status != "running":
                continue
            for srv_id, s_idx in job.gpu_slot_refs:
                slot_owners.setdefault((srv_id, s_idx), []).append(job.job_id)
        assert all(len(owners) <= 1 for owners in slot_owners.values())

    assert sched._jobs[b_24h["job_id"]].status == "completed"
    assert sched._jobs[b_tail["job_id"]].status == "completed"


def test_dispatch_tick_conserves_thermal_energy_and_matches_hourly_integration() -> None:
    # A. 1h hydronode_1kw completion conserves exact thermal energy
    sched_a = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    q1 = sched_a.create_quote(
        tier="on_demand",
        unit_mode="server",
        gpu_or_sku="hydronode_1kw",
        quantity=1,
        duration_hours=1.0,
        preferred_site_id="basel_klingental",
    )
    b1 = sched_a.book_quote(quote_id=q1["quote_id"], idempotency_key="cons-1h-hydro")
    soc0 = sched_a._sites["basel_klingental"].tank_soc_kwh
    sched_a.dispatch_tick(hours=1.0)
    dem1 = sched_a._sites["basel_klingental"].heat_demand_kw
    soc1 = sched_a._sites["basel_klingental"].tank_soc_kwh
    assert sched_a._jobs[b1["job_id"]].status == "completed"
    assert soc1 == pytest.approx(round(soc0 + q1["kw_th"] * 1.0 - dem1 * 1.0, 3), abs=1e-3)

    # B. Queued job promoted at t=1 injects 0.0 kWh phantom heat during [0, 1)
    sched_b = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    sched_b.update_site_demand("basel_klingental", tank_soc_kwh=32.0)
    q_now = sched_b.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=1,
        duration_hours=1.0,
        preferred_site_id="basel_klingental",
    )
    b_now = sched_b.book_quote(quote_id=q_now["quote_id"], idempotency_key="cons-now-rtx")
    q_next = sched_b.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="rtx_pro_4500_blackwell",
        quantity=4,
        duration_hours=1.0,
        preferred_site_id="basel_klingental",
    )
    assert q_next["wait_time_min"] == 60
    b_next = sched_b.book_quote(quote_id=q_next["quote_id"], idempotency_key="cons-next-rtx")
    assert b_next["status"] == "queued"

    sched_b.dispatch_tick(hours=1.0)
    dem_b1 = sched_b._sites["basel_klingental"].heat_demand_kw
    soc_b1 = sched_b._sites["basel_klingental"].tank_soc_kwh
    assert sched_b._jobs[b_now["job_id"]].status == "completed"
    assert sched_b._jobs[b_next["job_id"]].status == "running"
    assert soc_b1 == pytest.approx(
        round(32.0 + q_now["kw_th"] * 1.0 - dem_b1 * 1.0, 3), abs=1e-3
    )

    # C. Multi-hour dispatch_tick(3.0) matches 3 x dispatch_tick(1.0) hourly integration
    sched_1h = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    sched_3h = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    booked_1h: list[str] = []
    booked_3h: list[str] = []
    for idx, (sched, out_ids) in enumerate([(sched_1h, booked_1h), (sched_3h, booked_3h)]):
        qa = sched.create_quote(
            tier="on_demand",
            unit_mode="gpu",
            gpu_or_sku="rtx_pro_4500_blackwell",
            quantity=4,
            duration_hours=1.0,
            preferred_site_id="basel_klingental",
        )
        out_ids.append(
            sched.book_quote(quote_id=qa["quote_id"], idempotency_key=f"mh-a-{idx}")["job_id"]
        )
        qb = sched.create_quote(
            tier="on_demand",
            unit_mode="server",
            gpu_or_sku="hydronode_1kw",
            quantity=3,
            duration_hours=3.0,
            preferred_site_id="basel_klingental",
        )
        out_ids.append(
            sched.book_quote(quote_id=qb["quote_id"], idempotency_key=f"mh-b-{idx}")["job_id"]
        )
        qc = sched.create_quote(
            tier="spot",
            unit_mode="gpu",
            gpu_or_sku="rtx_pro_4500_blackwell",
            quantity=4,
            duration_hours=2.0,
            preferred_site_id="basel_klingental",
        )
        assert qc["wait_time_min"] == 60
        out_ids.append(
            sched.book_quote(quote_id=qc["quote_id"], idempotency_key=f"mh-c-{idx}")["job_id"]
        )

    for _ in range(3):
        sched_1h.dispatch_tick(hours=1.0)
    sched_3h.dispatch_tick(hours=3.0)

    site_1h = sched_1h._sites["basel_klingental"]
    site_3h = sched_3h._sites["basel_klingental"]
    assert site_3h.tank_soc_kwh == pytest.approx(site_1h.tank_soc_kwh, abs=1e-3)
    assert site_3h.indoor_temp_c == pytest.approx(site_1h.indoor_temp_c, abs=0.01)
    assert site_3h.cumulative_dumped_kwh == pytest.approx(0.0, abs=1e-6)
    assert all(sched_1h._jobs[jid].status == "completed" for jid in booked_1h)
    assert all(sched_3h._jobs[jid].status == "completed" for jid in booked_3h)


def test_fractional_tick_quote_to_book_trajectory_alignment() -> None:
    sched = FleetJobScheduler(
        log_store=TelemetryLogStore(),
        simulation_clock_utc="2025-07-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    q1 = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=2.0,
        preferred_site_id="sole_uno",
    )
    sched.book_quote(quote_id=q1["quote_id"], idempotency_key="ft-sole-1")

    q2 = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=1.0,
        preferred_site_id="sole_uno",
    )
    assert q2["wait_time_min"] == 120  # starts at t=2.0

    sched.dispatch_tick(hours=0.6)
    # Exact remaining offset 1.4h aligns candidate window with [2.0, 3.0)
    with sched._lock:
        committed_exact = sched._site_committed_energy_kwh_locked("sole_uno", 2.0, 3.0)
        assert committed_exact == pytest.approx(0.0, abs=1e-6)
        assert sched._can_absorb_trajectory_locked("sole_uno", 1.4, 1.0, q2["kw_th"])

    b2 = sched.book_quote(quote_id=q2["quote_id"], idempotency_key="ft-sole-2")
    assert b2["status"] == "queued"
    assert b2["wait_time_min"] == 84
    assert sched._jobs[b2["job_id"]].start_hour == pytest.approx(2.0, abs=1e-6)


def test_firm_tier_preempts_and_evicts_spot_job() -> None:
    """Firm immediate tier (Vaibhav Tier.evicts) preempts & migrates/requeues Spot jobs."""
    log_store = TelemetryLogStore()
    sched = FleetJobScheduler(
        log_store=log_store,
        simulation_clock_utc="2025-01-15T14:00:00Z",
        seed_initial_workloads=False,
    )
    # Fill all 8 H100 NVL GPUs on sole_uno with an interruptible Spot job
    q_spot = sched.create_quote(
        tier="spot",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=3.0,
        preferred_site_id="sole_uno",
        job_name="spot-fill-sole-uno",
    )
    b_spot = sched.book_quote(quote_id=q_spot["quote_id"], idempotency_key="evict-spot-1")
    spot_jid = b_spot["job_id"]
    assert sched._jobs[spot_jid].status == "running"

    # Incoming firm On-Demand job on sole_uno must succeed with 0 min wait via Spot preemption
    q_firm = sched.create_quote(
        tier="on_demand",
        unit_mode="gpu",
        gpu_or_sku="h100_nvl_94gb",
        quantity=8,
        duration_hours=2.0,
        preferred_site_id="sole_uno",
        job_name="firm-preempt-sole-uno",
    )
    assert q_firm["wait_time_min"] == 0
    assert spot_jid in q_firm["preempted_job_ids"]

    b_firm = sched.book_quote(quote_id=q_firm["quote_id"], idempotency_key="evict-firm-1")
    assert b_firm["status"] == "running"
    assert len(b_firm["preempted_jobs"]) == 1
    assert b_firm["preempted_jobs"][0]["evicted_job_id"] == spot_jid
    # Evicted Spot job must either have migrated to another H100 NVL site or been checkpoint-requeued
    spot_after = sched._jobs[spot_jid]
    assert spot_after.site_id != "sole_uno" or spot_after.status == "queued"
    evict_logs = [
        e for e in log_store.query(limit=50)["entries"] if e["event_type"] == "spot_job_evicted"
    ]
    assert len(evict_logs) >= 1
