"""Real TLS/proxy acceptance against an isolated Mock backend; no HPC access."""

import hashlib
import json
import os
import secrets
import socket
import sqlite3
import ssl
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
CADDY = os.environ.get("PORTAL_TEST_CADDY")
pytestmark = pytest.mark.skipif(not CADDY, reason="Set PORTAL_TEST_CADDY to run TLS gateway tests")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def process(command, env, log):
    with log.open("wb") as output:
        child = subprocess.Popen(command, env=env, cwd=ROOT, stdout=output, stderr=output)
        try:
            yield child
        finally:
            child.terminate()
            try:
                child.wait(timeout=7)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)


def wait_ready(child, url, client):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        assert child.poll() is None, "test server exited"
        try:
            response = client.get(url, timeout=0.5)
            if response.status_code in (200, 403):
                return response
        except (httpx.TransportError, OSError):
            pass
        time.sleep(0.05)
    pytest.fail("test server did not become ready")


def test_campus_https_sessions_websocket_streams_and_network_acl(tmp_path):
    api_port, tls_port, denied_port = free_port(), free_port(), free_port()
    origin = f"https://127.0.0.1:{tls_port}"
    password = secrets.token_urlsafe(24)
    env = {
        **os.environ,
        "PORTAL_E2E_API_PORT": str(api_port),
        "PORTAL_E2E_ORIGIN": origin,
        "PORTAL_E2E_SECURE": "true",
        "PORTAL_E2E_PASSWORD": password,
        "TMPDIR": str(tmp_path),
        "PORTAL_CAMPUS_IP": "127.0.0.1",
        "PORTAL_CAMPUS_PORT": str(tls_port),
        "PORTAL_CAMPUS_CIDR": "127.0.0.0/8",
        "PORTAL_CADDY_DATA": str(tmp_path / "caddy"),
        "PORTAL_FRONTEND_DIST": str(ROOT / "frontend/dist"),
        "PORTAL_API_UPSTREAM": f"127.0.0.1:{api_port}",
    }
    backend = ROOT / "backend/.venv/bin/python"
    with (
        process(
            [str(backend), str(ROOT / "backend/tests/e2e_server.py")], env, tmp_path / "backend.log"
        ) as api,
        httpx.Client(trust_env=False) as plain,
    ):
        wait_ready(api, f"http://127.0.0.1:{api_port}/api/health", plain)
        command = [
            CADDY,
            "run",
            "--config",
            str(ROOT / "deploy/Caddyfile.campus"),
            "--adapter",
            "caddyfile",
        ]
        with process(command, env, tmp_path / "gateway.log") as gateway:
            ca = tmp_path / "caddy/pki/authorities/local/root.crt"
            deadline = time.monotonic() + 10
            while not ca.is_file() and time.monotonic() < deadline:
                assert gateway.poll() is None
                time.sleep(0.05)
            assert ca.is_file()
            context = ssl.create_default_context(cafile=str(ca))
            # Real certificate validation: no insecure TLS or browser warning bypass.
            with httpx.Client(verify=context, trust_env=False, timeout=5) as admin:
                assert wait_ready(gateway, origin + "/api/health", admin).status_code == 200
                index = admin.get(origin + "/")
                assert "Tongji HPC Portal" in index.text and "/assets/" in index.text
                assert "/@vite/client" not in index.text
                assert admin.get(origin + "/api/auth/me").status_code == 401
                assert (
                    admin.post(
                        origin + "/api/auth/login",
                        headers={"Origin": "https://untrusted.test"},
                        json={"username": "admin", "password": password},
                    ).status_code
                    == 403
                )
                response = admin.post(
                    origin + "/api/auth/login",
                    headers={"Origin": origin, "X-Forwarded-For": "198.51.100.30"},
                    json={"username": "admin", "password": password},
                )
                assert response.status_code == 200
                cookie = response.headers["set-cookie"].lower()
                assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
                headers = {"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]}
                payload = {
                    "alias": "official_a",
                    "ssh_host": "mock-a",
                    "ssh_port": 22,
                    "ssh_username": "research_a",
                    "ssh_key_reference": "mock-key",
                    "enabled": True,
                }
                assert (
                    admin.post(
                        origin + "/api/admin/official-accounts",
                        headers={"Origin": origin},
                        json=payload,
                    ).status_code
                    == 403
                )
                response = admin.post(
                    origin + "/api/admin/official-accounts", headers=headers, json=payload
                )
                assert response.status_code == 201, response.text
                account_id = response.json()["id"]
                response = admin.post(
                    origin + "/api/admin/users",
                    headers=headers,
                    json={
                        "username": "alice",
                        "password": password,
                        "official_account_id": account_id,
                    },
                )
                assert response.status_code == 201, response.text
                home = response.json()["remote_home"]
                # Caddy replaces an untrusted client's forwarded-IP header.
                database = next(tmp_path.glob("portal-e2e-*/portal.sqlite3"))
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
                    assert (
                        db.execute("select client_ip from login_sessions limit 1").fetchone()[0]
                        == "127.0.0.1"
                    )

            with httpx.Client(verify=context, trust_env=False, timeout=5) as member:
                response = member.post(
                    origin + "/api/auth/login",
                    headers={"Origin": origin},
                    json={"username": "alice", "password": password},
                )
                assert response.status_code == 200
                csrf = response.json()["csrf_token"]
                member_headers = {"Origin": origin, "X-CSRF-Token": csrf}
                assert member.get(origin + "/api/admin/users").status_code == 403
                assert member.get(origin + "/api/files?path=").status_code == 200
                chunks = [secrets.token_bytes(65536) for _ in range(16)]
                response = member.put(
                    origin + "/api/files/upload?path=roundtrip.bin",
                    headers=member_headers,
                    content=iter(chunks),
                )
                assert response.status_code == 201, response.text
                digest = hashlib.sha256()
                with member.stream(
                    "GET", origin + "/api/files/download?path=roundtrip.bin"
                ) as download:
                    assert download.status_code == 200
                    for chunk in download.iter_bytes(65536):
                        digest.update(chunk)
                assert digest.digest() == hashlib.sha256(b"".join(chunks)).digest()
                cookie = "; ".join(f"{k}={v}" for k, v in member.cookies.items())
                with connect(
                    origin.replace("https:", "wss:") + "/ws/terminal",
                    ssl=context,
                    origin=origin,
                    additional_headers={"Cookie": cookie},
                    proxy=None,
                ) as ws:
                    ws.send(json.dumps({"type": "auth", "csrf_token": csrf}))
                    assert json.loads(ws.recv(timeout=5))["type"] == "connected"
                    ws.send(json.dumps({"type": "input", "data": "pwd\r"}))
                    output = ""
                    for _ in range(30):
                        message = json.loads(ws.recv(timeout=5))
                        if message["type"] == "output":
                            output += message["data"]
                            ws.send(json.dumps({"type": "ack", "seq": message["seq"]}))
                            if home in output:
                                break
                    assert home in output

            # A forged forwarding header must not bypass the socket-source ACL.
            denied_env = {
                **env,
                "PORTAL_CAMPUS_PORT": str(denied_port),
                "PORTAL_CAMPUS_CIDR": "198.51.100.0/24",
            }
            with process(command, denied_env, tmp_path / "denied.log") as denied:
                with httpx.Client(verify=context, trust_env=False) as client:
                    response = wait_ready(
                        denied, f"https://127.0.0.1:{denied_port}/api/health", client
                    )
                    assert response.status_code == 403
                    assert (
                        client.get(
                            f"https://127.0.0.1:{denied_port}/",
                            headers={"X-Forwarded-For": "198.51.100.1"},
                        ).status_code
                        == 403
                    )
