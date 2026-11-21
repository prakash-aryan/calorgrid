"""Unified deterministic reference scheduler, baseline, DH workloads and adapter hook."""

import math
from collections import defaultdict
from pathlib import Path
from typing import Literal, Protocol

from digitalheat.config import (
    DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H,
    DEFAULT_SERVERS_PER_HOUSE,
    ELCOM_BASEL_H4_CHF_PER_KWH,
    HEAT_PUMP_REF_COP,
    HEAT_RECOVERY_FRACTION,
    SERVER_ELECTRICAL_POWER_KW,
    SERVER_HEAT_OUTPUT_KW,
    TANK_CAPACITY_KWH,
    TANK_DEFAULT_START_KWH,
    HourlyWeather,
    JobSpec,
    JobTypeSpec,
    SchedulerDecision,
    SimulationResult,
)
from digitalheat.data_loader import load_weather_zurich
from digitalheat.physics import (
    compute_heating_days,
    compute_house_demand,
    simulate_tank_year,
)

WORKLOAD_CATALOG: dict[str, JobTypeSpec] = {
    "archive_ocr": JobTypeSpec(
        job_type="archive_ocr",
        label="Archival Newspaper and Manuscript OCR",
        unit_description="1 archival dossier scan batch",
        duration_hours_at_1kw=1,
        energy_kwh_per_job=1.0,
        deadline_hours=24,
        interruptible=True,
        arrival_share=0.50,
        local_cpu_runnable=True,
        scaling_formula=(
            "Measure local CPU energy per page e_page = P_cpu_w * dt_s / n_pages; "
            "1 kW reference server throughput = 3,600,000 J / e_page pages/server-hour."
        ),
    ),
    "iiif_embeddings": JobTypeSpec(
        job_type="iiif_embeddings",
        label="IIIF Archival Plate Visual Embeddings",
        unit_description="1 IIIF collection manifest batch",
        duration_hours_at_1kw=2,
        energy_kwh_per_job=2.0,
        deadline_hours=72,
        interruptible=True,
        arrival_share=0.50,
        local_cpu_runnable=True,
        scaling_formula=(
            "Measure local CPU energy per plate e_img = P_cpu_w * dt_s / n_images; "
            "1 kW reference server throughput = 3,600,000 J / e_img images/server-hour."
        ),
    ),
    "audio_transcription": JobTypeSpec(
        job_type="audio_transcription",
        label="Oral History Audio Indexing",
        unit_description="1 oral-history reel batch",
        duration_hours_at_1kw=2,
        energy_kwh_per_job=2.0,
        deadline_hours=72,
        interruptible=True,
        arrival_share=0.0,
        local_cpu_runnable=False,
        scaling_formula=(
            "Measure local CPU energy per audio minute e_min = P_cpu_w * dt_s / n_min; "
            "1 kW reference server throughput = 3,600,000 J / e_min audio-min/server-hour."
        ),
    ),
}

# Diurnal arrival weights (00:00..23:00 local time) modeled after Alibaba GPU cluster trace 2020
_RAW_DIURNAL = [
    0.018,
    0.015,
    0.013,
    0.012,
    0.013,
    0.016,
    0.025,
    0.038,
    0.052,
    0.066,
    0.072,
    0.070,
    0.064,
    0.068,
    0.072,
    0.070,
    0.064,
    0.056,
    0.048,
    0.042,
    0.036,
    0.030,
    0.026,
    0.024,
]
_DIURNAL_SUM = sum(_RAW_DIURNAL)
ALIBABA_2020_DIURNAL_WEIGHTS: list[float] = [w / _DIURNAL_SUM for w in _RAW_DIURNAL]

ARRIVAL_RATE_SOURCE: str = (
    "ASSUMPTION: 12.0 server-hours/server/day offered Digital Humanities batch backlog "
    "(50% cluster load: 50% archive_ocr 1h jobs + 50% iiif_embeddings 2h jobs) with "
    "24h diurnal weights derived from Alibaba cluster-trace-gpu-v2020."
)

SCALING_NOTES: str = (
    "1 kW reference server throughput scales measured local CPU energy per unit via "
    "3,600,000 J / e_unit_j; recovered thermal output is 0.80 kW_th per active server-hour."
)


