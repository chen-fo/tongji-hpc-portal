import time

import pytest
from conftest import ORIGIN, create_member, login
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.models.entities import TerminalSession
from app.services.audit import AuditStreamParser, redact
from app.ssh.mock_terminal import event_frame


def receive_until(ws, predicate):
    seen = []
    for _ in range(200):
        event = ws.receive_json()
        seen.append(event)
        if event["type"] == "output":
            ws.send_json({"type": "ack", "seq": event["seq"]})
        if predicate(event, seen):
            return event, seen
    raise AssertionError("Expected terminal event not received")


def connect(student):
    ws = student.websocket_connect("/ws/terminal", headers={"Origin": ORIGIN})
    return ws


@pytest.fixture
def student(admin, account, app):
    create_member(admin, account["id"])
    client = TestClient(app, headers={"Origin": ORIGIN})
    client.portal = admin.portal  # All clients share the application's lifespan/event loop.
    result = login(client, "alice").json()
    client.csrf = result["csrf_token"]
    client.user_id = result["user"]["id"]
    yield client
    client.close()


def test_websocket_origin_session_csrf_and_mapping(client, admin, student):
    with pytest.raises(WebSocketDisconnect):
        with student.websocket_connect("/ws/terminal", headers={"Origin": "https://evil.invalid"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with student.websocket_connect(
            "/ws/terminal?token=not-allowed", headers={"Origin": ORIGIN}
        ):
            pass
    with connect(student) as ws:
        ws.send_json({"type": "auth", "csrf_token": "bad"})
        assert ws.receive_json()["type"] == "error"
    with pytest.raises(WebSocketDisconnect):
        with admin.websocket_connect("/ws/terminal", headers={"Origin": ORIGIN}):
            pass  # Unmapped administrator cannot impersonate a student.


def test_terminal_audit_cwd_resize_control_keys_and_cleanup(student, admin, app):
    with connect(student) as ws:
        ws.send_json({"type": "auth", "csrf_token": student.csrf})
        first, _ = receive_until(ws, lambda e, _: e["type"] == "connected")
        terminal_id = first["session_id"]
        ws.send_json({"type": "resize", "cols": 120, "rows": 40})
        ws.send_json({"type": "input", "data": "pwd\rcd /tmp\rpwd\rcd ~\rpwd\r"})
        receive_until(ws, lambda _, seen: sum(e.get("data", "").count("$ ") for e in seen) >= 6)
        ws.send_json({"type": "input", "data": "echo never-executed\x03"})
        receive_until(ws, lambda e, _: "^C" in e.get("data", ""))
        ws.send_json({"type": "input", "data": "echo TOKEN=super-sensitive-value\r"})
        receive_until(ws, lambda e, _: "$ " in e.get("data", ""))
        ws.send_json({"type": "input", "data": "\x04"})
        with pytest.raises(WebSocketDisconnect):
            while True:
                event = ws.receive_json()
                if event["type"] == "output":
                    ws.send_json({"type": "ack", "seq": event["seq"]})
    # Queue worker runs asynchronously and must not hold the terminal output path.
    for _ in range(100):
        result = admin.get("/api/admin/audit", params={"username": "alice"}).json()
        if result["total"] >= 6:
            break
        time.sleep(0.01)
    commands = list(reversed(result["items"]))
    assert [item["command"] for item in commands[:5]] == ["pwd", "cd /tmp", "pwd", "cd ~", "pwd"]
    assert commands[1]["cwd"].endswith("/user/alice")
    assert commands[2]["cwd"] == "/tmp"
    assert commands[3]["cwd"] == "/tmp"
    assert commands[4]["cwd"].endswith("/user/alice")
    assert "super-sensitive-value" not in str(commands)
    assert commands[-1]["command"] == "[REDACTED: sensitive command]"
    assert student.get("/api/admin/audit").status_code == 403
    assert admin.get("/api/admin/audit", params={"cwd": "/tmp"}).json()["total"] == 2
    with app.state.sessions() as db:
        assert db.get(TerminalSession, terminal_id).ended_at is not None
    assert not app.state.terminals.active


def test_disabling_user_closes_existing_terminal(student, admin, app):
    app.state.settings.terminal_recheck_seconds = 0.05
    with connect(student) as ws:
        ws.send_json({"type": "auth", "csrf_token": student.csrf})
        receive_until(ws, lambda e, _: e["type"] == "connected")
        assert (
            admin.patch(
                f"/api/admin/users/{student.user_id}", json={"status": "disabled"}
            ).status_code
            == 200
        )
        event, _ = receive_until(ws, lambda e, _: e["type"] == "error")
        assert "撤销" in event["message"] or "停用" in event["message"]


def test_audit_failure_never_blocks_terminal(student, app, monkeypatch):
    def broken(*args):
        raise RuntimeError("do not log this sensitive error")

    monkeypatch.setattr(app.state.audit, "record", broken)
    with connect(student) as ws:
        ws.send_json({"type": "auth", "csrf_token": student.csrf})
        receive_until(ws, lambda e, _: e["type"] == "connected")
        ws.send_json({"type": "input", "data": "echo still-works\r"})
        receive_until(ws, lambda e, _: "still-works\r\n$ " in e.get("data", ""))


def test_limit_idle_and_invalid_frame(student, app):
    app.state.settings.terminal_max_per_user = 1
    app.state.settings.terminal_idle_seconds = 1
    with connect(student) as first:
        first.send_json({"type": "auth", "csrf_token": student.csrf})
        receive_until(first, lambda e, _: e["type"] == "connected")
        with connect(student) as second:
            second.send_json({"type": "auth", "csrf_token": student.csrf})
            assert "数量" in second.receive_json()["message"]
        event, _ = receive_until(first, lambda e, _: e["type"] == "error")
        assert "空闲" in event["message"]


def test_split_audit_frames_malformed_and_sensitive_multiline():
    parser = AuditStreamParser("abc")
    frame = event_frame("abc", "command", "/tmp", "echo hello\nprintf world")
    output, events = "", []
    for char in "before" + frame + "after":
        visible, found = parser.feed(char)
        output += visible
        events.extend(found)
    assert output == "beforeafter"
    assert events == [("command", "/tmp", "echo hello\nprintf world")]
    assert parser.feed("\x1b]777;PORTAL;abc;not-base64!\x07") == ("", [])
    assert "secret-value" not in redact(
        'curl -H "Authorization: Bearer secret-value"\nhttps://example.invalid'
    )
    assert redact("python research.py") == "python research.py"
    parser.feed(parser.prefix + "A" * 70000)
    assert parser.feed("A" * 1000 + "\x07ok") == ("ok", [])


def test_two_users_share_official_account_with_independent_terminals(student, admin, account, app):
    create_member(admin, account["id"], "bob")
    bob = TestClient(app, headers={"Origin": ORIGIN})
    bob.portal = admin.portal
    token = login(bob, "bob").json()["csrf_token"]
    try:
        with connect(student) as alice_ws, connect(bob) as bob_ws:
            alice_ws.send_json({"type": "auth", "csrf_token": student.csrf})
            bob_ws.send_json({"type": "auth", "csrf_token": token})
            alice_info, _ = receive_until(alice_ws, lambda e, _: e["type"] == "connected")
            bob_info, _ = receive_until(bob_ws, lambda e, _: e["type"] == "connected")
            assert alice_info["session_id"] != bob_info["session_id"]
            assert alice_info["home"].endswith("/alice") and bob_info["home"].endswith("/bob")
            alice_ws.send_json({"type": "input", "data": "cd /tmp\rpwd\r"})
            bob_ws.send_json({"type": "input", "data": "pwd\r"})
            receive_until(alice_ws, lambda e, _: e.get("data") == "/tmp\r\n$ ")
            receive_until(bob_ws, lambda e, _: e.get("data", "").endswith("/user/bob\r\n$ "))
    finally:
        bob.close()
