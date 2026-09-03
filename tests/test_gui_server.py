"""gui/server 测试:API 契约/SSE 序列/单锁/Host 校验/ask_yes 传递/错误三段式。

基于 2026-09-04-gui-v0.6 计划 Task 6 Step 1 的测试契约,按 brief「实现者注意」修正:
- `_make_game` 中残留的 `.json` 占位行已删除;
- 兼容模式下 `resolve_workdir(game_root=...)` 不落盘 game_root(Task 4 已知遗留),
  而 server 以 workdir config 为唯一事实来源,故每个测试先 `save_game_root` 持久化。
"""

from __future__ import annotations

import json
from pathlib import Path

import migration.gui.server as server_module
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
    """构建注入临时 workdir 的 TestClient(game_root 已持久化到 config)。

    base_url 用 127.0.0.1 直连,Host 白名单无需为 TestClient 留后门(M1)。
    """
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    game = game if game is not None else _make_game(tmp_path)
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    return game, TestClient(create_app(workdir=w), base_url="http://127.0.0.1")


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


def test_index_fallback_logs_error(tmp_path, monkeypatch, caplog):
    """I-2:index.html 读取失败时 GET / 仍 200 占位页,但必须留 error 日志(可观测性)。"""
    def _boom(pkg):
        raise FileNotFoundError(pkg)

    monkeypatch.setattr(server_module.resources, "files", _boom)
    _game, client = _make_client(tmp_path, monkeypatch)
    with caplog.at_level("ERROR", logger="migration.gui.server"):
        resp = client.get("/")
    assert resp.status_code == 200
    assert "mcmig" in resp.text  # 占位页兜底,不 500
    assert "页面资源缺失" in caplog.text


def test_config_get_returns_saved_root(tmp_path, monkeypatch):
    """GET /api/config:返回 config 中持久化的 game_root(步①输入框预填数据源)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json() == {"game_root": str(game)}


def test_config_get_null_when_unconfigured(tmp_path, monkeypatch):
    """GET /api/config:未配置时 game_root 为 null(而非 422,预填需要可空)。"""
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    w = wd.resolve_workdir()  # 兼容模式不依赖 game_root,可未配置起服务
    client = TestClient(create_app(workdir=w), base_url="http://127.0.0.1")
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json() == {"game_root": None}


def test_config_set_saves_and_takes_effect(tmp_path, monkeypatch):
    """POST /api/config:合法路径(存在 + 含 versions/)→ 落盘且 GET/versions 立即反映。"""
    game, client = _make_client(tmp_path, monkeypatch)
    game2 = tmp_path / "game2"
    (game2 / "versions" / "v1").mkdir(parents=True)

    r = client.post("/api/config", json={"game_root": str(game2)})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # game_root() 每次从 config 现读:frozen workdir 未变,新根即时生效
    assert client.get("/api/config").json()["game_root"] == str(game2)
    assert client.get("/api/versions").json()["versions"] == ["v1"]
    # 落盘证据:.mcmig/config.yaml 内容已切换(重跑工具后仍生效)
    import migration.workdir as wd

    assert wd.resolve_workdir().game_root() == game2
    assert game != game2


def test_config_set_rejects_invalid_root(tmp_path, monkeypatch):
    """POST /api/config:路径不存在/缺 versions/ 子目录/空串 → 422 三段式,不落盘。"""
    game, client = _make_client(tmp_path, monkeypatch)

    r1 = client.post("/api/config", json={"game_root": str(tmp_path / "nope")})
    assert r1.status_code == 422
    assert {"what", "why", "details"} <= set(r1.json().keys())

    bare = tmp_path / "bare"
    bare.mkdir()
    r2 = client.post("/api/config", json={"game_root": str(bare)})
    assert r2.status_code == 422
    assert {"what", "why", "details"} <= set(r2.json().keys())

    # 空串被 min_length 拦下,走请求校验三段式(与非法路径同一呈现口径)
    r3 = client.post("/api/config", json={"game_root": ""})
    assert r3.status_code == 422
    assert {"what", "why", "details"} <= set(r3.json().keys())

    # 三次拒绝均未写盘:GET 仍返回原值
    assert client.get("/api/config").json()["game_root"] == str(game)


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
    # summary 五键恒存在(I1:缺席 status 补 0,页面按固定键渲染)
    assert set(done2["summary"]) == {"copied", "identical", "asked_no", "skipped", "failed"}
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
    # 放大 job 最小存活窗口(0.005s→0.5s):第二个请求必然落在第一个
    # job 运行期内,409 结论确定(不依赖机器速度的时序碰运气)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 0.5)
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


def test_green_mode_plan_job_smoke(tmp_path, monkeypatch):
    """绿色模式(frozen)smoke:versions → plan job 全程无 error 事件。

    回归 C1:绿色布局下 snapshots/rules/plans 按 slug 隔离在 data/<slug>/ 内,
    server 传给 build_plan 的 mcmig_dir 必须取 snapshots 父目录(=data/<slug>);
    若误传 workdir.root(=data/),build_plan 会去 data/snapshots/ 找快照,
    plan job 必以「缺少 src 快照」error 收场。
    """
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: True)
    monkeypatch.setattr(wd, "_exe_dir", lambda: tmp_path)
    game = _make_game(tmp_path)
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    client = TestClient(create_app(workdir=w), base_url="http://127.0.0.1")

    resp = client.get("/api/versions")
    assert resp.status_code == 200
    assert set(resp.json()["versions"]) >= {"src", "dst"}

    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert r.status_code == 200
    events = _wait_job_done(client, r.json()["job_id"])
    assert "error" not in [e["type"] for e in events]
    assert events[-1]["type"] == "done"
    groups = events[-1]["plan"]
    assert groups["must_migrate"]["count"] >= 1
    assert {"path", "reason", "behavior", "origin"} <= set(
        groups["must_migrate"]["actions"][0]
    )
    # 快照/plan 均落在 slug 隔离目录(绿色布局落盘位置断言)
    assert (w.snapshots / "src.snapshot.json").exists()
    assert (w.snapshots / "dst.snapshot.json").exists()
    assert (w.plans / "src__dst.plan.json").exists()
