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


def _make_game(tmp: Path, files: int = 1) -> Path:
    """构建最小游戏根:versions/src 与 versions/dst 各带一份不同的 options.txt。

    Args:
        tmp: 测试临时目录。
        files: 计划的 COPY 动作数(≥1):除 options.txt 外,src 侧再补
            files-1 个 src 独有文件(批次I-T6 取消用例的多文件版本)。
    """
    game = tmp / "game"
    for name, text in (("src", "fps:120\n"), ("dst", "fps:60\n")):
        d = game / "versions" / name
        d.mkdir(parents=True)
        (d / "options.txt").write_text(text, encoding="utf-8")
    src_dir = game / "versions" / "src"
    for i in range(max(files - 1, 0)):
        (src_dir / f"extra{i:02d}.txt").write_text(f"内容{i}\n", encoding="utf-8")
    return game


def _make_client(
    tmp_path: Path, monkeypatch, game: Path | None = None, files: int = 1
) -> tuple[Path, TestClient]:
    """构建注入临时 workdir 的 TestClient(game_root 已持久化到 config)。

    base_url 用 127.0.0.1 直连,Host 白名单无需为 TestClient 留后门(M1)。

    Args:
        tmp_path: 测试临时目录。
        monkeypatch: pytest monkeypatch。
        game: 显式指定游戏根;None 时按 files 构建默认布局。
        files: 默认布局的 COPY 动作数(透传 _make_game,批次I-T6)。
    """
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    game = game if game is not None else _make_game(tmp_path, files=files)
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    return game, TestClient(create_app(workdir=w), base_url="http://127.0.0.1")


def _make_client_unconfigured(tmp_path: Path, monkeypatch) -> tuple[Path, TestClient]:
    """构建未配置 game_root 的 TestClient(首跑欢迎态,spec §3.1)。

    非冻结 + chdir 空目录(无 config.yaml)→ create_app() 自动解析出
    欢迎态 workdir(game_root=None,实例态字段全 None)。
    """
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    return tmp_path, TestClient(create_app(), base_url="http://127.0.0.1")


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

    # 批次I-T1:POST 后 app.state.wdir 已重建,新根即时生效
    assert client.get("/api/config").json()["game_root"] == str(game2)
    assert client.get("/api/versions").json()["versions"] == ["v1"]
    # 落盘证据:.mcmig/config.yaml 内容已切换(重跑工具后仍生效)
    import migration.workdir as wd

    assert wd.resolve_workdir().game_root == game2
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
    # 批次I-T3:done 带 plan_id(64 hex)与 persisted, migrate 须回传该校验标识
    assert done["persisted"] is True
    assert len(done["plan_id"]) == 64

    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                           "plan_id": done["plan_id"]})
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

    批次I-T1 路径契约 v3:绿色布局实例态锚定 game_root/.mcmig(不再按 slug
    隔离到 data/<slug>/),server 传给 build_plan 的 mcmig_dir 取
    ctx.game_root/.mcmig(=ctx.snapshots.parent);若误传 workdir.root(=data/),
    build_plan 会去 data/snapshots/ 找快照,plan job 必以「缺少 src 快照」
    error 收场(原 C1 回归点,布局变更后语义不变)。
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
    # 快照/plan 均落在实例态锚定目录(game_root/.mcmig,绿色布局落盘位置断言)
    assert (w.snapshots / "src.snapshot.json").exists()
    assert (w.snapshots / "dst.snapshot.json").exists()
    assert (w.plans / "src__dst.plan.json").exists()


# ---- 批次I-T1 路径契约 v3:首跑欢迎态 + POST /api/config 重建上下文 + job 上下文定格 ----


def test_welcome_state_no_game_root(tmp_path, monkeypatch):
    """批次I-T1:未配置 game_root:GET / 200、GET /api/config 返回 null、/api/plan 422 三段式(spec §3.1)。"""
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)  # 无 config.yaml → 未配置
    client = TestClient(create_app(), base_url="http://127.0.0.1")
    assert client.get("/").status_code == 200
    assert client.get("/api/config").json() == {"game_root": None}
    resp = client.post("/api/plan", json={"src": "a", "dst": "b"})
    assert resp.status_code == 422 and resp.json()["what"]


