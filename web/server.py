"""Dashboard + live-demo server (stdlib only).   python web/server.py  ->  http://localhost:8050"""
from __future__ import annotations

import json
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from heatshift.config import DERIVED, RESULTS  # noqa: E402
from live.controller import CONTROLLER  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        # never cache the dashboard: code and results change during the hackathon
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=float).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path):
        if not path.exists():
            return self._json({"error": f"{path.name} missing - run `make all`"}, 404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/results"):
            return self._file(RESULTS / "results.json")
        if self.path.startswith("/api/measured"):
            return self._file(DERIVED / "measured_jobs.json")
        if self.path.startswith("/api/mlperf"):
            return self._file(DERIVED / "mlperf_reference_server.json")
        if self.path.startswith("/api/profile-check"):
            return self._file(DERIVED / "heat_profile_crosscheck.json")
        if self.path.startswith("/api/live/state"):
            return self._json(CONTROLLER.snapshot())
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/live/start":
            CONTROLLER.start(day=body["day"], job=body.get("job", "image_tag"),
                             sec_per_hour=float(body.get("sec_per_hour", 4)),
                             servers=int(body.get("servers", 8)))
            return self._json({"ok": True})
        if self.path == "/api/live/stop":
            CONTROLLER.stop()
            return self._json({"ok": True})
        if self.path == "/api/live/override":
            CONTROLLER.set_override(**body)
            return self._json({"ok": True})
        if self.path == "/api/live/migrate":
            CONTROLLER.migrate(body.get("target", "davos_hotel"))
            return self._json({"ok": True})
        return self._json({"error": "unknown endpoint"}, 404)


def main(port: int = 8050):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"HeatShift dashboard: http://localhost:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        CONTROLLER.stop()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8050)
