from __future__ import annotations

from tests.conftest import TOKEN, submit


def test_websocket_streams_task_updates_logs_and_files(client):
    with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "hello"
        tid = submit(client, {"steps": 3, "delay_ms": 20}, markets=["Houston"]).json()["task"]["id"]
        seen_status, seen_logs, seen_file = [], [], None
        while True:
            ev = ws.receive_json()
            if ev["type"] == "task.updated" and ev["task"]["id"] == tid:
                seen_status.append(ev["task"]["status"])
                if ev["task"]["status"] == "Completed":
                    break
            elif ev["type"] == "task.logs" and ev["task_id"] == tid:
                seen_logs += [l["message"] for l in ev["lines"]]
            elif ev["type"] == "file.created":
                seen_file = ev["file"]
    assert seen_status[0] == "Queued" and "Running" in seen_status and seen_status[-1] == "Completed"
    assert any("step 3/3" in m for m in seen_logs)
    assert seen_file and seen_file["task_id"] == tid and seen_file["markets"] == ["Houston"]