def test_config_post_rebuilds_context(tmp_path, monkeypatch):
    """批次I-T1:POST /api/config 后:实例态落新根(game_root/.mcmig),plan job 写新位置(spec §3.1)。"""
    _game, client = _make_client_unconfigured(tmp_path, monkeypatch)
    game = tmp_path / "game"
    for name, text in (("src", "fps:120\n"), ("dst", "fps:60\n")):
        d = game / "versions" / name
        d.mkdir(parents=True)
        (d / "options.txt").write_text(text, encoding="utf-8")
    assert client.post("/api/config", json={"game_root": str(game)}).json()["ok"] is True
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    assert events[-1]["type"] == "done"
    assert (game / ".mcmig" / "snapshots" / "src.snapshot.json").is_file()
    assert (game / ".mcmig" / "plans" / "src__dst.plan.json").is_file()


def test_running_job_context_frozen_across_config_change(tmp_path, monkeypatch):
    """批次I-T1:运行中 job 用启动时捕获的实例上下文;改 game_root 只影响后续 job(spec §3.1)。"""
    game, client = _make_client(tmp_path, monkeypatch)  # 既有 helper
    game2 = tmp_path / "game2"
    (game2 / "versions").mkdir(parents=True)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 1.5)  # 放大窗口
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    client.post("/api/config", json={"game_root": str(game2)})  # 运行中切换
    events = _wait_job_done(client, job_id)
    assert events[-1]["type"] == "done"
    # 旧 job 的快照落在旧根,新根未被动过
    assert (game / ".mcmig" / "snapshots" / "src.snapshot.json").is_file()
    assert not (game2 / ".mcmig").exists()


def test_migrate_executed_plan_emits_preflight_error(tmp_path, monkeypatch):
    """批次I-T2:已执行计划再迁移 → preflight 阻断 → error 事件(details.blockers[0].code)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    events = _wait_job_done(client, r.json()["job_id"])
    assert events[-1]["type"] == "done"
    plan_id = events[-1]["plan_id"]
    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                           "plan_id": plan_id})
    events2 = _wait_job_done(client, r2.json()["job_id"])
    assert events2[-1]["type"] == "done"  # 首次执行成功,plan 已回写 executed_at
    # 指纹剔除运行结果字段 → 回写 executed_at 后 plan_id 仍有效

    r3 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                           "plan_id": plan_id})
    events3 = _wait_job_done(client, r3.json()["job_id"])
    assert events3[-1]["type"] == "error"
    err = events3[-1]
    assert {"what", "why", "details"} <= set(err.keys())
    assert err["details"]["blockers"][0]["code"] == "plan_executed"
    assert err["details"]["blockers"][0]["message"]  # 文案随事件带给前端展示


def test_migrate_preflight_warnings_emitted_as_events(tmp_path, monkeypatch):
    """批次I-T2:预检降级警告 → warning 事件(新事件型,页面忽略未知型;不阻断流程)。"""
    from migration.preflight import PreflightWarning

    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    events = _wait_job_done(client, r.json()["job_id"])
    # GUI 走严格默认(decisions=None),warnings 仅在决策放行时产生;
    # 注入替身验证「warnings → warning 事件」接线本身
    monkeypatch.setattr(
        server_module, "preflight_execute",
        lambda *a, **k: ([], [PreflightWarning("snapshot_stale", "快照比计划新")]),
    )
    plan_id = events[-1]["plan_id"]
    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                           "plan_id": plan_id})
    events2 = _wait_job_done(client, r2.json()["job_id"])
    warns = [e for e in events2 if e["type"] == "warning"]
    assert warns and warns[0]["code"] == "snapshot_stale"
    assert warns[0]["message"] == "快照比计划新"
    assert events2[-1]["type"] == "done"  # 警告不阻断,迁移照常完成


# ---- 批次I-T3:审阅有效性(done 带 plan_id/persisted;guards 校验阻断) ----


def test_plan_done_carries_plan_id_and_persisted(tmp_path, monkeypatch):
    """批次I-T3:plan job done 事件含 plan_id(64 hex)且 persisted=True。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    done = events[-1]
    assert done["type"] == "done"
    assert done["persisted"] is True
    plan_id = done["plan_id"]
    assert len(plan_id) == 64
    assert all(c in "0123456789abcdef" for c in plan_id)


