"""Build the job and power catalogues from two open datasets.

    python scripts/fetch_workloads.py      # writes data/alibaba_gpu_jobs.json and data/mlperf_power.json

Jobs: the Alibaba GPU cluster trace 2020 (table pai_task_table, about 35 MB to
download, kept in data/raw/). Tasks that finished and fit in one 8-GPU server
are grouped by the GPUs they asked for; from each group the script takes the
real task at the median, the 90th and the 99th percentile of duration.

Power: MLPerf Inference datacenter results that include a power measurement
(MLCommons results repositories on GitHub). Each row is the average wall
power of one system while it runs one benchmark at full load.

No API key is needed. Both outputs name their source in the file.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import tarfile
import urllib.request
from datetime import date
from pathlib import Path

TRACE_PAGE = "https://github.com/alibaba/clusterdata/tree/master/cluster-trace-gpu-v2020"
TRACE_URL = "https://aliopentrace.oss-cn-beijing.aliyuncs.com/v2020GPUTraces/pai_task_table.tar.gz"
TRACE_COLUMNS = ["job_name", "task_name", "inst_num", "status", "start_time", "end_time",
                 "plan_cpu", "plan_mem", "plan_gpu", "gpu_type"]
SERVER_GPUS = 8                  # a job must fit in one server
BUCKETS = [                      # (label, lowest GPUs exclusive, highest GPUs inclusive)
    ("under 1 GPU", 0.0, 0.999),
    ("1 GPU", 0.999, 1.0),
    ("2 GPUs", 1.0, 2.0),
    ("3 to 4 GPUs", 2.0, 4.0),
    ("5 to 8 GPUs", 4.0, 8.0),
]
QUANTILES = [("median", 0.50), ("90th percentile", 0.90), ("99th percentile", 0.99)]

MLPERF_PAGE = "https://mlcommons.org/benchmarks/inference-datacenter/"
MLPERF_ROUNDS = {
    "v5.1": "https://raw.githubusercontent.com/mlcommons/inference_results_v5.1/main/summary_results.json",
    "v5.0": "https://raw.githubusercontent.com/mlcommons/inference_results_v5.0/main/summary_results.json",
}


def download(url: str, path: Path) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {url}")
        with urllib.request.urlopen(url, timeout=300) as response:
            path.write_bytes(response.read())
    return path


# ------------------------------------------------------------------------------ jobs


def trace_rows(archive: Path):
    with tarfile.open(archive) as tar:
        member = next(m for m in tar if m.name.endswith(".csv"))
        yield from csv.reader(io.TextIOWrapper(tar.extractfile(member), encoding="utf-8"))


def build_jobs(archive: Path) -> dict:
    total = finished = 0
    groups: dict[str, list] = {label: [] for label, _, _ in BUCKETS}
    for row in trace_rows(archive):
        total += 1
        job, task, inst, status, start, end, cpu, mem, gpu, gpu_type = row
        if status != "Terminated" or not start or not end:
            continue
        duration = float(end) - float(start)
        if duration <= 0:
            continue
        finished += 1
        instances = float(inst) if inst else 1.0
        gpus = instances * (float(gpu) if gpu else 0.0) / 100      # plan_gpu: 100 = one GPU
        for label, low, high in BUCKETS:
            if low < gpus <= high:
                groups[label].append((duration, job, task, instances, gpus,
                                      float(cpu or 0) / 100, float(mem or 0), gpu_type))
                break

    examples, stats = [], []
    for label, _, _ in BUCKETS:
        tasks = sorted(groups[label])
        stats.append({"group": label, "tasks": len(tasks),
                      "share_of_finished": round(len(tasks) / finished, 4)})
        for name, q in QUANTILES:
            duration, job, task, instances, gpus, cpu, mem, gpu_type = tasks[int(q * len(tasks))]
            examples.append({
                "id": f"{label.replace(' ', '-')}-{name.split()[0]}".lower(),
                "group": label, "duration_rank": name,
                "trace_job": job, "task_name": task, "instances": instances,
                "gpus": round(gpus, 3), "duration_s": duration,
                "cpu_cores": round(cpu * instances, 2), "mem_gib": round(mem * instances, 2),
                "gpu_type": gpu_type or "not recorded",
            })
    return {
        "source": "Alibaba GPU cluster trace 2020 (cluster-trace-gpu-v2020), table pai_task_table",
        "source_url": TRACE_PAGE, "data_url": TRACE_URL, "retrieved": date.today().isoformat(),
        "method": f"Tasks with status Terminated and a start and end time. GPUs = inst_num x "
                  f"plan_gpu / 100; kept if they fit in {SERVER_GPUS} GPUs. Within each group, "
                  f"sorted by duration, the task at each stated percentile.",
        "tasks_in_trace": total, "tasks_finished": finished,
        "groups": stats, "jobs": examples,
    }


# ----------------------------------------------------------------------------- power


def build_power() -> dict:
    systems = []
    for round_name, url in MLPERF_ROUNDS.items():
        with urllib.request.urlopen(url, timeout=120) as response:
            rows = json.load(response)
        for row in rows:
            if not (row.get("has_power") and str(row.get("Suite", "")).startswith("datacenter")
                    and row.get("Power_Units") == "Watts"):
                continue
            systems.append({
                "id": f"{row['ID']}/{row['Model']}/{row['Scenario']}",
                "round": round_name, "submission": row["ID"], "submitter": row["Submitter"],
                "system": row["System"], "accelerator": row["Accelerator"],
                "accelerators": int(row["a#"]), "nodes": int(row["Nodes"]),
                "benchmark": row["Model"], "scenario": row["Scenario"],
                "power_w": round(float(row["Power_Result"]), 1),
                "performance": row.get("Performance_Result"),
                "performance_units": row.get("Performance_Units"),
                "division": row.get("Category"), "availability": row.get("Availability"),
                "details_url": row.get("Details"),
            })
    return {
        "source": "MLPerf Inference datacenter results with power measurement, MLCommons",
        "source_url": MLPERF_PAGE, "data_urls": MLPERF_ROUNDS,
        "retrieved": date.today().isoformat(),
        "method": "Every datacenter result with has_power true and power in watts: the average "
                  "wall power of the whole system while it runs the benchmark at full load.",
        "systems": systems,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=Path("data/raw"),
                        help="where the downloaded trace archive is kept")
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()

    jobs = build_jobs(download(TRACE_URL, args.raw / "pai_task_table.tar.gz"))
    power = build_power()
    for name, data in (("alibaba_gpu_jobs.json", jobs), ("mlperf_power.json", power)):
        (args.out / name).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out / 'alibaba_gpu_jobs.json'}: {len(jobs['jobs'])} example jobs from "
          f"{jobs['tasks_finished']:,} finished tasks")
    print(f"wrote {args.out / 'mlperf_power.json'}: {len(power['systems'])} measured results")


if __name__ == "__main__":
    main()
