"""Job types and the yearly job stream.

Durations / GPU share / daily arrival shape: resampled from the Alibaba PAI GPU trace 2020.
Useful output per kWh: MLPerf Inference v6.1 on the 1 kW reference server (4x RTX PRO 4500),
or our own laptop measurement (data/derived/measured_jobs.json) for the embedding job.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import DERIVED, val

GPUS_PER_SERVER = 4


@dataclass(frozen=True)
class JobType:
    key: str
    label: str
    share: float          # share of arriving jobs
    slack_h: float        # extra time beyond pure runtime before the deadline
    interruptible: bool   # checkpoint & resume possible
    unit: str             # unit of useful output
    per_kwh_key: str      # where the output/kWh figure comes from
    max_dur_h: float = 72.0


JOB_TYPES: list[JobType] = [
    JobType("llm_batch", "LLM batch inference (Llama-3.1-8B: summaries, extraction)", 0.35, 24, True,
            "M tokens", "mlperf:llama31_8b_tokens_per_kwh"),
    JobType("transcribe", "Speech transcription (Whisper)", 0.25, 12, True,
            "k utterances", "mlperf:whisper_samples_per_kwh"),
    JobType("image_tag", "Image tagging / detection (YOLO)", 0.20, 24, True,
            "k images", "mlperf:yolo_images_per_kwh"),
    JobType("embed", "Document embeddings for RAG search (MiniLM)", 0.15, 48, True,
            "k passages", "measured:embed_items_per_kwh"),
    JobType("eval", "Model evaluation run (not checkpointable)", 0.05, 6, False,
            "runs", "count", max_dur_h=2.0),
]
JOB_TYPE = {j.key: j for j in JOB_TYPES}


def output_per_kwh() -> dict[str, float]:
    """Useful output per kWh electric for each job type (unit as in JobType.unit)."""
    ml = json.loads((DERIVED / "mlperf_reference_server.json").read_text())["benchmarks"]
    meas_path = DERIVED / "measured_jobs.json"
    meas = json.loads(meas_path.read_text()) if meas_path.exists() else {}
    embed = meas.get("embed", {}).get("items_per_kwh", 1.5e6)  # fallback until benchmark is run
    return {
        "llm_batch": ml["llama31_8b_tokens_per_kwh"] / 1e6,
        "transcribe": ml["whisper_samples_per_kwh"] / 1e3,
        "image_tag": ml["yolo_images_per_kwh"] / 1e3,
        "embed": embed / 1e3,
        "eval": 0.0,
    }


@dataclass
class Job:
    id: int
    kind: str
    arrival: int            # hour of year
    work: float             # server-hours (= kWh electric on a 1 kW server)
    rate: float             # max servers it can use at once (from trace GPU share)
    deadline: int           # hour of year by which it must be finished
    interruptible: bool
    remaining: float = field(init=False)
    site: str | None = None
    started: bool = False
    done_at: int | None = None
    hold_until: int = 0     # checkpoint in transit after a migration
    migrations: int = 0
    rejected: bool = False

    def __post_init__(self):
        self.remaining = self.work


def alibaba_stats() -> dict:
    return json.loads((DERIVED / "alibaba_job_stats.json").read_text())


BUNDLE = 12          # trace tasks per customer batch job (a "job" = one customer's batch submission)
MAX_RATE = 4.0       # a job may spread over at most 4 servers (16 GPUs)


def _pool() -> pd.DataFrame:
    p = pd.read_csv(DERIVED / "alibaba_job_sample.csv")
    p["rate"] = np.clip(p.gpus / GPUS_PER_SERVER, 0.05, 2.0)
    return p


def _sample(rng, pool, n: int, kinds: np.ndarray):
    """Vectorised: work (server-h) and rate (servers) for n jobs of the given kind indices."""
    atomic = np.array([not JOB_TYPES[k].interruptible for k in kinds])
    maxdur = np.array([JOB_TYPES[k].max_dur_h for k in kinds])
    idx = rng.integers(0, len(pool), size=(n, BUNDLE))
    dur = np.minimum(pool.dur_h.to_numpy()[idx], maxdur[:, None])
    rate = pool.rate.to_numpy()[idx]
    work_b = (dur * rate).sum(1)
    rate_b = np.minimum(rate.sum(1), MAX_RATE)
    # non-checkpointable eval runs are single trace tasks
    work = np.where(atomic, dur[:, 0] * rate[:, 0], work_b)
    r = np.where(atomic, rate[:, 0], rate_b)
    return work, r


def mean_job_work(seed: int = 0) -> float:
    """Expected server-hours per job (Monte-Carlo over the trace pool)."""
    rng = np.random.default_rng(seed)
    kinds = rng.choice(len(JOB_TYPES), size=20000, p=[j.share for j in JOB_TYPES])
    w, _ = _sample(rng, _pool(), len(kinds), kinds)
    return float(w.mean())


def generate_jobs(server_hours_per_day: float, hours: int = 8760, seed: int = 7) -> list[Job]:
    """Flat-over-the-year job stream delivering `server_hours_per_day` of work on average.

    Job *shapes* (duration, GPU share) and the *daily arrival curve* come from the Alibaba
    trace (tasks bundled into customer batches). Customers do not care about the season:
    demand is flat, heat is not. That tension is exactly what the scheduler has to solve.
    """
    st = alibaba_stats()
    rng = np.random.default_rng(seed)
    pool = _pool()
    per_day = server_hours_per_day / mean_job_work()
    days = hours // 24
    counts = rng.poisson(per_day, size=days)
    n = int(counts.sum())
    day = np.repeat(np.arange(days), counts)
    hour = rng.choice(24, size=n, p=np.array(st["arrival_hour_share"]))
    kinds = rng.choice(len(JOB_TYPES), size=n, p=[j.share for j in JOB_TYPES])
    work, rate = _sample(rng, pool, n, kinds)
    arrival = day * 24 + hour
    slack = np.array([JOB_TYPES[k].slack_h for k in kinds])
    deadline = arrival + np.ceil(work / rate).astype(int) + slack.astype(int)
    order = np.argsort(arrival, kind="stable")
    return [Job(int(i), JOB_TYPES[kinds[o]].key, int(arrival[o]), float(work[o]), float(rate[o]),
                int(deadline[o]), JOB_TYPES[kinds[o]].interruptible)
            for i, o in enumerate(order)]