def test_migrate_rejected_when_plan_not_persisted(tmp_path, monkeypatch):
    """保存失败(注入)→ persisted=False → migrate 拒绝,封死「审新执旧」(spec §3.3)。

    单分支定死(ruling 3):plan job 收尾为 done 事件(persisted:false)+ 三段式
    错误字段同事件携带;此后 migrate 同 src/dst 一律 error,绝不执行。
    """
    game, client = _make_client(tmp_path, monkeypatch)

    def _boom_write(path, payload):
        raise OSError("disk full (注入)")

    # 注入点:migration.plan 命名空间的 write_json_atomic——只影响 plan.save,
    # 不影响 plan job 前置两步 scan 的快照落盘(snapshot.py 独立绑定同名函数)
    monkeypatch.setattr("migration.plan.write_json_atomic", _boom_write)
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    done = events[-1]
    assert done["type"] == "done" and done["persisted"] is False
    assert {"what", "why", "details"} <= set(done)  # 三段式与 persisted 同一次事件
    monkeypatch.undo()  # 恢复写盘
    # 未持久化的计划不存在于盘上 → migrate 拒绝(error 事件),目标原样
    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": []})
    events2 = _wait_job_done(client, r2.json()["job_id"])
    assert events2[-1]["type"] == "error"
    assert (game / "versions" / "dst" / "options.txt").read_text(encoding="utf-8") == "fps:60\n"


def test_migrate_plan_id_mismatch_blocks(tmp_path, monkeypatch):
    """批次I-T3:提交过期 plan_id(源已变、计划已重新生成)→ guards_plan_mismatch。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job1 = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    old_id = _wait_job_done(client, job1)[-1]["plan_id"]
    # 源变化 → 重新 plan(快照内容变 → review 变 → 新指纹)
    (game / "versions" / "src" / "options.txt").write_text("fps:144\n", encoding="utf-8")
    job2 = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    new_id = _wait_job_done(client, job2)[-1]["plan_id"]
    assert new_id != old_id
    # 用旧 plan_id 迁移:盘上是新计划,指纹失配 → error(guards_plan_mismatch),零执行
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                          "plan_id": old_id})
    events = _wait_job_done(client, r.json()["job_id"])
    assert events[-1]["type"] == "error"
    assert events[-1]["details"]["code"] == "guards_plan_mismatch"
    assert (game / "versions" / "dst" / "options.txt").read_text(encoding="utf-8") == "fps:60\n"


def test_migrate_target_drift_blocks(tmp_path, monkeypatch):
    """批次I-T3:plan 成功后手改 dst/options.txt(未重扫)→ migrate error 含 target_state_changed。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    plan_id = _wait_job_done(client, job)[-1]["plan_id"]
    (game / "versions" / "dst" / "options.txt").write_text("drifted\n", encoding="utf-8")
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                          "plan_id": plan_id})
    events = _wait_job_done(client, r.json()["job_id"])
    assert events[-1]["type"] == "error"
    err = events[-1]
    assert err["details"]["code"] == "target_state_changed"
    codes = [b["code"] for b in err["details"]["blockers"]]
    assert "target_state_changed" in codes
    assert (game / "versions" / "dst" / "options.txt").read_text(encoding="utf-8") == "drifted\n"


# ---- 批次I-T4:job 状态机 + GET /api/jobs/{id} + SSE 广播衔接协议 ----


def _run_plan_for_migrate(client: TestClient) -> str:
    """T4 用例前置:跑一个 plan job,断言成功并返回 plan_id(migrate 前置)。

    T3 落地后 migrate 必须回传 plan_id,brief 草图中「直接 POST /api/migrate」
    需补此前置(否则按 plan_id_missing 出 error 事件,与草图的 succeeded 断言矛盾)。
    """
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    done = _wait_job_done(client, job)[-1]
    assert done["type"] == "done"
    return done["plan_id"]


def _run_migrate_done(client: TestClient, plan_id: str) -> str:
    """T4 用例前置:带 plan_id 跑一个 migrate job 并等收尾,返回 job_id。"""
    payload = {"src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id}
    job_id = client.post("/api/migrate", json=payload).json()["job_id"]
    _wait_job_done(client, job_id)
    return job_id


def test_status_endpoint_returns_terminal_state(tmp_path, monkeypatch):
    """GET /api/jobs/{id}: 终态+summary+results(含备份位置与失败明细,非仅计数)(spec §4.1)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)
    job_id = _run_migrate_done(client, plan_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    assert body["kind"] == "migrate"
    assert body["results"][0]["path"] == "options.txt"
    assert "backup" in json.dumps(body) or body["results"][0].get("backed_up") is not None
    # summary 与 migrate done 事件同源(五键计数);phase/progress 从最后事件派生
    assert body["summary"]["failed"] == 0
    assert body["phase"] == "migrate"
    assert body["progress"] == {"index": 1, "total": 1}
    assert body["error"] is None


def test_status_endpoint_running_state(tmp_path, monkeypatch):
    """运行中查询:status=running、error/results 为 None、revision 单调可见。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 0.5)  # 放大窗口
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "running"
    assert body["kind"] == "plan"
    assert body["error"] is None and body["results"] is None and body["summary"] is None
    assert body["phase"] is None and body["progress"] is None  # 尚无 phase/file 事件
    assert body["revision"] == 0
    _wait_job_done(client, job_id)  # 收尾(避免 job 线程悬至下一用例)


