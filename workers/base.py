"""Checkpointable batch worker framework.

A worker processes items in batches. After every batch it atomically writes
  <state_dir>/checkpoint.json  {"next": i, "done": n, "server": ..., "history": [...]}
and appends results to <state_dir>/out_XXXXX.npy shards. Therefore it can be
  * paused instantly   (SIGSTOP / SIGCONT from the controller - process frozen, RAM kept)
  * preempted          (SIGTERM -> finishes current batch, writes checkpoint, exits 0)
  * resumed anywhere   (start again with the same state_dir, even with another --server id:
                        this is exactly what a migration to another house does)
Progress is also streamed as JSON lines on stdout for the controller / dashboard.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import numpy as np


class Worker:
    name = "base"
    batch_size = 32

    def __init__(self, state_dir: Path, server: str, limit: int | None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.server = server
        self.limit = limit
        self._stop = False
        signal.signal(signal.SIGTERM, self._on_term)

    # --- to implement -----------------------------------------------------
    def load_items(self) -> list: ...
    def setup(self) -> None: ...
    def process(self, batch: list) -> np.ndarray: ...

    # --- framework --------------------------------------------------------
    def _on_term(self, *_):
        self._stop = True          # finish the current batch, checkpoint, exit

    @property
    def ckpt_path(self) -> Path:
        return self.state_dir / "checkpoint.json"

    def load_ckpt(self) -> dict:
        if self.ckpt_path.exists():
            return json.loads(self.ckpt_path.read_text())
        return {"next": 0, "done": 0, "history": [], "seconds": 0.0}

    def save_ckpt(self, ck: dict) -> None:
        tmp = self.ckpt_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(ck))
        os.replace(tmp, self.ckpt_path)            # atomic

    def emit(self, **kw):
        print(json.dumps({"worker": self.name, "server": self.server, "t": time.time(), **kw}), flush=True)

    def run(self) -> int:
        items = self.load_items()
        total = len(items) if self.limit is None else min(self.limit, len(items))
        ck = self.load_ckpt()
        resumed = ck["next"] > 0
        ck["history"].append({"event": "resume" if resumed else "start", "server": self.server,
                              "at_item": ck["next"], "t": time.time()})
        self.save_ckpt(ck)
        self.emit(event="loading_model", at=ck["next"], total=total, resumed=resumed)
        self.setup()
        self.emit(event="running", at=ck["next"], total=total, resumed=resumed)
        i = ck["next"]
        while i < total and not self._stop:
            t0 = time.time()
            batch = items[i: min(i + self.batch_size, total)]
            out = self.process(batch)
            np.save(self.state_dir / f"out_{i:06d}.npy", out)
            i += len(batch)
            dt = time.time() - t0
            ck.update(next=i, done=i, seconds=ck.get("seconds", 0.0) + dt, server=self.server)
            self.save_ckpt(ck)
            self.emit(event="progress", at=i, total=total, batch_s=round(dt, 3),
                      items_per_s=round(len(batch) / dt, 2), sample=self.describe(batch, out))
        ev = "checkpointed" if i < total else "finished"
        ck["history"].append({"event": ev, "server": self.server, "at_item": i, "t": time.time()})
        self.save_ckpt(ck)
        self.emit(event=ev, at=i, total=total)
        return 0

    def describe(self, batch, out) -> str:
        return ""


def main(worker_cls):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, help="checkpoint / output directory (shared between servers)")
    ap.add_argument("--server", default="basel-ref-1", help="id of the server executing this run")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    sys.exit(worker_cls(Path(a.state), a.server, a.limit).run())
