"""Opaque-box E2E test harness: live uvicorn server, HTTP client, CLI runner and Chrome WebGL."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = REPO_ROOT / "data"
ASSETS_DIR: Path = REPO_ROOT / "assets"
GLB_DIR: Path = ASSETS_DIR / "glb"
SRC_DIR: Path = REPO_ROOT / "src"
WEB_DIR: Path = REPO_ROOT / "web"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def clean_subprocess_env() -> dict[str, str]:
    """Return process environment without host PYTHONPATH overrides."""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def wait_until(
    predicate: Callable[[], Any],
    timeout_s: float = 15.0,
    interval_s: float = 0.15,
    description: str = "condition",
) -> Any:
    """Poll predicate until truthy or raise AssertionError on timeout."""
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(interval_s)
    msg = f"Timed out after {timeout_s:.1f}s waiting for {description}"
    if last_exc is not None:
        msg = f"{msg} (last error: {last_exc})"
    raise AssertionError(msg)


@dataclass
class BrowserSessionErrors:
    """Captured browser console errors, page errors and failed network responses."""

    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    http_failures: list[str] = field(default_factory=list)

    def assert_clean(self) -> None:
        assert not self.page_errors, f"Uncaught page errors: {self.page_errors}"
        assert not self.console_errors, f"Console errors: {self.console_errors}"
        assert not self.http_failures, f"HTTP >=400 failures: {self.http_failures}"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def live_server_url() -> Generator[str, None, None]:
    """Start uvicorn digitalheat.api:app on an ephemeral localhost port."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "digitalheat.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=clean_subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30.0
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stdout_txt, stderr_txt = proc.communicate(timeout=2.0)
                raise RuntimeError(
                    f"uvicorn exited early ({proc.returncode}): {stderr_txt or stdout_txt}"
                )
            try:
                resp = httpx.get(f"{base_url}/api/v1/health", timeout=1.5)
                if resp.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        if not ready:
            raise RuntimeError(f"uvicorn did not become healthy at {base_url} within 30s")
        yield base_url
    finally:
        try:
            httpx.post(f"{base_url}/api/v1/worker/stop", timeout=3.0)
        except httpx.HTTPError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3.0)
        if proc.stdout:
            proc.stdout.close()
        if proc.stderr:
            proc.stderr.close()


@pytest.fixture(scope="session")
def base_url(live_server_url: str) -> str:
    return live_server_url


@pytest.fixture()
def api_client(live_server_url: str) -> Generator[httpx.Client, None, None]:
    """Per-test httpx client bound to the live server."""
    with httpx.Client(base_url=live_server_url, timeout=30.0) as client:
        yield client


@pytest.fixture()
def worker_guard(api_client: httpx.Client) -> Generator[httpx.Client, None, None]:
    """Ensure any live worker spawned during a test is stopped on teardown."""
    try:
        yield api_client
    finally:
        try:
            api_client.post("/api/v1/worker/stop", timeout=5.0)
        except httpx.HTTPError:
            pass


@pytest.fixture(scope="session")
def browser() -> Generator[Any, None, None]:
    """Session-scoped headless Chrome browser with WebGL enabled."""
    from playwright.sync_api import sync_playwright

    channel = os.environ.get("PW_CHANNEL", "chrome").strip()
    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "args": [
            "--enable-webgl",
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
            "--ignore-gpu-blocklist",
            "--no-sandbox",
        ],
    }
    if channel:
        launch_kwargs["channel"] = channel

    with sync_playwright() as pw:
        browser_inst = pw.chromium.launch(**launch_kwargs)
        try:
            yield browser_inst
        finally:
            browser_inst.close()


@pytest.fixture()
def cockpit_page(
    browser: Any,
    live_server_url: str,
) -> Generator[tuple[Any, BrowserSessionErrors], None, None]:
    """Open the cockpit page in a clean context and record console, page and HTTP errors."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = BrowserSessionErrors()

    def _on_console(msg: Any) -> None:
        if msg.type == "error":
            errors.console_errors.append(msg.text)

    def _on_pageerror(err: Any) -> None:
        errors.page_errors.append(str(err))

    def _on_response(resp: Any) -> None:
        if resp.status >= 400 and "/favicon.ico" not in resp.url:
            errors.http_failures.append(f"{resp.status} {resp.url}")

    page.on("console", _on_console)
    page.on("pageerror", _on_pageerror)
    page.on("response", _on_response)

    page.goto(live_server_url, wait_until="domcontentloaded", timeout=30_000)
    page.locator("[data-testid='kpi-scorecard']").wait_for(state="visible", timeout=15_000)
    wait_until(
        lambda: page.locator("[data-testid='kpi-servers-per-house']").inner_text().strip() != "",
        timeout_s=15.0,
        description="initial KPI scorecard hydration",
    )

    try:
        yield page, errors
    finally:
        try:
            httpx.post(f"{live_server_url}/api/v1/worker/stop", timeout=3.0)
        except httpx.HTTPError:
            pass
        context.close()


@pytest.fixture(scope="session")
def run_cli() -> Callable[[list[str]], subprocess.CompletedProcess[str]]:
    """Run digitalheat CLI command via python -m digitalheat.cli."""

    def _runner(args: list[str], timeout_s: float = 30.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "digitalheat.cli", *args],
            cwd=str(REPO_ROOT),
            env=clean_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )

    return _runner


@pytest.fixture(scope="session")
def poll_until() -> Callable[..., Any]:
    """Return polling helper for waiting on async worker or UI updates."""
    return wait_until