def test_status_endpoint_failed_on_error_event(tmp_path, monkeypatch):
    """migrate 无计划文件 → error 事件 → 终态 failed,error 载三段式字段。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": []}
                         ).json()["job_id"]
    events = _wait_job_done(client, job_id)
    assert events[-1]["type"] == "error"
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert {"what", "why", "details"} <= set(body["error"].keys())
    assert body["results"] is None


def test_status_endpoint_partial_failed(tmp_path, monkeypatch):
    """results 含失败文件 → 终态 partial_failed,失败明细可查(非仅计数)。"""
    from migration.executor import FileResult

    _game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)
    monkeypatch.setattr(
        server_module, "execute_migration",
        lambda *a, **k: [FileResult(path="options.txt", status="copied", failed=True,
                                    error="注入失败")],
    )
    job_id = _run_migrate_done(client, plan_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "partial_failed"
    assert body["error"] is None
    assert body["results"][0]["failed"] is True
    assert body["results"][0]["error"] == "注入失败"
    assert body["summary"]["failed"] == 1


def test_plan_job_error_maps_failed_status(tmp_path, monkeypatch):
    """plan job 的 error 事件同样映射 failed(scan 注入异常 → 终端可查)。"""

    def _boom(*_a, **_k):
        raise RuntimeError("注入扫描失败")

    _game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "scan_version", _boom)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job_id)
    assert events[-1]["type"] == "error"
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "failed"


def test_status_endpoint_unknown_job_404(tmp_path, monkeypatch):
    """GET /api/jobs/{id}:未知 job → 404 三段式(与 SSE 端点同口径)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.get("/api/jobs/ghost")
    assert r.status_code == 404
    assert {"what", "why", "details"} <= set(r.json().keys())


def test_job_can_cancel_by_kind() -> None:
    """can_cancel 按 kind:migrate 可取消(T6 端点消费),plan 不可。"""
    assert server_module.Job("a", "migrate").can_cancel is True
    assert server_module.Job("b", "plan").can_cancel is False


def test_subscribe_after_completion_replays_history(tmp_path, monkeypatch):
    """终态漏接封堵: 任务完成后带 last_event_id=0 订阅 → 全量重放+终态(spec §4.1 衔接协议)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)
    job_id = _run_migrate_done(client, plan_id)  # 先完成(不订阅)
    with client.stream("GET", f"/api/jobs/{job_id}/events?last_event_id=0") as resp:
        events = [json.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]
    assert [e["type"] for e in events][-1] == "done"
    assert all(e.get("seq", 0) == i + 1 for i, e in enumerate(events))   # 序号连续


def test_last_event_id_header_replays_from_seq(tmp_path, monkeypatch):
    """Last-Event-ID 请求头与 query 等价:从 seq 之后重放,且每帧带 id: 行。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)
    job_id = _run_migrate_done(client, plan_id)  # 事件序列: phase(1) file(2) done(3)
    with client.stream("GET", f"/api/jobs/{job_id}/events",
                       headers={"Last-Event-ID": "1"}) as resp:
        lines = [ln for ln in resp.iter_lines() if ln]
    events = [json.loads(ln[6:]) for ln in lines if ln.startswith("data: ")]
    assert [e["type"] for e in events] == ["file", "done"]
    assert [e["seq"] for e in events] == [2, 3]
    assert "id: 2" in lines and "id: 3" in lines  # SSE id 行 = 事件 seq


def test_two_subscribers_each_receive_all_events(tmp_path, monkeypatch):
    """广播: 两个并发订阅者各自收全量(修单队列分摊)(spec §4.1)。

    实现者注:同client双流受 TestClient 线程模型限制,改为「同一 app 两个独立
    client 各持一条流」——广播语义不变(同一 JobStore 的两个订阅者),时序稳定。
    """
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 1.0)
    _game, client = _make_client(tmp_path, monkeypatch)
    client2 = TestClient(client.app, base_url="http://127.0.0.1")
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    collected: list[list[dict]] = [[], []]
    with client.stream("GET", f"/api/jobs/{job_id}/events") as r1, \
         client2.stream("GET", f"/api/jobs/{job_id}/events") as r2:
        for src, out in ((r1, collected[0]), (r2, collected[1])):
            for line in src.iter_lines():
                if line.startswith("data: "):
                    out.append(json.loads(line[6:]))
                if out and out[-1].get("type") in ("done", "error"):
                    break
    assert [e["type"] for e in collected[0]] == [e["type"] for e in collected[1]]
    assert collected[0][-1]["type"] == "done"  # 双方均收到终态,而非单队列分摊