def _validate_live_inputs(
    tank_soc_kwh: float,
    heat_demand_kw: float,
    outdoor_temp_c: float,
) -> None:
    if not (
        math.isfinite(tank_soc_kwh)
        and math.isfinite(heat_demand_kw)
        and math.isfinite(outdoor_temp_c)
    ):
        raise ValueError("Live step thermal inputs must be finite")
    if tank_soc_kwh < -1e-9 or tank_soc_kwh > TANK_CAPACITY_KWH + 1e-9:
        raise ValueError(f"tank_soc_kwh ({tank_soc_kwh}) outside [0, {TANK_CAPACITY_KWH}]")
    if heat_demand_kw < -1e-9:
        raise ValueError(f"heat_demand_kw ({heat_demand_kw}) must be >= 0")


def generate_dh_batch_jobs(
    hours: list[HourlyWeather],
    n_servers: int,
    offered_hours_per_server_per_day: float = 12.0,
    include_audio: bool = False,
) -> list[JobSpec]:
    """Deterministically generate annual Digital Humanities batch job arrivals."""
    if n_servers < 1:
        raise ValueError("n_servers must be >= 1")
    if (
        not math.isfinite(offered_hours_per_server_per_day)
        or offered_hours_per_server_per_day < 0.0
    ):
        raise ValueError("offered_hours_per_server_per_day must be finite and >= 0")

    daily_server_hours = n_servers * offered_hours_per_server_per_day
    if include_audio:
        type_shares = [
            ("archive_ocr", 0.40),
            ("iiif_embeddings", 0.40),
            ("audio_transcription", 0.20),
        ]
    else:
        type_shares = [
            ("archive_ocr", 0.50),
            ("iiif_embeddings", 0.50),
        ]

    accumulators: dict[str, float] = {jt: 0.0 for jt, _ in type_shares}
    jobs: list[JobSpec] = []
    next_id = 1

    for t_idx, h in enumerate(hours):
        weight = ALIBABA_2020_DIURNAL_WEIGHTS[h.local_hour]
        hour_server_hours = daily_server_hours * weight
        for jt, share in type_shares:
            spec = WORKLOAD_CATALOG[jt]
            expected_jobs = (hour_server_hours * share) / float(spec.duration_hours_at_1kw)
            accumulators[jt] += expected_jobs
            count = int(accumulators[jt] + 1e-12)
            accumulators[jt] -= count
            for _ in range(count):
                jobs.append(
                    JobSpec(
                        job_id=next_id,
                        job_type=jt,
                        arrival_hour=t_idx,
                        duration_hours=spec.duration_hours_at_1kw,
                        remaining_hours=spec.duration_hours_at_1kw,
                        deadline_hour=t_idx + spec.deadline_hours,
                        interruptible=spec.interruptible,
                    )
                )
                next_id += 1
    return jobs


class SchedulerAdapter(Protocol):
    """Pluggable deterministic scheduler interface (including Vaibhav/Vilas hook)."""

    name: str

    def schedule_year(
        self,
        hours: list[HourlyWeather],
        jobs: list[JobSpec],
        n_servers: int,
    ) -> list[SchedulerDecision]: ...

    def decide_live_step(
        self,
        tank_soc_kwh: float,
        heat_demand_kw: float,
        outdoor_temp_c: float,
        queue_depth: int,
    ) -> bool: ...


