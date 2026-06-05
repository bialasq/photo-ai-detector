"""Task 1.2.5 — sidecar binds loopback only (127.0.0.1 / ::1)."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import main

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "::1"],
)
def test_assert_loopback_bind_host_accepts_loopback(host: str) -> None:
    resolved = main.assert_loopback_bind_host(host)
    assert resolved in {"127.0.0.1", "::1"}


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.1", "8.8.8.8"],
)
def test_assert_loopback_bind_host_rejects_non_loopback(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        main.assert_loopback_bind_host(host)


def test_assert_loopback_bind_host_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        main.assert_loopback_bind_host("")


def _wait_for_health(port: int, *, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.25)
    raise TimeoutError(f"Sidecar did not become healthy on {url}")


def _listening_addresses_for_port(port: int) -> list[str]:
    result = subprocess.run(
        ["netstat", "-an"],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )
    listeners: list[str] = []
    port_suffix = f":{port}"
    for line in result.stdout.splitlines():
        if "LISTENING" not in line or port_suffix not in line:
            continue
        match = re.match(r"\s*TCP\s+(\S+)\s+\S+\s+LISTENING", line, flags=re.IGNORECASE)
        if match:
            listeners.append(match.group(1))
    return listeners


def test_uvicorn_subprocess_binds_loopback_only(tmp_path: Path) -> None:
    port = _pick_free_port()
    env = {
        **os.environ,
        "PHOTO_ORGANIZER_HOST": "127.0.0.1",
        "PHOTO_ORGANIZER_PORT": str(port),
        "PHOTO_ORGANIZER_DB_PATH": str(tmp_path / "bind-test.db"),
        "PHOTO_ORGANIZER_DEV": "0",
    }
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(port)
        listeners = _listening_addresses_for_port(port)
        assert listeners, f"Expected LISTENING socket on port {port}, netstat had none"
        for address in listeners:
            assert address.startswith("127.0.0.1:") or address.startswith("[::1]:"), (
                f"Non-loopback bind detected: {address!r}"
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