def test_slow_subscriber_dropped_without_backpressure(monkeypatch):
    """慢订阅者队列有界: 满则丢弃该订阅者并要求重读状态,不影响 job(不反压复制线程)。

    Job 级单元断言(确定性):容量 2,连发 5 事件——快订阅者随发随收全量,
    慢订阅者在第 3 次 put 时被移除,emit 全程不阻塞不抛错。
    """
    monkeypatch.setattr(server_module, "_SUBSCRIBER_QUEUE_LIMIT", 2)
    job = server_module.Job("j-slow", "migrate")
    _reset1, _replay1, q_fast = job.subscribe(0)
    _reset2, _replay2, q_slow = job.subscribe(0)  # 从不消费(慢订阅者)
    got: list[dict] = []
    for i in range(5):
        job.emit({"type": "phase", "name": f"p{i}"})
        got.append(q_fast.get_nowait())  # 快订阅者随发随收,队列永不积压
    assert [e["seq"] for e in got] == [1, 2, 3, 4, 5]  # 快订阅者全量
    assert q_slow not in job._subs                     # 慢订阅者已因队列满被丢弃
    job.unsubscribe(q_fast)


def test_slow_subscriber_http_job_not_blocked(tmp_path, monkeypatch):
    """HTTP 级:存在从不消费的订阅者时 job 照常收尾,状态端点可查询终态(不反压)。"""
    import time

    monkeypatch.setattr(server_module, "_SUBSCRIBER_QUEUE_LIMIT", 2)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 0.5)  # 保证订阅先于事件
    _game, client = _make_client(tmp_path, monkeypatch)
    watcher = TestClient(client.app, base_url="http://127.0.0.1")
    plan_id = _run_plan_for_migrate(client)
    job_id = client.post(
        "/api/migrate",
        json={"src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id},
    ).json()["job_id"]
    with client.stream("GET", f"/api/jobs/{job_id}/events") as _slow:  # 打开后不读任何字节
        status = "running"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = watcher.get(f"/api/jobs/{job_id}").json()["status"]
            if status != "running":
                break
            time.sleep(0.05)
    assert status == "succeeded"  # 慢订阅者未拖住 job(事件 3 个 > 容量 2,已丢弃)
    events = _wait_job_done(client, job_id)  # 事后重放:历史完整可查
    assert events[-1]["type"] == "done"


def test_history_truncated_emits_reset(tmp_path, monkeypatch):
    """历史截断:last_event_id=0 订阅 → 首事件 {"type":"reset"},其后为保留的历史尾。"""
    monkeypatch.setattr(server_module, "_EVENT_HISTORY_LIMIT", 2)
    _game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)
    job_id = _run_migrate_done(client, plan_id)  # phase(1) file(2) done(3),只留尾 2 条
    with client.stream("GET", f"/api/jobs/{job_id}/events?last_event_id=0") as resp:
        events = [json.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]
    assert events[0] == {"type": "reset"}  # reset 不带 seq(非真实事件,客户端重读状态)
    assert [e["type"] for e in events[1:]] == ["file", "done"]
    assert [e["seq"] for e in events[1:]] == [2, 3]  # 历史尾保留原序号


# ---- 批次I-T5:跨进程实例锁接线(锁体真实现由 test_instlock.py 覆盖) ----


def _recording_locks(calls):
    """构造记录调用参数的 instance_locks 替身(monkeypatch 用,零争用即时通过)。"""
    from contextlib import contextmanager

    @contextmanager
    def _locks(game_root, *versions, timeout=5.0):
        calls.append((Path(game_root), versions))
        yield {"abandoned": []}

    return _locks


