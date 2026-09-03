"""gui/server 测试:API 契约/SSE 序列/单锁/Host 校验/ask_yes 传递/错误三段式。

基于 2026-09-04-gui-v0.6 计划 Task 6 Step 1 的测试契约,按 brief「实现者注意」修正:
- `_make_game` 中残留的 `.json` 占位行已删除;
- 兼容模式下 `resolve_workdir(game_root=...)` 不落盘 game_root(Task 4 已知遗留),
  而 server 以 workdir config 为唯一事实来源,故每个测试先 `save_game_root` 持久化。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from migration.gui.server import create_app


def _make_game(tmp: Path) -> Path:
    """构建最小游戏根:versions/{src,dst} 各带一份不同的 options.txt。"""
    game = tmp / "game"
    for name, text in (("src", "fps:120\n"), ("dst", "fps:60\n")):
        d = game / "versions" / name
        d.mkdir(parents=True)
        (d / "options.txt").write_text(text, encoding="utf-8")
    return game


def _make_client(tmp_path: Path, monkeypatch, game: Path | None = None) -> tuple[Path, TestClient]:
    """构建注入临时 workdir 的 TestClient(game_root 已持久化到 config)。"""
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    game = game if game is not None else _make_game(tmp_path)
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    return game, TestClient(create_app(workdir=w))


def _wait_job_done(client: TestClient, job_id: str) -> list[dict]:
    """订阅 SSE 直到 done/error 事件(或连接关闭),返回已收集的事件列表。"""
    with client.stream("GET", f"/api/jobs/{job_id}/events") as resp:
        events: list[dict] = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            if events and events[-1].get("type") in ("done", "error"):
                break
        return events


def test_versions_endpoint(tmp_path, monkeypatch):
    game, client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/api/versions")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["versions"]) >= {"src", "dst"}
    # 无 PCL.ini 时 active 为 None(有则读 Version: 行)
    assert body["active"] is None
    (game / "PCL.ini").write_text("Version:src\n", encoding="utf-8")
    assert client.get("/api/versions").json()["active"] == "src"


def test_index_placeholder(tmp_path, monkeypatch):
    _game, client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "mcmig" in resp.text
    assert "text/html" in resp.headers["content-type"]


def test_plan_and_migrate_job_flow(tmp_path, monkeypatch):
    game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert r.status_code == 200
    job = r.json()["job_id"]
    events = _wait_job_done(client, job)
    types = [e["type"] for e in events]
    assert "phase" in types and types[-1] == "done"
    # plan 阶段序列:scan_src → scan_dst → plan → done
    assert [e["name"] for e in events if e["type"] == "phase"] == ["scan_src", "scan_dst", "plan"]
    # done 事件携带按 origin 分组的 action 摘要(供②步渲染)
    done = events[-1]
    assert done["job_kind"] == "plan"
    groups = done["plan"]
    assert groups["must_migrate"]["count"] >= 1
    action = groups["must_migrate"]["actions"][0]
    assert {"path", "reason", "behavior", "origin"} <= set(action.keys())
    assert action["path"] == "options.txt"
    assert "title" in groups["must_migrate"]

    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": []})
    events2 = _wait_job_done(client, r2.json()["job_id"])
    assert events2[-1]["type"] == "done"
    assert (game / "versions" / "dst" / "options.txt").read_text(encoding="utf-8") == "fps:120\n"
    # migrate done 事件带 summary(各 status 计数)与 PCL 提醒文案
    done2 = events2[-1]
    assert done2["job_kind"] == "migrate"
    assert done2["summary"]["copied"] >= 1
    assert done2["summary"]["failed"] == 0
    assert isinstance(done2["reminder"], list) and len(done2["reminder"]) >= 3
    assert any("PCL.ini" in line for line in done2["reminder"])
    assert any("Setup.ini" in line for line in done2["reminder"])
    # file 事件:字段齐全(spec §4 schema)
    files = [e for e in events2 if e["type"] == "file"]
    assert files and {"path", "status", "index", "total", "backed_up"} <= set(files[0].keys())
    assert files[-1]["index"] == files[-1]["total"]


def test_single_job_lock(tmp_path, monkeypatch):
    _game, client = _make_client(tmp_path, monkeypatch)
    client.post("/api/plan", json={"src": "src", "dst": "dst"})
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert r.status_code == 409
    assert {"what", "why", "details"} <= set(r.json().keys())


def test_host_header_rejected(tmp_path, monkeypatch):
    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/versions", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    assert {"what", "why", "details"} <= set(r.json().keys())


def test_error_response_three_part(tmp_path, monkeypatch):
    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/plan", json={"src": "ghost", "dst": "dst"})
    assert r.status_code == 422
    body = r.json()
    assert {"what", "why", "details"} <= set(body.keys())
    # 请求体字段缺失同样走三段式
    r2 = client.post("/api/plan", json={"src": "src"})
    assert r2.status_code == 422
    assert {"what", "why", "details"} <= set(r2.json().keys())


def test_migrate_without_plan_file_emits_error_event(tmp_path, monkeypatch):
    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": []})
    assert r.status_code == 200
    events = _wait_job_done(client, r.json()["job_id"])
    assert events[-1]["type"] == "error"
    err = events[-1]
    assert {"what", "why", "details"} <= set(err.keys())
    assert (events[0]["type"] == "phase") and (events[0]["name"] == "migrate")


def test_unknown_job_sse_404(tmp_path, monkeypatch):
    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/jobs/ghost/events")
    assert r.status_code == 404
    assert {"what", "why", "details"} <= set(r.json().keys())
