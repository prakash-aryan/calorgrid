"""Real local CPU worker runner and live SIGSTOP/SIGCONT manager for CalorGrid."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import psutil
import pytesseract
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from digitalheat.config import (
    SERVER_ELECTRICAL_POWER_KW,
    SERVER_HEAT_OUTPUT_KW,
    SPACE_HEATING_PEAK_KW,
    TANK_CAPACITY_KWH,
    UNRECOVERED_HEAT_FRACTION,
)
from digitalheat.scheduler import (
    ARRIVAL_RATE_SOURCE,
    WORKLOAD_CATALOG,
    ReferenceScheduler,
)

WorkerState = Literal["idle", "running", "paused"]
JobType = Literal["archive_ocr", "iiif_embeddings", "audio_transcription"]

# Software CPU meter constants (fallback when Linux RAPL energy_uj is root-only)
SOFTWARE_CPU_BASE_W: float = 15.0
SOFTWARE_CPU_PER_CORE_W: float = 15.0
SOFTWARE_CPU_MAX_CORES_CLAMP: float = 4.0
DEFAULT_MAX_CONTINUOUS_RUNTIME_S: float = 600.0
JOULES_PER_KWH: float = 3_600_000.0

RAPL_ENERGY_UJ_PATH = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
RAPL_MAX_RANGE_UJ_PATH = Path("/sys/class/powercap/intel-rapl:0/max_energy_range_uj")
DEJAVU_SERIF_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")


class WorkerEvent(BaseModel):
    """Timestamped lifecycle or heat-signal event for a local CPU worker."""

    timestamp_utc: str
    action: Literal[
        "start",
        "heat_signal_pause",
        "heat_signal_resume",
        "manual_pause",
        "manual_resume",
        "stop",
        "complete",
        "error",
    ]
    pid: int | None = None
    state: WorkerState
    heat_signal_on: bool
    tank_soc_kwh: float | None = None
    heat_demand_kw: float | None = None
    outdoor_temp_c: float | None = None
    detail: str


class Scaled1kWEquivalent(BaseModel):
    """Explicit 1 kW reference server scaling derived from measured local CPU energy."""

    reference_server_power_kw: float
    heat_recovered_kw: float
    basement_unrecovered_kw: float
    measured_energy_per_unit_j: float | None = None
    scaled_units_per_server_hour: float | None = None
    scaling_formula: str


class WorkerStatus(BaseModel):
    """Live local CPU worker telemetry, OS status and 1 kW reference scaling."""

    pid: int | None = None
    pgid: int | None = None
    state: WorkerState
    psutil_status: str
    job_type: JobType | None = None
    job_label: str | None = None
    units_completed: int
    units_total: int | None = None
    continuous: bool
    queue_depth: int
    active_runtime_s: float
    paused_time_s: float
    wall_elapsed_s: float
    cpu_time_s: float
    cpu_percent: float
    rss_mb: float
    estimated_cpu_power_w: float
    measured_energy_j: float
    energy_meter_method: str
    heat_signal_on: bool
    last_tank_soc_kwh: float | None = None
    last_heat_demand_kw: float | None = None
    last_outdoor_temp_c: float | None = None
    scaled_1kw: Scaled1kWEquivalent
    arrival_rate_source: str
    last_unit_output: dict | None = None
    last_exit_code: int | None = None
    last_error: str | None = None
    history: list[WorkerEvent] = Field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_archival_font(size: int = 24) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if DEJAVU_SERIF_FONT_PATH.is_file():
        try:
            return ImageFont.truetype(str(DEJAVU_SERIF_FONT_PATH), size=size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _run_archive_ocr_unit(unit_index: int) -> dict:
    """Render a synthetic Basel Staatsarchiv ledger page and run Tesseract OCR."""
    width, height = 1200, 1600
    img = Image.new("L", (width, height), color=244)
    draw = ImageDraw.Draw(img)
    font = _load_archival_font(size=24)

    ledger_lines = [
        (
            f"STAATSARCHIV BASEL-STADT  MUNICIPAL LEDGER FOLIO "
            f"{1890 + (unit_index % 40)}-{unit_index:04d}"
        ),
        "DISTRICT: KLEINBASEL KLINGENTAL & MITTLERE BRUECKE ARCHIVE",
        "ITEM    CATEGORY                     AMOUNT CHF   NOTES",
        "-----------------------------------------------------------------------",
        (
            f"01      Rhine Ferry Toll Ledger      {1420 + unit_index * 7:8.2f}   "
            "Verified winter crossing"
        ),
        (
            f"02      Klingental Timber & Coal     {895 + unit_index * 3:8.2f}   "
            "District heating reserve"
        ),
        (
            f"03      Public Bath House Fuel       {610 + unit_index * 5:8.2f}   "
            "St. Jakob boiler account"
        ),
        (
            f"04      Waterwheel Conduit Repair    {340 + unit_index * 2:8.2f}   "
            "Riehenteich canal section"
        ),
        (
            f"05      Guild Archive Binding        {215 + unit_index * 4:8.2f}   "
            "Folio preservation series"
        ),
        (
            f"06      Civic Granary Assessment     {1180 + unit_index * 6:8.2f}   "
            "Cantonal audit approved"
        ),
        "-----------------------------------------------------------------------",
        "CERTIFIED BY ARCHIVIST H. BURCKHARDT FOR CANTON BASEL-STADT DIGITAL INDEX",
    ]

    y_pos = 70
    for block in range(3):
        for line in ledger_lines:
            draw.text((64, y_pos), f"[B{block + 1}] {line}", fill=22, font=font)
            y_pos += 36
        y_pos += 28

    arr = np.asarray(img, dtype=np.uint8).copy()
    rng = np.random.default_rng(1894 + unit_index)
    noise_mask = rng.random(arr.shape) < 0.008
    arr[noise_mask] = rng.integers(90, 210, size=int(noise_mask.sum()), dtype=np.uint8)
    noisy_img = Image.fromarray(arr, mode="L")

    text = pytesseract.image_to_string(noisy_img, lang="eng", config="--psm 6")
    cleaned = " ".join(text.split())
    words = cleaned.split(" ") if cleaned else []
    return {
        "unit_type": "archive_ocr",
        "pages_processed": 1,
        "chars_extracted": len(cleaned),
        "words_extracted": len(words),
        "sample_text": cleaned[:240],
    }


def _run_iiif_embedding_unit(unit_index: int) -> dict:
    """Compute deterministic multi-scale 2D FFT spectral embeddings on 32 synthetic IIIF plates."""
    n_plates = 32
    plate_size = 512
    rng = np.random.default_rng(2025 + unit_index)
    plate_vectors = np.zeros((n_plates, 256), dtype=np.float64)

    for p_idx in range(n_plates):
        base = rng.normal(loc=128.0, scale=28.0, size=(plate_size, plate_size))
        grid_x = np.linspace(0.0, 12.0 * np.pi, plate_size, dtype=np.float64)
        wave = 24.0 * np.outer(np.sin(grid_x + p_idx * 0.2), np.cos(grid_x * 0.5))
        plate = np.clip(base + wave, 0.0, 255.0)

        spec = np.abs(np.fft.rfft2(plate))
        # Multi-scale spectral pooling: 8x8 low-freq block (64) + 12x16 mid-freq block (192) = 256
        low_block = spec[:32, :32].reshape(8, 4, 8, 4).mean(axis=(1, 3)).ravel()
        mid_block = spec[32:128, :128].reshape(12, 8, 16, 8).mean(axis=(1, 3)).ravel()
        vec = np.concatenate([low_block, mid_block])
        norm = float(np.linalg.norm(vec))
        if norm > 1e-12:
            vec = vec / norm
        plate_vectors[p_idx] = vec

    mean_vec = plate_vectors.mean(axis=0)
    l2_norm = float(np.linalg.norm(mean_vec))
    if l2_norm > 1e-12:
        mean_vec = mean_vec / l2_norm
        l2_norm = float(np.linalg.norm(mean_vec))

    digest = hashlib.sha256(np.round(mean_vec, 6).tobytes()).hexdigest()[:16]
    return {
        "unit_type": "iiif_embeddings",
        "plates_processed": n_plates,
        "embedding_dim": 256,
        "l2_norm": round(l2_norm, 6),
        "plate_digest": digest,
    }


def _run_audio_transcription_unit(unit_index: int) -> dict:
    """Compute STFT, 40-band Mel spectrogram and 13-coeff MFCC + delta features on 8 segments."""
    n_segments = 8
    sample_rate = 16000
    n_samples = sample_rate * 10
    n_fft = 512
    hop = 256
    n_mels = 40
    n_mfcc = 13

    rng = np.random.default_rng(1970 + unit_index)
    t = np.arange(n_samples, dtype=np.float64) / sample_rate
    window = np.hamming(n_fft)

    freqs_hz = np.fft.rfftfreq(n_fft, d=1.0 / sample_rate)
    mel_min = 0.0
    mel_max = 2595.0 * np.log10(1.0 + (sample_rate / 2.0) / 700.0)
    mel_pts = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_pts = 700.0 * (10.0 ** (mel_pts / 2595.0) - 1.0)
    filterbank = np.zeros((n_mels, len(freqs_hz)), dtype=np.float64)
    for m in range(1, n_mels + 1):
        f_left, f_center, f_right = hz_pts[m - 1], hz_pts[m], hz_pts[m + 1]
        up = (freqs_hz - f_left) / max(f_center - f_left, 1e-9)
        down = (f_right - freqs_hz) / max(f_right - f_center, 1e-9)
        filterbank[m - 1] = np.maximum(0.0, np.minimum(up, down))

    dct_k = np.arange(n_mfcc, dtype=np.float64)[:, None]
    dct_n = np.arange(n_mels, dtype=np.float64)[None, :]
    dct_mat = np.cos(np.pi * dct_k * (dct_n + 0.5) / n_mels)

    total_frames = 0
    for seg in range(n_segments):
        f0 = 140.0 + 18.0 * seg
        sig = (
            0.5 * np.sin(2.0 * np.pi * f0 * t)
            + 0.3 * np.sin(2.0 * np.pi * (2.0 * f0) * t)
            + 0.1 * rng.normal(0.0, 1.0, size=n_samples)
        )
        n_frames = 1 + (n_samples - n_fft) // hop
        strides = (sig.strides[0] * hop, sig.strides[0])
        frames = np.lib.stride_tricks.as_strided(sig, shape=(n_frames, n_fft), strides=strides)
        power_spec = (np.abs(np.fft.rfft(frames * window, axis=1)) ** 2) / n_fft
        mel_energies = np.maximum(1e-10, power_spec @ filterbank.T)
        mfcc = np.log(mel_energies) @ dct_mat.T
        delta = np.diff(mfcc, axis=0, prepend=mfcc[:1])
        _ = np.concatenate([mfcc, delta], axis=1)
        total_frames += n_frames

    return {
        "unit_type": "audio_transcription",
        "audio_seconds_indexed": float(n_segments * 10),
        "mfcc_frames": total_frames,
        "feature_dim": n_mfcc * 2,
        "reel_id": f"BS-ORAL-{1970 + unit_index:04d}",
    }


def run_single_unit(job_type: JobType, unit_index: int) -> dict:
    """Execute one real CPU workload unit for the specified job type."""
    if job_type == "archive_ocr":
        return _run_archive_ocr_unit(unit_index)
    if job_type == "iiif_embeddings":
        return _run_iiif_embedding_unit(unit_index)
    if job_type == "audio_transcription":
        return _run_audio_transcription_unit(unit_index)
    raise ValueError(f"Unsupported job_type: {job_type}")


def _can_read_rapl() -> bool:
    if not RAPL_ENERGY_UJ_PATH.is_file() or not os.access(RAPL_ENERGY_UJ_PATH, os.R_OK):
        return False
    try:
        int(RAPL_ENERGY_UJ_PATH.read_text(encoding="utf-8").strip())
        return True
    except (OSError, ValueError):
        return False


def _read_rapl_uj() -> int | None:
    try:
        return int(RAPL_ENERGY_UJ_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_rapl_max_uj() -> int:
    try:
        return int(RAPL_MAX_RANGE_UJ_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 2**32


class WorkerManager:
    """Thread-safe controller for a real CPU worker subprocess with SIGSTOP/SIGCONT."""

    def __init__(self, scheduler: ReferenceScheduler | None = None) -> None:
        self.scheduler = (
            scheduler if scheduler is not None else ReferenceScheduler(sla_override=False)
        )
        self._lock = threading.RLock()
        self._proc: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._exit_thread: threading.Thread | None = None
        self._generation: int = 0

        self._pid: int | None = None
        self._pgid: int | None = None
        self._state: WorkerState = "idle"
        self._job_type: JobType | None = None
        self._units_completed: int = 0
        self._units_total: int | None = None
        self._continuous: bool = False

        self._started_mono: float | None = None
        self._last_tick_mono: float | None = None
        self._exited_mono: float | None = None
        self._last_unit_done_mono: float | None = None
        self._active_runtime_s: float = 0.0
        self._paused_time_s: float = 0.0
        self._cpu_time_hwm_s: float = 0.0
        self._prev_cpu_time_s: float = 0.0
        self._last_cpu_percent: float = 0.0
        self._last_rss_mb: float = 0.0
        self._last_power_w: float = 0.0
        self._measured_energy_j: float = 0.0

        self._use_rapl: bool = _can_read_rapl()
        self._energy_meter_method: str = (
            "linux_rapl" if self._use_rapl else "software_cpu_meter_psutil"
        )
        self._last_rapl_uj: int | None = None
        self._rapl_max_uj: int = _read_rapl_max_uj() if self._use_rapl else 0

        self._heat_signal_on: bool = False
        self._last_tank_soc_kwh: float | None = None
        self._last_heat_demand_kw: float | None = None
        self._last_outdoor_temp_c: float | None = None
        self._last_unit_output: dict | None = None
        self._last_exit_code: int | None = None
        self._last_error: str | None = None
        self._history: list[WorkerEvent] = []

    def _append_event_locked(
        self,
        action: Literal[
            "start",
            "heat_signal_pause",
            "heat_signal_resume",
            "manual_pause",
            "manual_resume",
            "stop",
            "complete",
            "error",
        ],
        detail: str,
        tank_soc_kwh: float | None = None,
        heat_demand_kw: float | None = None,
        outdoor_temp_c: float | None = None,
    ) -> None:
        self._history.append(
            WorkerEvent(
                timestamp_utc=_utc_now_iso(),
                action=action,
                pid=self._pid,
                state=self._state,
                heat_signal_on=self._heat_signal_on,
                tank_soc_kwh=tank_soc_kwh,
                heat_demand_kw=heat_demand_kw,
                outdoor_temp_c=outdoor_temp_c,
                detail=detail,
            )
        )
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def _read_stdout_loop(self, proc: subprocess.Popen[str], generation: int) -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                with self._lock:
                    if generation != self._generation:
                        return
                    self._units_completed += 1
                    self._last_unit_output = payload.get("result")
                    self._last_unit_done_mono = time.monotonic()
        except (ValueError, OSError):
            pass

    def _read_stderr_loop(self, proc: subprocess.Popen[str], generation: int) -> None:
        if proc.stderr is None:
            return
        chunks: list[str] = []
        try:
            for line in proc.stderr:
                chunks.append(line.strip())
                if len(chunks) > 20:
                    chunks = chunks[-20:]
        except (ValueError, OSError):
            return
        if chunks:
            with self._lock:
                if generation == self._generation and self._last_exit_code not in (None, 0, -15):
                    self._last_error = " | ".join(c for c in chunks if c)[-500:]

    def _observe_psutil_locked(self) -> tuple[str, float, float, float]:
        """Return (psutil_status, cpu_time_s, cpu_percent, rss_mb) for the live worker tree."""
        if self._proc is None or self._pid is None:
            return "idle", self._cpu_time_hwm_s, 0.0, 0.0
        try:
            p = psutil.Process(self._pid)
            status_str = p.status()
            if self._state == "paused" or status_str == psutil.STATUS_STOPPED:
                return status_str, self._cpu_time_hwm_s, 0.0, round(self._last_rss_mb, 3)

            ct = p.cpu_times()
            total_cpu = float(ct.user + ct.system)
            total_cpu += float(
                getattr(ct, "children_user", 0.0) + getattr(ct, "children_system", 0.0)
            )
            if status_str == psutil.STATUS_ZOMBIE:
                self._cpu_time_hwm_s = max(self._cpu_time_hwm_s, total_cpu)
                self._last_cpu_percent = 0.0
                return status_str, self._cpu_time_hwm_s, 0.0, round(self._last_rss_mb, 3)

            rss_bytes = int(p.memory_info().rss)
            cpu_pct = float(p.cpu_percent(interval=None))

            for child in p.children(recursive=True):
                try:
                    cct = child.cpu_times()
                    total_cpu += float(cct.user + cct.system)
                    rss_bytes += int(child.memory_info().rss)
                    cpu_pct += float(child.cpu_percent(interval=None))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            self._cpu_time_hwm_s = max(self._cpu_time_hwm_s, total_cpu)
            self._last_rss_mb = round(rss_bytes / (1024.0 * 1024.0), 3)
            self._last_cpu_percent = round(cpu_pct, 2)
            return status_str, self._cpu_time_hwm_s, self._last_cpu_percent, self._last_rss_mb
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return "terminated", self._cpu_time_hwm_s, 0.0, self._last_rss_mb

    def _sample_telemetry_locked(
        self, now_mono: float | None = None
    ) -> tuple[str, float, float, float]:
        now = time.monotonic() if now_mono is None else now_mono
        if self._last_tick_mono is None:
            self._last_tick_mono = now
        dt = max(0.0, now - self._last_tick_mono)
        self._last_tick_mono = max(self._last_tick_mono, now)

        ps_status, cpu_time_s, cpu_pct, rss_mb = self._observe_psutil_locked()

        if self._state == "running" and self._proc is not None:
            self._active_runtime_s += dt
            delta_cpu = max(0.0, cpu_time_s - self._prev_cpu_time_s)
            self._prev_cpu_time_s = cpu_time_s

            if self._use_rapl:
                curr_uj = _read_rapl_uj()
                if curr_uj is not None and self._last_rapl_uj is not None:
                    if curr_uj >= self._last_rapl_uj:
                        delta_uj = curr_uj - self._last_rapl_uj
                    else:
                        delta_uj = (self._rapl_max_uj - self._last_rapl_uj) + curr_uj
                    delta_j = delta_uj / 1_000_000.0
                    self._measured_energy_j += delta_j
                    self._last_power_w = (delta_j / dt) if dt > 1e-6 else self._last_power_w
                    self._last_rapl_uj = curr_uj
                else:
                    self._use_rapl = False
                    self._energy_meter_method = "software_cpu_meter_psutil"

            if not self._use_rapl:
                if dt > 1e-6 and delta_cpu > 0.0:
                    cores_equiv = min(SOFTWARE_CPU_MAX_CORES_CLAMP, delta_cpu / dt)
                elif self._last_power_w > 0.0:
                    cores_equiv = max(
                        0.5,
                        (self._last_power_w - SOFTWARE_CPU_BASE_W) / SOFTWARE_CPU_PER_CORE_W,
                    )
                else:
                    cores_equiv = 1.0
                power_w = SOFTWARE_CPU_BASE_W + SOFTWARE_CPU_PER_CORE_W * cores_equiv
                self._last_power_w = power_w
                self._measured_energy_j += power_w * dt
        elif self._state == "paused":
            self._paused_time_s += dt
            self._last_power_w = 0.0
            self._last_cpu_percent = 0.0
            if self._use_rapl:
                self._last_rapl_uj = _read_rapl_uj()
        else:
            self._last_power_w = 0.0
            self._last_cpu_percent = 0.0

        return ps_status, cpu_time_s, cpu_pct, rss_mb

    def _watch_exit_loop(
        self,
        proc: subprocess.Popen[str],
        stdout_thread: threading.Thread,
        stderr_thread: threading.Thread,
        generation: int,
    ) -> None:
        waitid_fn = getattr(os, "waitid", None)
        if waitid_fn is None:
            return
        try:
            waitid_fn(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT)
        except (ChildProcessError, OSError):
            return
        exit_mono = time.monotonic()
        with self._lock:
            if generation != self._generation or self._proc is not proc:
                return
            if self._exited_mono is None:
                self._exited_mono = exit_mono
            self._sample_telemetry_locked(now_mono=self._exited_mono)

        for t in (stdout_thread, stderr_thread):
            if t.is_alive():
                t.join(timeout=0.5)

        with self._lock:
            if generation != self._generation or self._proc is not proc:
                return
            self._reap_if_exited_locked()

    def _reap_if_exited_locked(self) -> None:
        if self._proc is None:
            return
        rc = self._proc.poll()
        if rc is None:
            return

        # Freeze runtime and energy at true exit timestamp before clearing process handle
        if self._exited_mono is not None:
            stop_mono = self._exited_mono
        elif (
            self._last_unit_done_mono is not None
            and self._units_total is not None
            and self._units_completed >= self._units_total
        ):
            stop_mono = max(self._last_tick_mono or 0.0, self._last_unit_done_mono)
        else:
            stop_mono = time.monotonic()
        self._sample_telemetry_locked(now_mono=stop_mono)
        proc = self._proc
        for t in (self._stdout_thread, self._stderr_thread):
            if t is not None and t.is_alive():
                t.join(timeout=0.5)
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

        self._last_exit_code = rc
        self._proc = None
        self._stdout_thread = None
        self._stderr_thread = None
        self._exit_thread = None
        self._state = "idle"
        self._heat_signal_on = False
        self._last_power_w = 0.0
        self._last_cpu_percent = 0.0
        finished_pid = self._pid
        self._pid = None
        self._pgid = None

        if rc in (0, -signal.SIGTERM):
            self._append_event_locked(
                "complete",
                f"Worker PID {finished_pid} completed cleanly "
                f"(exit_code={rc}, units={self._units_completed})",
            )
        else:
            self._last_error = self._last_error or f"Worker exited with code {rc}"
            self._append_event_locked(
                "error",
                f"Worker PID {finished_pid} exited unexpectedly (exit_code={rc})",
            )

    def _effective_queue_depth_locked(self, queue_depth: int | None) -> int:
        if queue_depth is not None:
            return max(0, int(queue_depth))
        if self._proc is None:
            return 0
        if self._units_total is not None:
            return max(1, self._units_total - self._units_completed)
        return 1

    def _poll_psutil_stopped_locked(
        self, pid: int, expect_stopped: bool, timeout_s: float = 0.25
    ) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                st = psutil.Process(pid).status()
                if expect_stopped and st == psutil.STATUS_STOPPED:
                    return
                if not expect_stopped and st != psutil.STATUS_STOPPED:
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return
            time.sleep(0.005)

    def start(
        self,
        job_type: JobType = "archive_ocr",
        max_units: int | None = None,
        continuous: bool | None = None,
        max_runtime_s: float = DEFAULT_MAX_CONTINUOUS_RUNTIME_S,
    ) -> WorkerStatus:
        """Spawn a new isolated CPU worker subprocess in its own process group."""
        if job_type not in WORKLOAD_CATALOG:
            raise ValueError(f"Unsupported job_type '{job_type}'")

        with self._lock:
            self._reap_if_exited_locked()
            if self._proc is not None:
                self.stop()

            is_continuous = (
                (max_units is None or max_units <= 0) if continuous is None else bool(continuous)
            )
            if not is_continuous and (max_units is None or max_units <= 0):
                max_units = 2
            cli_max_units = 0 if is_continuous else int(max_units or 2)

            self._generation += 1
            gen = self._generation
            cmd = [
                sys.executable,
                "-m",
                "digitalheat.worker",
                "--job-type",
                job_type,
                "--max-units",
                str(cli_max_units),
                "--max-runtime-s",
                str(max_runtime_s),
            ]
            proc = subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            self._proc = proc
            self._pid = proc.pid
            try:
                self._pgid = os.getpgid(proc.pid)
            except OSError:
                self._pgid = proc.pid

            self._state = "running"
            self._job_type = job_type
            self._units_completed = 0
            self._units_total = None if is_continuous else cli_max_units
            self._continuous = is_continuous

            now = time.monotonic()
            self._started_mono = now
            self._last_tick_mono = now
            self._exited_mono = None
            self._last_unit_done_mono = None
            self._active_runtime_s = 0.0
            self._paused_time_s = 0.0
            self._cpu_time_hwm_s = 0.0
            self._prev_cpu_time_s = 0.0
            self._last_cpu_percent = 0.0
            self._last_rss_mb = 0.0
            self._last_power_w = SOFTWARE_CPU_BASE_W + SOFTWARE_CPU_PER_CORE_W
            self._measured_energy_j = 0.0
            self._heat_signal_on = True
            self._last_unit_output = None
            self._last_exit_code = None
            self._last_error = None
            if self._use_rapl:
                self._last_rapl_uj = _read_rapl_uj()

            try:
                psutil.Process(proc.pid).cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            self._stdout_thread = threading.Thread(
                target=self._read_stdout_loop, args=(proc, gen), daemon=True
            )
            self._stderr_thread = threading.Thread(
                target=self._read_stderr_loop, args=(proc, gen), daemon=True
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            self._exit_thread = threading.Thread(
                target=self._watch_exit_loop,
                args=(proc, self._stdout_thread, self._stderr_thread, gen),
                daemon=True,
            )
            self._exit_thread.start()

            self._append_event_locked(
                "start",
                f"Started {job_type} worker PID {proc.pid} "
                f"(pgid={self._pgid}, continuous={is_continuous})",
            )
            return self.get_status()

    def apply_heat_signal(
        self,
        tank_soc_kwh: float,
        heat_demand_kw: float,
        outdoor_temp_c: float = 10.0,
        queue_depth: int | None = None,
        action_override: Literal[
            "heat_signal_pause",
            "heat_signal_resume",
            "manual_pause",
            "manual_resume",
        ]
        | None = None,
    ) -> WorkerStatus:
        """Apply ReferenceScheduler.decide_live_step and signal SIGSTOP/SIGCONT to group."""
        with self._lock:
            self._reap_if_exited_locked()
            self._sample_telemetry_locked()

            eff_q = self._effective_queue_depth_locked(queue_depth)
            signal_on = self.scheduler.decide_live_step(
                tank_soc_kwh=tank_soc_kwh,
                heat_demand_kw=heat_demand_kw,
                outdoor_temp_c=outdoor_temp_c,
                queue_depth=eff_q,
            )
            self._last_tank_soc_kwh = tank_soc_kwh
            self._last_heat_demand_kw = heat_demand_kw
            self._last_outdoor_temp_c = outdoor_temp_c
            self._heat_signal_on = signal_on

            if self._proc is not None and self._pid is not None:
                pgid = self._pgid if self._pgid is not None else self._pid
                if not signal_on and self._state == "running":
                    try:
                        os.killpg(pgid, signal.SIGSTOP)
                        self._poll_psutil_stopped_locked(self._pid, expect_stopped=True)
                    except ProcessLookupError:
                        self._reap_if_exited_locked()
                        return self.get_status()
                    self._state = "paused"
                    self._last_power_w = 0.0
                    self._last_cpu_percent = 0.0
                    self._last_tick_mono = time.monotonic()
                    self._append_event_locked(
                        action_override or "heat_signal_pause",
                        f"Sent SIGSTOP to pgid={pgid} "
                        f"(SOC={tank_soc_kwh:.2f} kWh, demand={heat_demand_kw:.2f} kW)",
                        tank_soc_kwh=tank_soc_kwh,
                        heat_demand_kw=heat_demand_kw,
                        outdoor_temp_c=outdoor_temp_c,
                    )
                elif signal_on and self._state == "paused":
                    try:
                        os.killpg(pgid, signal.SIGCONT)
                        self._poll_psutil_stopped_locked(self._pid, expect_stopped=False)
                    except ProcessLookupError:
                        self._reap_if_exited_locked()
                        return self.get_status()
                    self._state = "running"
                    self._last_tick_mono = time.monotonic()
                    if self._use_rapl:
                        self._last_rapl_uj = _read_rapl_uj()
                    self._append_event_locked(
                        action_override or "heat_signal_resume",
                        f"Sent SIGCONT to pgid={pgid} "
                        f"(SOC={tank_soc_kwh:.2f} kWh, demand={heat_demand_kw:.2f} kW)",
                        tank_soc_kwh=tank_soc_kwh,
                        heat_demand_kw=heat_demand_kw,
                        outdoor_temp_c=outdoor_temp_c,
                    )

            return self.get_status()

    def pause(self) -> WorkerStatus:
        """Pause the active CPU worker via the unified scheduler heat signal (full tank)."""
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None
            return self.apply_heat_signal(
                tank_soc_kwh=TANK_CAPACITY_KWH,
                heat_demand_kw=0.0,
                outdoor_temp_c=25.0,
                queue_depth=1 if alive else 0,
                action_override="manual_pause",
            )

    def resume(self) -> WorkerStatus:
        """Resume a paused CPU worker via the unified scheduler heat signal (cold demand)."""
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None
            return self.apply_heat_signal(
                tank_soc_kwh=0.0,
                heat_demand_kw=SPACE_HEATING_PEAK_KW,
                outdoor_temp_c=-5.6,
                queue_depth=1 if alive else 0,
                action_override="manual_resume",
            )

    def stop(self) -> WorkerStatus:
        """Terminate the live worker process group cleanly (SIGCONT before SIGTERM/SIGKILL)."""
        with self._lock:
            self._reap_if_exited_locked()
            if self._proc is not None:
                self._sample_telemetry_locked()
                proc = self._proc
                pid = self._pid
                pgid = self._pgid if self._pgid is not None else proc.pid

                for sig in (signal.SIGCONT, signal.SIGTERM):
                    try:
                        os.killpg(pgid, sig)
                    except (ProcessLookupError, OSError):
                        pass

                try:
                    rc = proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    rc = proc.wait(timeout=1.0)

                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass

                for t in (self._stdout_thread, self._stderr_thread):
                    if t is not None and t.is_alive():
                        t.join(timeout=1.0)

                self._last_exit_code = rc
                self._proc = None
                self._generation += 1
                self._stdout_thread = None
                self._stderr_thread = None
                self._exit_thread = None
                self._state = "idle"
                self._heat_signal_on = False
                self._last_power_w = 0.0
                self._last_cpu_percent = 0.0
                self._pid = None
                self._pgid = None
                self._append_event_locked(
                    "stop",
                    f"Stopped worker PID {pid} (pgid={pgid}, exit_code={rc})",
                )
            return self.get_status()

    def shutdown(self) -> None:
        """Release any active subprocess on server or interpreter exit."""
        self.stop()

    def get_status(self) -> WorkerStatus:
        """Return current worker status, OS telemetry and 1 kW reference server scaling."""
        with self._lock:
            self._reap_if_exited_locked()
            ps_status, cpu_time_s, cpu_pct, rss_mb = self._sample_telemetry_locked()

            wall_elapsed_s = 0.0
            if self._started_mono is not None and (
                self._proc is not None or self._active_runtime_s > 0.0 or self._paused_time_s > 0.0
            ):
                if self._proc is not None:
                    wall_elapsed_s = max(0.0, time.monotonic() - self._started_mono)
                else:
                    wall_elapsed_s = self._active_runtime_s + self._paused_time_s

            is_running = self._state == "running"
            ref_kw = SERVER_ELECTRICAL_POWER_KW if is_running else 0.0
            rec_kw = SERVER_HEAT_OUTPUT_KW if is_running else 0.0
            unrec_kw = (
                (SERVER_ELECTRICAL_POWER_KW * UNRECOVERED_HEAT_FRACTION) if is_running else 0.0
            )

            e_per_unit: float | None = None
            scaled_rate: float | None = None
            if self._units_completed >= 1 and self._measured_energy_j > 0.0:
                e_per_unit = round(self._measured_energy_j / self._units_completed, 4)
                scaled_rate = round(JOULES_PER_KWH / e_per_unit, 2)

            spec = WORKLOAD_CATALOG.get(self._job_type) if self._job_type else None
            scaling_formula = (
                spec.scaling_formula
                if spec is not None
                else "3,600,000 J / measured local CPU energy per unit"
            )

            return WorkerStatus(
                pid=self._pid,
                pgid=self._pgid,
                state=self._state,
                psutil_status=ps_status,
                job_type=self._job_type,
                job_label=spec.label if spec is not None else None,
                units_completed=self._units_completed,
                units_total=self._units_total,
                continuous=self._continuous,
                queue_depth=self._effective_queue_depth_locked(None),
                active_runtime_s=round(self._active_runtime_s, 4),
                paused_time_s=round(self._paused_time_s, 4),
                wall_elapsed_s=round(wall_elapsed_s, 4),
                cpu_time_s=round(cpu_time_s, 4),
                cpu_percent=round(cpu_pct, 2),
                rss_mb=round(rss_mb, 3),
                estimated_cpu_power_w=round(self._last_power_w, 2),
                measured_energy_j=round(self._measured_energy_j, 4),
                energy_meter_method=self._energy_meter_method,
                heat_signal_on=self._heat_signal_on,
                last_tank_soc_kwh=self._last_tank_soc_kwh,
                last_heat_demand_kw=self._last_heat_demand_kw,
                last_outdoor_temp_c=self._last_outdoor_temp_c,
                scaled_1kw=Scaled1kWEquivalent(
                    reference_server_power_kw=ref_kw,
                    heat_recovered_kw=rec_kw,
                    basement_unrecovered_kw=unrec_kw,
                    measured_energy_per_unit_j=e_per_unit,
                    scaled_units_per_server_hour=scaled_rate,
                    scaling_formula=scaling_formula,
                ),
                arrival_rate_source=ARRIVAL_RATE_SOURCE,
                last_unit_output=self._last_unit_output,
                last_exit_code=self._last_exit_code,
                last_error=self._last_error,
                history=list(self._history),
            )

    def status(self) -> WorkerStatus:
        """Alias for get_status()."""
        return self.get_status()


_DEFAULT_MANAGER_LOCK = threading.Lock()
_DEFAULT_MANAGER: WorkerManager | None = None


def get_worker_manager() -> WorkerManager:
    """Return the process-wide singleton WorkerManager."""
    global _DEFAULT_MANAGER
    with _DEFAULT_MANAGER_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = WorkerManager()
        return _DEFAULT_MANAGER


def shutdown_default_worker_manager() -> None:
    """Ensure the default WorkerManager terminates any live worker on exit."""
    with _DEFAULT_MANAGER_LOCK:
        if _DEFAULT_MANAGER is not None:
            _DEFAULT_MANAGER.shutdown()


atexit.register(shutdown_default_worker_manager)


def _subprocess_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CalorGrid isolated CPU worker subprocess")
    parser.add_argument(
        "--job-type",
        required=True,
        choices=("archive_ocr", "iiif_embeddings", "audio_transcription"),
    )
    parser.add_argument("--max-units", type=int, default=0)
    parser.add_argument("--max-runtime-s", type=float, default=DEFAULT_MAX_CONTINUOUS_RUNTIME_S)
    args = parser.parse_args(argv)

    stop_requested = False

    def _handle_term(signum: int, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    t_start = time.monotonic()
    unit_index = 0
    while not stop_requested:
        if args.max_units > 0 and unit_index >= args.max_units:
            break
        if args.max_units == 0 and (time.monotonic() - t_start) >= args.max_runtime_s:
            break
        t0 = time.monotonic()
        result = run_single_unit(args.job_type, unit_index)
        t1 = time.monotonic()
        payload = {
            "unit_index": unit_index,
            "unit_wall_s": round(t1 - t0, 6),
            "result": result,
        }
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()
        unit_index += 1

    return 0


if __name__ == "__main__":
    raise SystemExit(_subprocess_main())