def test_plan_job_acquires_instance_lock(tmp_path, monkeypatch):
    """plan job 以 ctx.game_root+src+dst 取锁(scan×2+build_plan 全程持锁,批次I-T5)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    calls: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(server_module, "instance_locks", _recording_locks(calls))
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    assert events[-1]["type"] == "done"
    assert calls == [(game, ("src", "dst"))]


def test_migrate_job_acquires_instance_lock(tmp_path, monkeypatch):
    """migrate job 以 ctx.game_root+src+dst 取锁(守卫/预检/执行最外层,批次I-T5)。

    同进程双 job 已由 JobStore 单任务锁先行 409,不会自锁;本用例只验接线与参数。
    """
    game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)  # plan 阶段用真锁跑完(不记录)
    calls: list[tuple[Path, tuple[str, ...]]] = []
    monkeypatch.setattr(server_module, "instance_locks", _recording_locks(calls))
    job_id = _run_migrate_done(client, plan_id)
    assert calls == [(game, ("src", "dst"))]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "succeeded"


def _contended_locks():
    """构造获取即抛 InstanceLockError 的替身(模拟另一 mcmig 进程持锁,批次I-T5)。"""
    from contextlib import contextmanager

    from migration.instlock import InstanceLockError

    @contextmanager
    def _locks(game_root, *versions, timeout=5.0):
        key = str(Path(game_root) / "versions" / versions[0])
        raise InstanceLockError(
            f"实例锁({key})",
            "获取实例排他锁超时:另一 mcmig 进程正在操作该实例",
            {"holders": [key], "timeout": timeout},
        )
        yield  # pragma: no cover — 仅为满足 contextmanager 生成器语法

    return _locks


def test_plan_job_lock_error_emits_error_event(tmp_path, monkeypatch):
    """plan job 实例锁获取失败 → error 事件(job_err_instlock 文案,details.holders)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "instance_locks", _contended_locks())
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert r.status_code == 200  # 锁失败在 job 内,启动仍 200
    events = _wait_job_done(client, r.json()["job_id"])
    err = events[-1]
    assert err["type"] == "error"
    assert err["what"] == server_module.STRINGS["job_err_instlock.what"]
    assert err["why"] == server_module.STRINGS["job_err_instlock.why"]
    assert err["details"]["holders"]
    assert client.get(f"/api/jobs/{r.json()['job_id']}").json()["status"] == "failed"


def test_migrate_job_lock_error_emits_error_event(tmp_path, monkeypatch):
    """migrate job 实例锁获取失败 → error 事件(job_err_instlock 文案,details.holders)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "instance_locks", _contended_locks())
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                          "plan_id": "deadbeef"})
    assert r.status_code == 200
    events = _wait_job_done(client, r.json()["job_id"])
    err = events[-1]
    assert err["type"] == "error"
    assert err["what"] == server_module.STRINGS["job_err_instlock.what"]
    assert err["why"] == server_module.STRINGS["job_err_instlock.why"]
    assert "src" in err["details"]["holders"][0]


# ---- 批次I-T6:恢复与取消——cancel 端点/journal 接线/interrupted 清单/退出入口 ----


def test_cancel_migrate_flows_to_cancelled(tmp_path, monkeypatch):
    """取消:202+cancelling 中间态 → 「正在停止」 → 终态 cancelled+部分完成清单(spec §4.3)。"""
    game, client = _make_client(tmp_path, monkeypatch, files=20)  # 多文件版本
    plan_id = _run_plan_for_migrate(client)  # migrate 前置:先出计划(T3 起 plan_id 必传)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 5.0)  # 拉长窗口换确定取消
    job_id = client.post(
        "/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id}
    ).json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 202
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelling"
    events = _wait_job_done(client, job_id)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
    assert events[-1]["type"] == "done" and events[-1].get("cancelled") is True
    # 部分完成清单:首个动作恒执行(安全边界),其余未分发;计划不回写 executed_at
    assert events[-1]["completed"] and len(events[-1]["pending"]) == 19
    assert (game / ".mcmig" / "jobs" / f"{job_id}.json").is_file()


def test_cancel_plan_job_unsupported(tmp_path, monkeypatch):
    """扫描/plan 阶段不可取消:405 三段式,无假按钮语义(spec §4.3)。"""
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 2.0)
    _game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 405 and resp.json()["what"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "running"  # 取消不影响运行
    _wait_job_done(client, job_id)


def test_cancel_terminal_job_conflict(tmp_path, monkeypatch):
    """终态 job 取消 → 409(已结束,无可取消对象)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)
    job_id = _run_migrate_done(client, plan_id)
    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 409 and resp.json()["what"]


def test_interrupted_listing_from_journal(tmp_path, monkeypatch):
    """启动期扫描 journal: 未收尾 job 列为 interrupted(待核对),≠cancelled(spec §4.3)。"""
    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    (game / ".mcmig" / "jobs").mkdir(parents=True)
    JobJournal(game / ".mcmig" / "jobs", "deadbeef", "migrate").record_intent(
        "options.txt", {"op": "copy"}
    )
    items = client.get("/api/jobs/interrupted").json()["items"]
    assert items[0]["job_id"] == "deadbeef" and items[0]["entries"][0]["rel"] == "options.txt"