def _replay_capacity_schedule(
    hours: list[HourlyWeather],
    jobs: list[JobSpec],
    n_servers: int,
    hourly_capacity: list[int],
    heat_signals: list[bool] | None = None,
) -> tuple[list[SchedulerDecision], int]:
    """Replay a deterministic job queue against an hourly active-server capacity array."""
    if len(hourly_capacity) != len(hours):
        raise ValueError("hourly_capacity length must match hours length")

    arrivals_by_hour: dict[int, list[JobSpec]] = defaultdict(list)
    for j in jobs:
        arrivals_by_hour[j.arrival_hour].append(j.model_copy())

    queue: list[JobSpec] = []
    decisions: list[SchedulerDecision] = []

    for t_idx, h in enumerate(hours):
        still_valid: list[JobSpec] = []
        missed_t = 0
        for j in queue:
            if t_idx >= j.deadline_hour:
                missed_t += 1
            else:
                still_valid.append(j)
        queue = still_valid

        if t_idx in arrivals_by_hour:
            queue.extend(arrivals_by_hour[t_idx])
        queue.sort(
            key=lambda j: (j.deadline_hour - t_idx - j.remaining_hours, j.deadline_hour, j.job_id)
        )

        cap = max(0, min(n_servers, int(hourly_capacity[t_idx])))
        run_count = min(cap, len(queue))
        completed_t = 0
        remaining_queue: list[JobSpec] = []

        for idx, j in enumerate(queue):
            if idx < run_count:
                j.remaining_hours -= 1
                if j.remaining_hours <= 0:
                    completed_t += 1
                else:
                    remaining_queue.append(j)
            else:
                remaining_queue.append(j)
        queue = remaining_queue

        sig = heat_signals[t_idx] if heat_signals is not None else (run_count > 0)
        decisions.append(
            SchedulerDecision(
                timestamp_zurich=h.timestamp_zurich,
                server_power_kw=run_count * SERVER_ELECTRICAL_POWER_KW,
                active_servers=run_count,
                jobs_completed=completed_t,
                jobs_missed_deadline=missed_t,
                heat_signal_on=sig,
            )
        )

    return decisions, len(queue)


class RunOnArrivalBaseline:
    """FIFO baseline running queued jobs immediately up to n_servers regardless of tank SOC."""

    name: str = "baseline"

    def decide_live_step(
        self,
        tank_soc_kwh: float,
        heat_demand_kw: float,
        outdoor_temp_c: float,
        queue_depth: int,
    ) -> bool:
        _validate_live_inputs(tank_soc_kwh, heat_demand_kw, outdoor_temp_c)
        return queue_depth > 0

    def schedule_year(
        self,
        hours: list[HourlyWeather],
        jobs: list[JobSpec],
        n_servers: int,
    ) -> list[SchedulerDecision]:
        decisions, _ = self.schedule_year_with_queue_end(hours, jobs, n_servers)
        return decisions

    def schedule_year_with_queue_end(
        self,
        hours: list[HourlyWeather],
        jobs: list[JobSpec],
        n_servers: int,
    ) -> tuple[list[SchedulerDecision], int]:
        if isinstance(n_servers, bool) or n_servers < 1:
            raise ValueError("n_servers must be >= 1")
        arrivals_by_hour: dict[int, list[JobSpec]] = defaultdict(list)
        for j in jobs:
            arrivals_by_hour[j.arrival_hour].append(j.model_copy())

        queue: list[JobSpec] = []
        decisions: list[SchedulerDecision] = []

        for t_idx, h in enumerate(hours):
            still_valid: list[JobSpec] = []
            missed_t = 0
            for j in queue:
                if t_idx >= j.deadline_hour:
                    missed_t += 1
                else:
                    still_valid.append(j)
            queue = still_valid

            if t_idx in arrivals_by_hour:
                queue.extend(arrivals_by_hour[t_idx])
            # FIFO order by arrival_hour then job_id
            queue.sort(key=lambda j: (j.arrival_hour, j.job_id))

            run_count = min(n_servers, len(queue))
            completed_t = 0
            remaining_queue: list[JobSpec] = []
            for idx, j in enumerate(queue):
                if idx < run_count:
                    j.remaining_hours -= 1
                    if j.remaining_hours <= 0:
                        completed_t += 1
                    else:
                        remaining_queue.append(j)
                else:
                    remaining_queue.append(j)
            queue = remaining_queue

            decisions.append(
                SchedulerDecision(
                    timestamp_zurich=h.timestamp_zurich,
                    server_power_kw=run_count * SERVER_ELECTRICAL_POWER_KW,
                    active_servers=run_count,
                    jobs_completed=completed_t,
                    jobs_missed_deadline=missed_t,
                    heat_signal_on=(run_count > 0),
                )
            )
        return decisions, len(queue)


