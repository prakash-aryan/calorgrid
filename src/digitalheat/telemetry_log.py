"""Thread-safe in-memory telemetry and simulation log ring buffer (stdlib-only)."""

from __future__ import annotations

import copy
import threading
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

LogCategory = Literal[
    "simulation_run",
    "analysis_task",
    "scheduler_dispatch",
    "worker_lifecycle",
    "customer_booking",
    "thermal_rebalance",
]

LogLevel = Literal["info", "warning", "error"]

ALL_LOG_CATEGORIES: tuple[str, ...] = (
    "simulation_run",
    "analysis_task",
    "scheduler_dispatch",
    "worker_lifecycle",
    "customer_booking",
    "thermal_rebalance",
)

CATEGORY_SUBCAPS: dict[str, int] = {
    "simulation_run": 90,
    "analysis_task": 110,
    "scheduler_dispatch": 100,
    "worker_lifecycle": 80,
    "customer_booking": 80,
    "thermal_rebalance": 90,
}

CATEGORY_FLOORS: dict[str, int] = {
    "customer_booking": 40,
    "simulation_run": 40,
    "scheduler_dispatch": 40,
    "worker_lifecycle": 35,
    "thermal_rebalance": 35,
    "analysis_task": 35,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class TelemetryLogEntry:
    """Single immutable structured telemetry log entry."""

    seq_id: int
    timestamp_utc: str
    category: LogCategory
    level: LogLevel
    source_module: str
    event_type: str
    summary: str
    site_id: str | None = None
    server_id: str | None = None
    job_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq_id": self.seq_id,
            "timestamp_utc": self.timestamp_utc,
            "category": self.category,
            "level": self.level,
            "source_module": self.source_module,
            "event_type": self.event_type,
            "summary": self.summary,
            "site_id": self.site_id,
            "server_id": self.server_id,
            "job_id": self.job_id,
            "metrics": copy.deepcopy(self.metrics),
        }