def test_shutdown_endpoint_guards_on_busy(tmp_path, monkeypatch):
    """退出入口: 空闲 200 置停机标志;job 运行 409(spec §4.3,uvicorn 侧接线归 T10)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    assert client.post("/api/shutdown").json()["ok"] is True
    assert client.app.state.shutdown_requested is True
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 2.0)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    assert client.post("/api/shutdown").status_code == 409
    _wait_job_done(client, job_id)


# ---- 批次I-T6 修复(finding 1/2):journal 停发呈现与取消清单口径 ----


def test_journal_write_failure_not_locked_as_executed(tmp_path, monkeypatch):
    """journal 写失败停发: error 事件呈现(非静默 succeeded),executed_at 不回写,计划不锁(finding 1)。

    注入点:migration.journal 命名空间的 write_json_atomic——首动作 intent+completion
    两次写成功后,第二个动作的 intent 写失败 → Executor 停发(journal_failed),
    仅 1 文件落盘。修复前:done 事件普通摘要 + status=succeeded + mark_executed 回写
    → plan_executed 锁死计划,文件被静默漏迁。
    """
    import migration.journal as jm

    game, client = _make_client(tmp_path, monkeypatch, files=3)
    plan_id = _run_plan_for_migrate(client)
    real_write = jm.write_json_atomic
    calls = {"n": 0}

    def _flaky(path, payload):
        calls["n"] += 1
        if calls["n"] > 2:  # #1 intent(首动作) #2 completion(首动作) 成功;#3 起 disk
            raise OSError("disk")
        real_write(path, payload)

    monkeypatch.setattr(jm, "write_json_atomic", _flaky)
    job_id = client.post(
        "/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id}
    ).json()["job_id"]
    events = _wait_job_done(client, job_id)
    # 终态是 error 事件(带 journal 停发原因),绝非普通 succeeded 的 done
    assert events[-1]["type"] == "error"
    assert events[-1]["what"] == server_module.STRINGS["job_err_journal.what"]
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "failed"
    assert body["results"] is not None and len(body["results"]) == 1  # 部分结果可见
    assert not body["results"][0]["failed"]
    assert (game / "versions" / "dst" / body["results"][0]["path"]).is_file()
    # executed_at 未回写:盘上计划仍可执行(未被 plan_executed 锁死)
    plan_json = json.loads(
        (game / ".mcmig" / "plans" / "src__dst.plan.json").read_text(encoding="utf-8")
    )
    assert plan_json["executed_at"] is None
    # 重跑验证:阻断来自目标漂移(target_state_changed,提示重新审阅)而非 plan_executed
    monkeypatch.undo()
    job2 = client.post(
        "/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id}
    ).json()["job_id"]
    err2 = _wait_job_done(client, job2)[-1]
    assert err2["type"] == "error" and err2["details"]["code"] == "target_state_changed"


def test_cancelled_completed_list_excludes_failed(tmp_path, monkeypatch):
    """取消清单口径: completed 只含成功落盘文件,失败文件(status=copied+failed)不计(finding 2)。"""
    from migration.fsops import FsOpsError

    import migration.executor as ex_mod

    _game, client = _make_client(tmp_path, monkeypatch, files=3)
    plan_id = _run_plan_for_migrate(client)

    def _fail_all(src, dst, **kw):
        raise FsOpsError(str(src), "注入失败:取消清单口径用例")

    monkeypatch.setattr(ex_mod, "copy_atomic", _fail_all)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 5.0)
    job_id = client.post(
        "/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id}
    ).json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 202
    events = _wait_job_done(client, job_id)
    done = events[-1]
    assert done["type"] == "done" and done["cancelled"] is True
    body = client.get(f"/api/jobs/{job_id}").json()
    # 首动作恒执行且注入失败:results[0] 是 failed 的「copied」——正是 finding 2 的形态
    assert body["results"][0]["failed"] is True
    assert body["results"][0]["status"] == "copied"
    assert done["completed"] == []  # 失败文件绝不计入「已完成/已落盘」
    assert len(done["pending"]) == 2  # 其余动作未分发,属未执行
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"


# ---- 批次I W1W2 终审修复:终态单次判定(I2)/ GUI 旧规则回退(I4) ----


def test_cancel_after_settle_rejected_and_terminal_consistent(tmp_path, monkeypatch):
    """I2:终态单次判定——settle 后取消窗口封死,四视图(事件/回写/journal/状态)一致。

    确定性竞态钉:monkeypatch Job.settle 在真判定完成后阻塞 job 线程,主线程在此刻
    发 cancel——修复前该窗口可 202 并翻转状态(出现 done 说成功/executed_at 已回写/
    journal 已 finish、GET status 永久报 cancelled 的自相矛盾);修复后必 409。
    竞态本身不可确定性复现,故钉其不变量:判定与收尾后 status 不再变化、cancel 恒 409。
    """
    import threading

    game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)

    settled = threading.Event()
    release = threading.Event()
    real_settle = server_module.Job.settle

    def _slow_settle(self):
        """真判定完成后挂起:把「判定成功 → done 事件尚未发出」的窗口撑到可观测。"""
        result = real_settle(self)
        settled.set()
        if not release.wait(timeout=10):
            raise RuntimeError("settle 门闩未被放行(测试自身超时)")
        return result

    monkeypatch.setattr(server_module.Job, "settle", _slow_settle)
    monkeypatch.setattr(
        server_module, "execute_migration",
        lambda *a, **k: [server_module.FileResult(path="options.txt", status="copied")],
    )
    job_id = client.post(
        "/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id}
    ).json()["job_id"]
    assert settled.wait(timeout=10)  # job 线程已完成终态判定(done 尚未发出)
    # 判定之后、收尾之前:取消必须被 409 拒绝(终态已定,不再受理翻案)
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
    release.set()  # 放行 job 线程:按已判定的「成功」收尾
    events = _wait_job_done(client, job_id)
    done = events[-1]
    assert done["type"] == "done" and "cancelled" not in done  # 事件口径:成功
    assert done["summary"]["copied"] == 1
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "succeeded"  # 状态口径:与 done 事件同一次判定导出
    # 回写口径:成功 → executed_at 已落盘;journal 正常收尾(不在中断清单)
    plan_json = json.loads(
        (game / ".mcmig" / "plans" / "src__dst.plan.json").read_text(encoding="utf-8")
    )
    assert plan_json["executed_at"] is not None
    assert client.get("/api/jobs/interrupted").json()["items"] == []
    # 不变量:done 后 status 永不再变化,重复 cancel 恒 409
    for _ in range(3):
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 409
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "succeeded"


def test_plan_job_uses_legacy_rules_with_notice(tmp_path, monkeypatch):
    """I4:仅旧位置存在 rules.yaml → GUI 计划受其影响 + notice 提示;锚定快照仍落 data_dir。

    绿色/兼容老用户的旧布局规则不再被无声忽略(spec §3.1 只读回退+命中提示);
    migrate 侧守卫与签发同构(同参 _rules_dir),不因路径失配误报 rules_changed。
    """
    import migration.workdir as wd

    monkeypatch.setattr(wd, "_is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    game = _make_game(tmp_path)  # src/dst 各带一份不同的 options.txt
    # 旧布局:CWD 侧 .mcmig 带 snapshots 目录(触发 legacy_snapshots 只读回退标记)
    # 与 rules.yaml(never 规则压过 options.txt 的默认 must_migrate)
    (tmp_path / ".mcmig" / "snapshots").mkdir(parents=True)
    (tmp_path / ".mcmig" / "rules.yaml").write_text(
        "version: 1\nrules:\n  - match: 'options.txt'\n    decide: never\n"
        "    reason: 'legacy override'\n",
        encoding="utf-8",
    )
    w = wd.resolve_workdir(game_root=game)
    w.save_game_root(game)
    client = TestClient(create_app(workdir=w), base_url="http://127.0.0.1")

    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    done = events[-1]
    assert done["type"] == "done"
    # 旧 never 规则生效:options.txt 不再入 must_migrate(空分组不出现)
    assert "must_migrate" not in (done["plan"] or {})
    # 命中提示经既有 notice 事件通道流出(回退可见,不再无声)
    notices = [e["text"] for e in events if e["type"] == "notice"]
    assert any("旧布局规则" in t for t in notices)
    # 快照/计划仍锚定实例态 data_dir(游戏根 .mcmig),mcmig_dir 回退位不影响定位
    assert (game / ".mcmig" / "snapshots" / "src.snapshot.json").is_file()
    assert (game / ".mcmig" / "snapshots" / "dst.snapshot.json").is_file()
    assert (game / ".mcmig" / "plans" / "src__dst.plan.json").is_file()
    # migrate 侧审阅守卫与签发同构:旧布局规则用户不误报 rules_changed,照常收尾
    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                           "plan_id": done["plan_id"]})
    events2 = _wait_job_done(client, r2.json()["job_id"])
    assert events2[-1]["type"] == "done"