def _min_servers_for_sla_llf(
    queue: list[JobSpec],
    t_idx: int,
    n_servers: int,
    thermal_cap: int,
) -> int:
    max_k = min(n_servers, len(queue))
    must_run = sum(1 for j in queue if (j.deadline_hour - t_idx) <= j.remaining_hours)
    base_k = min(max_k, max(must_run, thermal_cap))
    if base_k >= max_k:
        return base_k
    if len(queue) <= n_servers:
        return base_k

    for k in range(base_k, max_k):
        active: list[list[int]] = []
        for idx, j in enumerate(queue):
            rem = j.remaining_hours - (1 if idx < k else 0)
            if rem > 0:
                active.append([j.deadline_hour, rem])
        feasible = True
        t = t_idx + 1
        while active:
            zero_cnt = 0
            for d, r in active:
                lax = d - t - r
                if lax < 0:
                    feasible = False
                    break
                if lax == 0:
                    zero_cnt += 1
            if not feasible or zero_cnt > n_servers:
                feasible = False
                break
            if len(active) <= n_servers:
                break
            active.sort(key=lambda item: (item[0] - t - item[1], item[0]))
            next_active: list[list[int]] = []
            for idx, item in enumerate(active):
                if idx < n_servers:
                    rem = item[1] - 1
                    if rem > 0:
                        next_active.append([item[0], rem])
                else:
                    next_active.append(item)
            active = next_active
            t += 1
        if feasible:
            return k
    return max_k