class TelemetryLogStore:
    """Bounded thread-safe ring buffer with per-category sub-caps and minimum floors."""

    def __init__(
        self,
        maxlen: int = 500,
        category_subcaps: dict[str, int] | None = None,
        category_floors: dict[str, int] | None = None,
    ) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        self._lock = threading.RLock()
        self._maxlen = maxlen
        self._entries: deque[TelemetryLogEntry] = deque(maxlen=maxlen)
        self._subcaps: dict[str, int] = dict(
            category_subcaps if category_subcaps is not None else CATEGORY_SUBCAPS
        )
        self._floors: dict[str, int] = dict(
            category_floors if category_floors is not None else CATEGORY_FLOORS
        )
        self._next_seq_id: int = 1
        self._category_counts: Counter[str] = Counter()
        self._lifetime_counts: Counter[str] = Counter()
        self._total_logged: int = 0
        self._evicted_by_subcap: int = 0

    @property
    def maxlen(self) -> int:
        return self._maxlen

    def _remove_oldest_of_category_locked(self, target_cat: str) -> bool:
        for entry in self._entries:
            if entry.category == target_cat:
                self._entries.remove(entry)
                self._category_counts[target_cat] = max(0, self._category_counts[target_cat] - 1)
                self._evicted_by_subcap += 1
                return True
        return False

    def append(
        self,
        category: LogCategory,
        event_type: str,
        summary: str,
        *,
        level: LogLevel = "info",
        source_module: str = "",
        site_id: str | None = None,
        server_id: str | None = None,
        job_id: str | None = None,
        metrics: dict[str, Any] | None = None,
        timestamp_utc: str | None = None,
    ) -> dict[str, Any]:
        if category not in ALL_LOG_CATEGORIES:
            raise ValueError(f"Unknown log category '{category}'")
        if level not in ("info", "warning", "error"):
            raise ValueError(f"Invalid log level '{level}'")

        safe_metrics = copy.deepcopy(metrics) if metrics else {}
        ts = timestamp_utc or _utc_now_iso()

        with self._lock:
            subcap = self._subcaps.get(category, self._maxlen)
            if self._category_counts[category] >= subcap and len(self._entries) > 0:
                self._remove_oldest_of_category_locked(category)

            if len(self._entries) >= self._maxlen:
                excess_candidates = [
                    (self._category_counts[c] - self._floors.get(c, 0), c)
                    for c, cnt in self._category_counts.items()
                    if cnt > self._floors.get(c, 0) and cnt > 0
                ]
                if excess_candidates:
                    excess_candidates.sort(key=lambda item: (-item[0], item[1]))
                    victim_cat = excess_candidates[0][1]
                    self._remove_oldest_of_category_locked(victim_cat)
                else:
                    oldest = self._entries.popleft()
                    self._category_counts[oldest.category] = max(
                        0, self._category_counts[oldest.category] - 1
                    )

            entry = TelemetryLogEntry(
                seq_id=self._next_seq_id,
                timestamp_utc=ts,
                category=category,
                level=level,
                source_module=source_module,
                event_type=event_type,
                summary=summary,
                site_id=site_id,
                server_id=server_id,
                job_id=job_id,
                metrics=safe_metrics,
            )
            self._entries.append(entry)
            self._next_seq_id += 1
            self._total_logged += 1
            self._category_counts[category] += 1
            self._lifetime_counts[category] += 1
            return entry.to_dict()

    def query(
        self,
        *,
        category: LogCategory | None = None,
        site_id: str | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
        order: Literal["desc", "asc"] = "desc",
        site_resolver: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        if category is not None and category not in ALL_LOG_CATEGORIES:
            raise ValueError(f"Unknown log category '{category}'")
        clamped_limit = max(1, min(self._maxlen, int(limit)))

        with self._lock:
            entries_snapshot = list(self._entries)
            counts_snapshot = dict(self._category_counts)
            lifetime_snapshot = dict(self._lifetime_counts)
            latest_seq = self._next_seq_id - 1
            total_logged = self._total_logged
            evicted = self._evicted_by_subcap

        oldest_seq = entries_snapshot[0].seq_id if entries_snapshot else None

        resolved_filter_site: str | None = None
        if site_id is not None:
            if site_resolver is not None:
                resolved_filter_site = site_resolver(site_id)
            else:
                resolved_filter_site = site_id

        filtered: list[TelemetryLogEntry] = []
        for entry in entries_snapshot:
            if category is not None and entry.category != category:
                continue
            if after_sequence is not None and entry.seq_id <= after_sequence:
                continue
            if resolved_filter_site is not None:
                entry_site = entry.site_id
                if entry_site is None:
                    continue
                if site_resolver is not None:
                    try:
                        entry_site = site_resolver(entry_site)
                    except ValueError:
                        pass
                if entry_site != resolved_filter_site and entry.site_id != site_id:
                    continue
            filtered.append(entry)

        if order == "desc":
            filtered.reverse()
        sliced = filtered[:clamped_limit]
        items = [e.to_dict() for e in sliced]
        next_after = max((it["seq_id"] for it in items), default=after_sequence or latest_seq)

        return {
            "count": len(items),
            "buffer_capacity": self._maxlen,
            "total_buffered": len(entries_snapshot),
            "total_logged": total_logged,
            "evicted_by_subcap": evicted,
            "oldest_seq_id": oldest_seq,
            "latest_seq_id": latest_seq,
            "next_after_sequence": next_after,
            "truncated": len(filtered) > len(items),
            "category_counts": {cat: counts_snapshot.get(cat, 0) for cat in ALL_LOG_CATEGORIES},
            "lifetime_counts": {cat: lifetime_snapshot.get(cat, 0) for cat in ALL_LOG_CATEGORIES},
            "entries": items,
        }

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._category_counts.clear()


_DEFAULT_STORE: TelemetryLogStore | None = None
_STORE_INIT_LOCK = threading.Lock()


def get_log_store() -> TelemetryLogStore:
    """Return the process-wide thread-safe TelemetryLogStore singleton."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _STORE_INIT_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = TelemetryLogStore()
    return _DEFAULT_STORE


def log_event(
    category: LogCategory,
    event_type: str,
    summary: str,
    *,
    level: LogLevel = "info",
    source_module: str = "",
    site_id: str | None = None,
    server_id: str | None = None,
    job_id: str | None = None,
    metrics: dict[str, Any] | None = None,
    store: TelemetryLogStore | None = None,
) -> dict[str, Any] | None:
    """Exception-safe helper to emit a telemetry event without disrupting callers."""
    try:
        target = store if store is not None else get_log_store()
        return target.append(
            category=category,
            event_type=event_type,
            summary=summary,
            level=level,
            source_module=source_module,
            site_id=site_id,
            server_id=server_id,
            job_id=job_id,
            metrics=metrics,
        )
    except Exception:
        return None