class ReferenceScheduler:
    """Unified heat-led LLF scheduler controlling both 8,760h runs and live PID SIGSTOP/SIGCONT."""

    def __init__(
        self,
        sla_override: bool = False,
        s_start_kwh: float = TANK_DEFAULT_START_KWH,
        extra_sink_kw: list[float] | None = None,
        primary_demand_override_kw: list[float] | None = None,
    ) -> None:
        if not math.isfinite(s_start_kwh) or not (0.0 <= s_start_kwh <= TANK_CAPACITY_KWH + 1e-9):
            raise ValueError(f"s_start_kwh ({s_start_kwh}) outside [0, {TANK_CAPACITY_KWH}]")
        self.sla_override = sla_override
        self.s_start_kwh = float(s_start_kwh)
        self.extra_sink_kw = extra_sink_kw
        self.primary_demand_override_kw = primary_demand_override_kw
        self.name = "smart_sla" if sla_override else "smart_heat_led"

    def max_thermal_servers(self, tank_soc_kwh: float, heat_demand_kw: float) -> int:
        """Maximum integer 1 kW servers whose 0.8 kW_th output fits in demand + tank headroom."""
        if not (math.isfinite(tank_soc_kwh) and math.isfinite(heat_demand_kw)):
            raise ValueError("Thermal inputs must be finite")
        if tank_soc_kwh < -1e-9 or tank_soc_kwh > TANK_CAPACITY_KWH + 1e-9:
            raise ValueError(f"tank_soc_kwh ({tank_soc_kwh}) outside [0, {TANK_CAPACITY_KWH}]")
        if heat_demand_kw < -1e-9:
            raise ValueError(f"heat_demand_kw ({heat_demand_kw}) must be >= 0")
        s_clamped = min(TANK_CAPACITY_KWH, max(0.0, tank_soc_kwh))
        d_clamped = max(0.0, heat_demand_kw)
        headroom_kwh = (TANK_CAPACITY_KWH - s_clamped) + d_clamped
        return int((headroom_kwh + 1e-9) // SERVER_HEAT_OUTPUT_KW)

    def decide_live_step(
        self,
        tank_soc_kwh: float,
        heat_demand_kw: float,
        outdoor_temp_c: float,
        queue_depth: int,
    ) -> bool:
        """Live SIGCONT (True) vs SIGSTOP (False) decision using the identical thermal rule."""
        _validate_live_inputs(tank_soc_kwh, heat_demand_kw, outdoor_temp_c)
        if queue_depth <= 0:
            return False
        return self.max_thermal_servers(tank_soc_kwh, heat_demand_kw) >= 1

    def schedule_year(
        self,
        hours: list[HourlyWeather],
        jobs: list[JobSpec],
        n_servers: int,
    ) -> list[SchedulerDecision]:
        decisions, _ = self.schedule_year_with_queue_end(hours, jobs, n_servers)
        return decisions

    def schedule_year_with_queue_end(
        self,
        hours: list[HourlyWeather],
        jobs: list[JobSpec],
        n_servers: int,
    ) -> tuple[list[SchedulerDecision], int]:
        if n_servers < 1:
            raise ValueError("n_servers must be >= 1")
        if self.extra_sink_kw is not None:
            if len(self.extra_sink_kw) != len(hours):
                raise ValueError("extra_sink_kw length must match hours length")
            for idx, val in enumerate(self.extra_sink_kw):
                if not math.isfinite(val) or val < 0.0:
                    raise ValueError(f"extra_sink_kw[{idx}] ({val}) must be finite and >= 0")
        if self.primary_demand_override_kw is not None:
            if len(self.primary_demand_override_kw) != len(hours):
                raise ValueError("primary_demand_override_kw length must match hours length")
            for idx, val in enumerate(self.primary_demand_override_kw):
                if not math.isfinite(val) or val < 0.0:
                    raise ValueError(
                        f"primary_demand_override_kw[{idx}] ({val}) must be finite and >= 0"
                    )

        heating_days = compute_heating_days(hours)
        house_demand = compute_house_demand(hours, heating_days=heating_days)

        arrivals_by_hour: dict[int, list[JobSpec]] = defaultdict(list)
        for j in jobs:
            arrivals_by_hour[j.arrival_hour].append(j.model_copy())

        queue: list[JobSpec] = []
        decisions: list[SchedulerDecision] = []
        s_t = float(self.s_start_kwh)

        for t_idx, h in enumerate(hours):
            still_valid: list[JobSpec] = []
            missed_t = 0
            for j in queue:
                if t_idx >= j.deadline_hour:
                    missed_t += 1
                else:
                    still_valid.append(j)
            queue = still_valid

            if t_idx in arrivals_by_hour:
                queue.extend(arrivals_by_hour[t_idx])
            queue.sort(
                key=lambda j: (
                    j.deadline_hour - t_idx - j.remaining_hours,
                    j.deadline_hour,
                    j.job_id,
                )
            )

            d_primary = (
                self.primary_demand_override_kw[t_idx]
                if self.primary_demand_override_kw is not None
                else house_demand[t_idx][2]
            )
            d_secondary = self.extra_sink_kw[t_idx] if self.extra_sink_kw is not None else 0.0
            d_total = d_primary + d_secondary

            thermal_cap = min(n_servers, self.max_thermal_servers(s_t, d_total))
            if self.sla_override:
                target_srv = _min_servers_for_sla_llf(queue, t_idx, n_servers, thermal_cap)
            else:
                target_srv = min(n_servers, len(queue), thermal_cap)

            completed_t = 0
            remaining_queue: list[JobSpec] = []
            for idx, j in enumerate(queue):
                if idx < target_srv:
                    j.remaining_hours -= 1
                    if j.remaining_hours <= 0:
                        completed_t += 1
                    else:
                        remaining_queue.append(j)
                else:
                    remaining_queue.append(j)
            queue = remaining_queue

            p_kw = target_srv * SERVER_ELECTRICAL_POWER_KW
            avail_t = s_t + HEAT_RECOVERY_FRACTION * p_kw
            q_prim = min(avail_t, d_primary)
            rem_prim = avail_t - q_prim
            surplus = max(0.0, rem_prim - TANK_CAPACITY_KWH)
            q_sec = min(surplus, d_secondary)
            s_t = min(TANK_CAPACITY_KWH, max(0.0, rem_prim - q_sec))

            decisions.append(
                SchedulerDecision(
                    timestamp_zurich=h.timestamp_zurich,
                    server_power_kw=p_kw,
                    active_servers=target_srv,
                    jobs_completed=completed_t,
                    jobs_missed_deadline=missed_t,
                    heat_signal_on=(thermal_cap >= 1),
                )
            )
        return decisions, len(queue)


class ExternalScheduleAdapter:
    """Adapter hook for external deterministic scheduler outputs (Vaibhav & Vilas schema)."""

    name: str = "adapter"

    def __init__(
        self,
        decisions: list[SchedulerDecision] | list[dict] | None = None,
        hourly_active_servers: list[int] | None = None,
        hourly_server_power_kw: list[float] | None = None,
        adapter_name: str = "external_adapter",
    ) -> None:
        self.name = adapter_name
        self._decisions_raw = decisions
        self._hourly_active_servers = hourly_active_servers
        self._hourly_server_power_kw = hourly_server_power_kw
        if decisions is None and hourly_active_servers is None and hourly_server_power_kw is None:
            raise ValueError(
                "ExternalScheduleAdapter requires decisions, "
                "hourly_active_servers or hourly_server_power_kw"
            )

    def decide_live_step(
        self,
        tank_soc_kwh: float,
        heat_demand_kw: float,
        outdoor_temp_c: float,
        queue_depth: int,
    ) -> bool:
        _validate_live_inputs(tank_soc_kwh, heat_demand_kw, outdoor_temp_c)
        if queue_depth <= 0:
            return False
        s_clamped = min(TANK_CAPACITY_KWH, max(0.0, tank_soc_kwh))
        d_clamped = max(0.0, heat_demand_kw)
        headroom = (TANK_CAPACITY_KWH - s_clamped) + d_clamped
        return headroom + 1e-9 >= SERVER_HEAT_OUTPUT_KW

    def schedule_year(
        self,
        hours: list[HourlyWeather],
        jobs: list[JobSpec],
        n_servers: int,
    ) -> list[SchedulerDecision]:
        decisions, _ = self.schedule_year_with_queue_end(hours, jobs, n_servers)
        return decisions

    def schedule_year_with_queue_end(
        self,
        hours: list[HourlyWeather],
        jobs: list[JobSpec],
        n_servers: int,
    ) -> tuple[list[SchedulerDecision], int]:
        if n_servers < 1:
            raise ValueError("n_servers must be >= 1")
        n_hours = len(hours)
        if self._decisions_raw is not None:
            if len(self._decisions_raw) != n_hours:
                raise ValueError(
                    f"Adapter decisions length ({len(self._decisions_raw)}) must equal {n_hours}"
                )
            parsed: list[SchedulerDecision] = []
            for idx, (h, item) in enumerate(zip(hours, self._decisions_raw)):
                dec = (
                    item
                    if isinstance(item, SchedulerDecision)
                    else SchedulerDecision.model_validate(item)
                )
                if dec.timestamp_zurich != h.timestamp_zurich:
                    raise ValueError(
                        f"Adapter timestamp mismatch at hour {idx}: "
                        f"{dec.timestamp_zurich} != {h.timestamp_zurich}"
                    )
                if not math.isfinite(dec.server_power_kw):
                    raise ValueError(
                        f"server_power_kw ({dec.server_power_kw}) must be finite at hour {idx}"
                    )
                if not (0 <= dec.active_servers <= n_servers):
                    raise ValueError(
                        f"active_servers ({dec.active_servers}) out of bounds "
                        f"[0, {n_servers}] at hour {idx}"
                    )
                if not (
                    0.0 <= dec.server_power_kw <= n_servers * SERVER_ELECTRICAL_POWER_KW + 1e-9
                ):
                    raise ValueError(
                        f"server_power_kw ({dec.server_power_kw}) out of bounds at hour {idx}"
                    )
                if (
                    abs(dec.server_power_kw - dec.active_servers * SERVER_ELECTRICAL_POWER_KW)
                    > 1e-6
                ):
                    raise ValueError(
                        f"server_power_kw ({dec.server_power_kw}) does not match "
                        f"active_servers ({dec.active_servers}) * {SERVER_ELECTRICAL_POWER_KW} "
                        f"at hour {idx}"
                    )
                if dec.jobs_completed < 0 or dec.jobs_missed_deadline < 0:
                    raise ValueError(
                        f"jobs_completed and jobs_missed_deadline must be >= 0 at hour {idx}"
                    )
                parsed.append(dec)
            return parsed, 0

        if self._hourly_active_servers is not None:
            if len(self._hourly_active_servers) != n_hours:
                raise ValueError(
                    f"hourly_active_servers length ({len(self._hourly_active_servers)}) "
                    f"must equal {n_hours}"
                )
            cap: list[int] = []
            for idx, v in enumerate(self._hourly_active_servers):
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ValueError(
                        f"hourly_active_servers[{idx}]={v!r} must be an integer, "
                        f"not {type(v).__name__}"
                    )
                if v < 0 or v > n_servers:
                    raise ValueError(
                        f"hourly_active_servers[{idx}]={v} out of bounds [0, {n_servers}]"
                    )
                cap.append(v)
            return _replay_capacity_schedule(hours, jobs, n_servers, cap)

        assert self._hourly_server_power_kw is not None
        if len(self._hourly_server_power_kw) != n_hours:
            raise ValueError(
                f"hourly_server_power_kw length ({len(self._hourly_server_power_kw)}) "
                f"must equal {n_hours}"
            )
        cap_from_kw: list[int] = []
        for idx, p_kw in enumerate(self._hourly_server_power_kw):
            if isinstance(p_kw, bool):
                raise ValueError(
                    f"hourly_server_power_kw[{idx}]={p_kw!r} must be numeric, not bool"
                )
            if not math.isfinite(p_kw):
                raise ValueError(f"hourly_server_power_kw[{idx}]={p_kw} must be finite")
            if p_kw < -1e-9 or p_kw > n_servers * SERVER_ELECTRICAL_POWER_KW + 1e-9:
                raise ValueError(f"hourly_server_power_kw[{idx}]={p_kw} out of bounds")
            units = p_kw / SERVER_ELECTRICAL_POWER_KW
            if abs(units - round(units)) > 1e-6:
                raise ValueError(
                    f"hourly_server_power_kw[{idx}]={p_kw} must be an integer multiple "
                    f"of {SERVER_ELECTRICAL_POWER_KW} kW"
                )
            cap_from_kw.append(int(round(units)))
        return _replay_capacity_schedule(hours, jobs, n_servers, cap_from_kw)


def run_annual_simulation(
    strategy: Literal["smart", "smart_heat_led", "smart_sla", "baseline", "adapter"] = "smart",
    n_servers: int = DEFAULT_SERVERS_PER_HOUSE,
    site: Literal["basel", "davos"] = "basel",
    s_start_kwh: float = TANK_DEFAULT_START_KWH,
    offered_hours_per_server_per_day: float = 12.0,
    extra_sink_kw: list[float] | None = None,
    electricity_price_chf: float = ELCOM_BASEL_H4_CHF_PER_KWH,
    heat_pump_cop: float = HEAT_PUMP_REF_COP,
    computing_price_chf_per_server_h: float = DEFAULT_COMPUTING_PRICE_CHF_PER_SERVER_H,
    data_dir: Path | None = None,
    include_hourly: bool = True,
    adapter: SchedulerAdapter | None = None,
) -> SimulationResult:
    """Load verified 8,760h weather, generate deterministic DH jobs, schedule and simulate tank."""
    weather = load_weather_zurich(site=site, data_dir=data_dir)
    jobs = generate_dh_batch_jobs(
        hours=weather.hours,
        n_servers=n_servers,
        offered_hours_per_server_per_day=offered_hours_per_server_per_day,
    )

    if strategy == "baseline":
        sched = RunOnArrivalBaseline()
        decisions, queue_end = sched.schedule_year_with_queue_end(weather.hours, jobs, n_servers)
    elif strategy in ("smart", "smart_heat_led"):
        sched = ReferenceScheduler(
            sla_override=False,
            s_start_kwh=s_start_kwh,
            extra_sink_kw=extra_sink_kw,
        )
        decisions, queue_end = sched.schedule_year_with_queue_end(weather.hours, jobs, n_servers)
    elif strategy == "smart_sla":
        sched = ReferenceScheduler(
            sla_override=True,
            s_start_kwh=s_start_kwh,
            extra_sink_kw=extra_sink_kw,
        )
        decisions, queue_end = sched.schedule_year_with_queue_end(weather.hours, jobs, n_servers)
    elif strategy == "adapter":
        if adapter is None:
            raise ValueError("strategy='adapter' requires a non-None adapter")
        if hasattr(adapter, "schedule_year_with_queue_end"):
            decisions, queue_end = adapter.schedule_year_with_queue_end(
                weather.hours, jobs, n_servers
            )
        else:
            decisions = adapter.schedule_year(weather.hours, jobs, n_servers)
            queue_end = 0
    else:
        raise ValueError(f"Unsupported strategy: {strategy}")

    return simulate_tank_year(
        hours=weather.hours,
        decisions=decisions,
        n_servers=n_servers,
        strategy=strategy,
        site=site,
        s_start_kwh=s_start_kwh,
        extra_sink_kw=extra_sink_kw,
        electricity_price_chf=electricity_price_chf,
        heat_pump_cop=heat_pump_cop,
        computing_price_chf_per_server_h=computing_price_chf_per_server_h,
        total_jobs_arrived=len(jobs),
        jobs_in_queue_year_end=queue_end,
        arrival_rate_source=ARRIVAL_RATE_SOURCE,
        scaling_notes=SCALING_NOTES,
        include_hourly=include_hourly,
    )
