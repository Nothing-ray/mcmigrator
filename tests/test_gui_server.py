"""gui/server 测试:API 契约/SSE 序列/单锁/Host 校验/ask_yes 传递/错误三段式。

基于 2026-09-04-gui-v0.6 计划 Task 6 Step 1 的测试契约,按 brief「实现者注意」修正:
- `_make_game` 中残留的 `.json` 占位行已删除;
- 兼容模式下 `resolve_workdir(game_root=...)` 不落盘 game_root(Task 4 已知遗留),
  而 server 以 workdir config 为唯一事实来源,故每个测试先 `save_game_root` 持久化。
"""

from __future__ import annotations

import asyncio
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
    # 修复 A6:why 取首条阻断的具体指引(不再是「请重新生成并审阅计划」通用文案)
    assert err["why"] == err["details"]["blockers"][0]["message"]
    assert "已执行" in err["why"]


def test_legacy_plan_migrate_state_drift_blocked(tmp_path, monkeypatch):
    """修复 A2:review=None 旧版计划不再跳过状态校验——与 CLI 同构(执行侧
    内部兜底 validate_states=plan.review is None,cli.py:_cmd_migrate 同参)。
    剥离审阅守卫后改动源文件(审阅后漂移),迁移必须阻断而非静默覆盖;
    修复前 GUI 传 validate_states=False 且 precheck 对 review=None 不查状态
    → 漂移被静默覆盖(GUI 防护弱于 CLI 的白名单外行为)。"""
    import json as _json

    from migration.plan import MigrationPlan
    from migration.review import plan_fingerprint

    game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    done = _wait_job_done(client, r.json()["job_id"])[-1]
    assert done["type"] == "done"
    # 剥离审阅守卫(模拟旧版工具签发的计划)并复算指纹(review 参与 plan_id)
    plan_file = game / ".mcmig" / "plans" / "src__dst.plan.json"
    payload = _json.loads(plan_file.read_text(encoding="utf-8"))
    payload["review"] = None
    plan_file.write_text(
        _json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    legacy_id = plan_fingerprint(MigrationPlan.load(plan_file))
    # 审阅后漂移:改动源文件内容(快照记录的 md5 失配)
    (game / "versions" / "src" / "options.txt").write_text("drift\n", encoding="utf-8")

    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                           "plan_id": legacy_id})
    events = _wait_job_done(client, r2.json()["job_id"])
    assert events[-1]["type"] == "error", "旧版计划审阅后漂移必须阻断(修复 A2)"
    err = events[-1]
    assert err["details"]["code"] == "review_state_changed"
    assert err["details"]["blockers"][0]["code"] == "source_state_changed"
    # 漂移未被覆盖到目标:目标 options.txt 保持计划前内容
    assert (game / "versions" / "dst" / "options.txt").read_text(
        encoding="utf-8") == "fps:60\n"


def test_migrate_preflight_warnings_emitted_as_events(tmp_path, monkeypatch):
    """批次I-T2:预检降级警告 → warning 事件(新事件型,页面忽略未知型;不阻断流程)。

    批次I-W3 T1 起 migrate 前置检查经 pipeline.precheck_execution 单点,
    注入点随之迁移(替身返回 PrecheckOutcome)。
    """
    from migration.pipeline import PrecheckOutcome
    from migration.preflight import PreflightWarning

    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    events = _wait_job_done(client, r.json()["job_id"])
    # GUI 走严格默认(decisions=None),warnings 仅在决策放行时产生;
    # 注入替身验证「warnings → warning 事件」接线本身
    monkeypatch.setattr(
        server_module, "precheck_execution",
        lambda *a, **k: PrecheckOutcome(
            [], [PreflightWarning("snapshot_stale", "快照比计划新")], False),
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
    (W3 T2 起 subscribe 返回 _Sub 订阅者对象,取队列经 ``.queue``。)
    """
    monkeypatch.setattr(server_module, "_SUBSCRIBER_QUEUE_LIMIT", 2)
    job = server_module.Job("j-slow", "migrate")
    _reset1, _replay1, sub_fast = job.subscribe(0)
    _reset2, _replay2, sub_slow = job.subscribe(0)  # 从不消费(慢订阅者)
    got: list[dict] = []
    for i in range(5):
        job.emit({"type": "phase", "name": f"p{i}"})
        got.append(sub_fast.queue.get_nowait())  # 快订阅者随发随收,队列永不积压
    assert [e["seq"] for e in got] == [1, 2, 3, 4, 5]  # 快订阅者全量
    assert sub_slow not in job._subs                 # 慢订阅者已因队列满被摘除
    assert sub_slow.dropped                          # 且已标记(其流将以 overflow 帧收尾)
    job.unsubscribe(sub_fast)


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
    assert client.post(f"/api/jobs/{job_id}/cancel", json={}).status_code == 202
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelling"
    events = _wait_job_done(client, job_id)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
    assert events[-1]["type"] == "done" and events[-1].get("cancelled") is True
    # 部分完成清单:首个动作恒执行(安全边界),其余未分发;计划不回写 executed_at
    assert events[-1]["completed"] and len(events[-1]["pending"]) == 19
    assert (game / ".mcmig" / "jobs" / f"{job_id}.jsonl").is_file()


def test_cancel_plan_job_unsupported(tmp_path, monkeypatch):
    """扫描/plan 阶段不可取消:405 三段式,无假按钮语义(spec §4.3)。"""
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 2.0)
    _game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    resp = client.post(f"/api/jobs/{job_id}/cancel", json={})
    assert resp.status_code == 405 and resp.json()["what"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "running"  # 取消不影响运行
    _wait_job_done(client, job_id)


def test_cancel_terminal_job_conflict(tmp_path, monkeypatch):
    """终态 job 取消 → 409(已结束,无可取消对象)。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    plan_id = _run_plan_for_migrate(client)
    job_id = _run_migrate_done(client, plan_id)
    resp = client.post(f"/api/jobs/{job_id}/cancel", json={})
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
    # 批次I-W3 T3 新返回形:身份字段照带(本条无身份=旧式构造);有待核对条目
    # → unknown_progress=False(整体进度以 entries 呈现)
    assert items[0]["src"] == "" and items[0]["dst"] == "" and items[0]["game_root"] == ""
    assert items[0]["unknown_progress"] is False


def test_interrupted_listing_filters_running_job(tmp_path, monkeypatch):
    """W2.5 复审 B4:在跑 job 的 journal 不入 interrupted 清单(活性过滤)。

    迁移进行中刷新页面,当前任务的 journal 同样「未收尾+有 unfinished」——
    不得被报成「上次未完成的迁移」(横幅与仍在增长的进度自相矛盾)。
    手工预置与在跑 job 同名的 journal,保证观测窗内 journal 确已落盘(不依赖
    job 内部时序):该条被剔除,无关的中断残留照常呈现。
    """
    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    (game / ".mcmig" / "jobs").mkdir(parents=True)
    JobJournal(game / ".mcmig" / "jobs", "deadbeef", "migrate").record_intent(
        "options.txt", {"op": "copy"}
    )
    events = _wait_job_done(client, client.post(
        "/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"])
    plan_id = events[-1]["plan_id"]
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 1.5)  # 放大窗口
    live = client.post("/api/migrate", json={
        "src": "src", "dst": "dst", "ask_yes": [], "plan_id": plan_id}).json()["job_id"]
    # 与在跑 job 同名的 journal 已落盘(手工预置,模拟 journal 写入后 job 仍在跑)
    JobJournal(game / ".mcmig" / "jobs", live, "migrate").record_intent(
        "a.txt", {"op": "copy"})
    items = client.get("/api/jobs/interrupted").json()["items"]
    assert [i["job_id"] for i in items] == ["deadbeef"]  # 在跑 job 被剔除,残留保留
    _wait_job_done(client, live)
    # 收尾后(finished 标记)同样不入清单;残留仍在
    items2 = client.get("/api/jobs/interrupted").json()["items"]
    assert [i["job_id"] for i in items2] == ["deadbeef"]


def test_shutdown_endpoint_drains_atomically(tmp_path, monkeypatch):
    """退出入口(修复 A5):忙 → 409 且不置位;空闲 → begin_shutdown 原子排空
    (draining + 停机标志),此后新任务在 JobStore.start 同临界区被拒 503——
    封死「检查为空闲 → 新任务启动 → 进程退出」竞争窗。"""
    _game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(server_module, "_JOB_MIN_ALIVE_SECONDS", 2.0)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    busy = client.post("/api/shutdown", json={})
    assert busy.status_code == 409
    assert busy.json()["details"]["task"]                  # details 附任务描述
    assert client.app.state.shutdown_requested is False    # 忙:不置位、不排空
    assert client.app.state.jobs.draining is False
    _wait_job_done(client, job_id)

    assert client.post("/api/shutdown", json={}).json()["ok"] is True
    assert client.app.state.shutdown_requested is True
    assert client.app.state.jobs.draining is True          # 原子排空已置位
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert r.status_code == 503                            # 排空后拒绝新任务
    assert {"what", "why", "details"} <= set(r.json().keys())


def test_jobstore_evicts_oldest_done_jobs():
    """修复 A7:_jobs 只增不减 → 按插入序淘汰最旧**终态** job,保留最近
    ``_MAX_JOBS`` 条;在跑 job 永不淘汰。"""
    from migration.gui.server import Job, JobStore

    store = JobStore()
    for i in range(server_module._MAX_JOBS + 5):
        j = Job(f"done{i}", "plan")
        j.done = True
        store._jobs[j.id] = j
    running = Job("running", "migrate")          # 未收尾(在跑)
    store._jobs[running.id] = running
    store._current = running
    store._evict_done_locked()
    ids = set(store._jobs)
    assert len(store._jobs) == server_module._MAX_JOBS
    assert "running" in ids                        # 在跑 job 保留
    assert "done0" not in ids                      # 最旧终态被淘汰
    assert f"done{server_module._MAX_JOBS + 4}" in ids  # 最新终态保留


def test_jobstore_eviction_wired_and_old_job_404(tmp_path, monkeypatch):
    """修复 A7 接线:经 start() 连续注册 job 触发淘汰——超出上限的最旧 job
    从仓库消失(GET 404),最近 job 仍可查(GET 200)。"""
    monkeypatch.setattr(server_module, "_MAX_JOBS", 3)
    _game, client = _make_client(tmp_path, monkeypatch)
    ids = []
    for _ in range(5):
        jid = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
        _wait_job_done(client, jid)
        ids.append(jid)
    assert client.get(f"/api/jobs/{ids[-1]}").status_code == 200   # 最近:可查
    assert client.get(f"/api/jobs/{ids[0]}").status_code == 404    # 最旧:已淘汰


# ---- 批次I-T6 修复(finding 1/2):journal 停发呈现与取消清单口径 ----


def test_journal_write_failure_not_locked_as_executed(tmp_path, monkeypatch):
    """journal 写失败停发: error 事件呈现(非静默 succeeded),executed_at 不回写,计划不锁(finding 1)。

    注入点(批次I-W3 T3 机械更新):``JobJournal._append``——JSONL 追加原语,
    替代旧版全量重写锚点 write_json_atomic;start 行不计次(构造期写入),
    保持「首动作 intent+completion 两次追加成功后,第二个动作的 intent 失败」
    的等价注入语义。修复前:done 事件普通摘要 + status=succeeded + mark_executed 回写
    → plan_executed 锁死计划,文件被静默漏迁。
    """
    from migration.journal import JobJournal, JournalError

    game, client = _make_client(tmp_path, monkeypatch, files=3)
    plan_id = _run_plan_for_migrate(client)
    calls = {"n": 0}
    real_append = JobJournal._append

    def _flaky(self, line):
        if line.get("op") != "start":  # start 行不计次(构造期追加,非记录)
            calls["n"] += 1
        if calls["n"] > 2:  # #1 intent(首动作) #2 completion(首动作) 成功;#3 起 disk
            self.write_failed = True  # 模拟真实 _append 的 OSError 留痕语义
            raise JournalError("journal 写入失败: disk(注入)")
        real_append(self, line)

    monkeypatch.setattr(JobJournal, "_append", _flaky)
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
    assert client.post(f"/api/jobs/{job_id}/cancel", json={}).status_code == 202
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
    assert client.post(f"/api/jobs/{job_id}/cancel", json={}).status_code == 409
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
        assert client.post(f"/api/jobs/{job_id}/cancel", json={}).status_code == 409
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


# ---- 批次I-W3 T1:执行前置检查单点 precheck_execution(③④⑤ 三段合一) ----


def test_migrate_gui_legacy_snapshots_guard_no_false_positive(tmp_path, monkeypatch):
    """吸收 #1/#10:旧布局快照用户 GUI 全链路——plan 带 notice 提示,
    migrate 不误报 snapshot_changed;守卫错误 details.blockers 恒非空(#2 见下例)。

    实现者注:旧布局目录须在 client 构建前就位——WorkDir 是冻结值对象,
    resolve_workdir 在 create_app 时一次性判定 legacy_snapshots,客户端创建
    后再挪目录不会被感知(故先建游戏与旧布局、再经 game= 传参建 client)。
    rename 保 mtime,不触发 snapshot_stale(评审建议 B:先锚定扫描再 rename,
    直接对不存在的锚定快照 rename 会 FileNotFoundError)。
    """
    from migration.pipeline import scan_version

    game = _make_game(tmp_path)
    # 先锚定扫描再 rename 到旧布局(锚定目录留空壳,与 CLI 同型夹具)
    anchored = game / ".mcmig" / "snapshots"
    scan_version(game, "src", anchored)
    scan_version(game, "dst", anchored)
    (tmp_path / ".mcmig" / "snapshots").mkdir(parents=True)
    for ver in ("src", "dst"):
        (anchored / f"{ver}.snapshot.json").rename(
            tmp_path / ".mcmig" / "snapshots" / f"{ver}.snapshot.json")
    _game, client = _make_client(tmp_path, monkeypatch, game=game)
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job)
    assert events[-1]["type"] == "done"
    assert any(e["type"] == "notice" and "旧布局快照" in e["text"] for e in events)  # 吸收 #1
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst",
                                          "ask_yes": [], "plan_id": events[-1]["plan_id"]})
    events2 = _wait_job_done(client, r.json()["job_id"])
    assert events2[-1]["type"] == "done"                              # 不误报


def test_migrate_guards_error_details_never_empty_blockers(tmp_path, monkeypatch):
    """吸收 #2:guards_plan_mismatch 时 details.blockers 恒含合成条目(消费方取 [0] 安全)。

    两次 plan 之间改写 src(options.txt 内容进指纹;同秒连跑时 generated_at 与
    快照哈希可能逐字节相同,内容改动才确定性产出不同 plan_id,盘上已是新计划);
    持第一次的 plan_id 迁移即指纹失配。
    """
    game, client = _make_client(tmp_path, monkeypatch)
    j1 = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    old_id = _wait_job_done(client, j1)[-1]["plan_id"]
    (game / "versions" / "src" / "options.txt").write_text("fps:144\n", encoding="utf-8")
    j2 = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events2 = _wait_job_done(client, j2)
    assert events2[-1]["plan_id"] != old_id                           # 盘上已是新计划
    r = client.post("/api/migrate", json={"src": "src", "dst": "dst",
                                          "ask_yes": [], "plan_id": old_id})
    ev = _wait_job_done(client, r.json()["job_id"])[-1]
    assert ev["type"] == "error" and ev["details"]["code"] == "guards_plan_mismatch"
    assert ev["details"]["blockers"] and ev["details"]["blockers"][0]["code"] == "guards_plan_mismatch"


# ---- 批次I-W3 T2:SSE 终态原子提交 + 慢订阅者溢出信号 + 游标契约对齐 ----


def test_emit_terminal_settles_status_atomically():
    """P2-4:终态事件入历史与状态收口同一临界区——emit(done) 返回即终态,无 _finish。"""
    from migration.gui.server import Job
    from migration.executor import FileResult
    job = Job("t1", "migrate")
    job.emit({"type": "phase", "name": "migrate"})
    assert job.snapshot()["status"] == "running"
    job.settle()                                        # 非取消场景仍先封取消窗
    job.results = [FileResult("options.txt", "copied", failed=True, error="x")]
    job.emit({"type": "done", "job_kind": "migrate", "summary": {}})
    snap = job.snapshot()                               # emit 后立即快照,不经 _finish
    assert snap["status"] == "partial_failed" and snap["revision"] == 2


def test_emit_error_settles_failed_without_finish():
    """P2-4 同型:error 事件即收口 failed(守卫阻断的早退路径)。"""
    from migration.gui.server import Job
    job = Job("t2", "migrate")
    job.emit({"type": "error", "what": "w", "why": "y", "details": {}})
    assert job.snapshot()["status"] == "failed" and job.done


def test_error_event_wins_over_cancelling_status():
    """修复 A3①:取消落在守卫/预检窗口、随后以 error 阻断的交错——终态必须为
    failed(与 SSE error 事件一致);修复前按 cancelling 收口成 cancelled,
    GET 报「已取消」而事件说「任务失败」,状态接口与事件流自相矛盾。"""
    from migration.gui.server import Job

    job = Job("t2a", "migrate")
    job.status = "cancelling"                 # 模拟 cancel 端点已置中间态
    job.emit({"type": "error", "what": "计划审阅守卫未通过", "why": "y", "details": {}})
    snap = job.snapshot()
    assert snap["status"] == "failed"         # 修复前:cancelled
    assert snap["error"]["what"] == "计划审阅守卫未通过"
    assert job.done


def test_pure_cancel_still_reports_cancelled():
    """回归护栏(A3①):纯取消路径(无 error 事件)仍按 cancelled 收口——
    error 优先不改变合法取消的语义。"""
    from migration.gui.server import Job

    job = Job("t2b", "migrate")
    job.status = "cancelling"
    job.emit({"type": "done", "job_kind": "migrate", "cancelled": True})
    assert job.snapshot()["status"] == "cancelled"


def test_defensive_settle_emits_error_not_success():
    """修复 A3②:job 体未产出终态事件即退出(异常路径)→ 兜底 error 事件、
    状态 failed;修复前只做状态收口会被判 succeeded(SSE 无终态帧,GET 报成功,
    页面停在「运行中/重连」)。"""
    from migration.gui.server import Job

    job = Job("t2c", "plan")
    server_module._defensive_settle(job)
    snap = job.snapshot()
    assert snap["status"] == "failed"
    assert snap["error"]["details"]["code"] == "job_aborted"
    assert snap["done"]["type"] == "error" and job.done


def test_defensive_settle_noop_after_terminal_event():
    """A3② 护栏:终态事件已由 emit 原子收口的正常路径,防御收口为 no-op
    (不覆写已定格的成功载荷)。"""
    from migration.gui.server import Job

    job = Job("t2d", "plan")
    job.emit({"type": "done", "job_kind": "plan", "plan_id": "x", "persisted": True})
    server_module._defensive_settle(job)
    snap = job.snapshot()
    assert snap["status"] == "succeeded" and snap["error"] is None
    assert snap["done"]["plan_id"] == "x"


def test_defensive_settle_fallback_keeps_payload_symmetry(monkeypatch):
    """评审 M3:兜底 emit 自身失败时状态与载荷同源——_done_payload 复用同一份
    error 事件,不出现「status=failed 而 GET done=None」的不对称。"""
    from migration.gui.server import Job

    job = Job("t2e", "plan")

    def _boom(_ev: dict) -> None:
        raise RuntimeError("emit 失败(注入)")

    monkeypatch.setattr(job, "emit", _boom)
    server_module._defensive_settle(job)
    snap = job.snapshot()
    assert snap["status"] == "failed"
    assert snap["error"] is not None
    assert snap["done"] is not None and snap["done"]["type"] == "error"


def test_overflowed_subscriber_gets_explicit_signal_and_prompt_close(monkeypatch):
    """P2-5/#18:慢订阅者队列满被摘后,其流尽快以 overflow 帧收尾(而非空转到 job 结束)。

    确定性构造:手工占满订阅者队列(maxsize=1)再 emit → put_nowait 必然 Full
    → sub.dropped;随后直接泵送该 sub(_pump_sub),0.5s 轮询醒来即收尾。
    """
    monkeypatch.setattr(server_module, "_SUBSCRIBER_QUEUE_LIMIT", 1)
    job = server_module.Job("t3", "migrate")
    reset, replay, sub = job.subscribe(0)
    sub.queue.put_nowait({"type": "file", "seq": -1})   # 预占满队列(不被消费)
    job.emit({"type": "file", "path": "f0", "index": 1, "total": 9})
    assert sub.dropped                                  # 溢出即摘除+标记

    async def _collect():                               # 0.12.0 复审#7:泵已 async 化
        return [f async for f in server_module._pump_sub(job, sub, reset=False, replay=[])]

    frames = asyncio.run(_collect())
    assert any('"overflow"' in f for f in frames)       # 显式溢出帧
    assert not job.done                                 # job 未收尾,流已先行断开


def test_last_event_id_invalid_query_treated_as_zero(tmp_path, monkeypatch):
    """吸收 #3:?last_event_id=abc 按 0 处理(200 从头重放),不再 422。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    _wait_job_done(client, job_id)
    with client.stream("GET", f"/api/jobs/{job_id}/events?last_event_id=abc") as resp:
        assert resp.status_code == 200
        lines = [ln for ln in resp.iter_lines() if ln.startswith("data: ")]
    assert json.loads(lines[-1][6:])["type"] == "done"  # 从头重放含终态


def test_sse_header_overrides_stale_query_on_reconnect(tmp_path, monkeypatch):
    """评审 v3 P2-2:「显式恢复连接(带旧 query)再次断线后自动重连」——URL
    仍带旧游标,浏览器回发新 header:须按 header 续订,不得被旧 query 压回
    重放(否则反复重放/反复 reset)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    events = _wait_job_done(client, job_id)
    seqs = [e["seq"] for e in events]
    assert len(seqs) >= 2                                  # phase+done 至少两帧
    with client.stream(
        "GET", f"/api/jobs/{job_id}/events?last_event_id={seqs[0] - 1}",
        headers={"Last-Event-ID": str(seqs[-2])},
    ) as resp:
        got = [json.loads(ln[6:]) for ln in resp.iter_lines() if ln.startswith("data: ")]
    assert [e["seq"] for e in got] == seqs[-1:]            # 从 header 游标续,仅末帧


def test_status_endpoint_carries_full_done_payload(tmp_path, monkeypatch):
    """评审 P2-4:GET 终态含完整 done 载荷(plan/plan_id/persisted)——
    刷新恢复按 kind 重建页面的数据源,不再只有 status+计数(diff 键由 T6
    增补并在 T6 用例中断言)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    job_id = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    _wait_job_done(client, job_id)
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["status"] == "succeeded"
    done = body["done"]
    assert done["type"] == "done" and done["plan_id"] and done["persisted"] is True
    assert done["plan"]                                   # 审阅页可从 GET 重建


# ---- 批次I-W3 T3:interrupted 新返回形透传 + dismiss 清除通道(#9/P2-8) ----

# 仓库根(持锁子进程 ``python -c`` 须显式注入 sys.path 才能 import migration;
# _make_client 已 chdir 到 tmp_path,不依赖 cwd,同 tests/test_instlock.py 约定)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_interrupted_carries_identity_and_unknown_progress(tmp_path, monkeypatch):
    """T3 新返回形:条目携带 src/dst/game_root/unknown_progress 字段(透传,
    T5 横幅消费);无待核对条目时 unknown_progress=True。"""
    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    JobJournal(jobs_dir, "stale0", "migrate", src="src", dst="dst",
               game_root=str(game)).record_intent("a.txt", {"op": "copy"})
    j1 = JobJournal(jobs_dir, "stale1", "migrate", src="src", dst="dst",
                    game_root=str(game))
    j1.record_intent("b.txt", {"op": "copy"})
    j1.record_completion("b.txt")                           # 全完成但未收尾(崩溃前夜)
    items = {i["job_id"]: i for i in client.get("/api/jobs/interrupted").json()["items"]}
    assert set(items) == {"stale0", "stale1"}
    assert items["stale0"]["src"] == "src" and items["stale0"]["dst"] == "dst"
    assert items["stale0"]["game_root"] == str(game)
    assert items["stale0"]["unknown_progress"] is False      # 有待核对条目
    assert items["stale1"]["unknown_progress"] is True       # 全完成未收尾


def test_interrupted_dismiss_endpoint(tmp_path, monkeypatch):
    """#9:dismiss 端点删除指定未收尾 journal;再 dismiss → 404。"""
    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    JobJournal(jobs_dir, "stale1", "migrate", src="s", dst="d",
               game_root=str(game)).record_intent("a.txt", {"op": "copy"})
    assert client.post("/api/jobs/interrupted/stale1/dismiss", json={}).json()["ok"] is True
    assert not (jobs_dir / "stale1.jsonl").exists()
    assert client.post("/api/jobs/interrupted/stale1/dismiss", json={}).status_code == 404


def test_interrupted_dismiss_rejects_live_owner(tmp_path, monkeypatch):
    """评审 P2-8:活任务的 journal 不可 dismiss——跨进程持有实例锁(如正在
    运行的 CLI 迁移)时端点以 409 拒绝,防止删档后活任务重建出缺 start/意图
    的残缺 journal(恢复证据被削弱)。"""
    import subprocess
    import sys
    import textwrap

    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    JobJournal(jobs_dir, "livejob", "migrate", src="src", dst="dst",
               game_root=str(game)).record_intent("a.txt", {"op": "copy"})
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(_REPO_ROOT)!r})
            import time
            from migration.instlock import instance_locks
            with instance_locks({str(game)!r}, "src", "dst"):
                print("HELD", flush=True); time.sleep(30)
        """)],
        stdout=subprocess.PIPE)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == b"HELD"
        resp = client.post("/api/jobs/interrupted/livejob/dismiss", json={})
        assert resp.status_code == 409
        assert (jobs_dir / "livejob.jsonl").exists()    # 档案未被删除
    finally:
        holder.kill()
        holder.wait()


def test_interrupted_endpoints_survive_torn_multibyte_journal(tmp_path, monkeypatch):
    """评审(0.12.0 复审#3):journal 末行截断在多字节字符内部(中文路径)——
    修复前 read_text 整体解码失败→整档按损坏跳过,恢复证据全丢;现按字节
    逐行解码保留完整前缀:interrupted 清单呈现待核对条目(而非空),dismiss
    可正常清除(锁探针判活通过)。"""
    import json as _json

    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    start = _json.dumps({"op": "start", "kind": "migrate", "src": "src", "dst": "dst",
                         "game_root": str(game), "started_at": "t"}, ensure_ascii=False)
    ok_intent = _json.dumps({"op": "intent", "rel": "已完成的意图.txt",
                             "detail": {"op": "copy"}}, ensure_ascii=False)
    line = _json.dumps({"op": "intent", "rel": "配置/龙.txt",
                        "detail": {"op": "copy"}}, ensure_ascii=False)
    cut = len(line[: line.index("龙")].encode("utf-8")) + 2   # 切在「龙」第 2/3 字节
    (jobs_dir / "torn.jsonl").write_bytes(
        (start + "\n" + ok_intent + "\n").encode("utf-8")
        + line.encode("utf-8")[:cut])
    resp = client.get("/api/jobs/interrupted")                # 修复前:500→跳过→[]
    assert resp.status_code == 200
    items = resp.json()["items"]                              # 修复前:[](整档丢弃)
    assert [i["job_id"] for i in items] == ["torn"]
    assert [e["rel"] for e in items[0]["entries"]] == ["已完成的意图.txt"]
    resp2 = client.post("/api/jobs/interrupted/torn/dismiss", json={})
    assert resp2.status_code == 200                           # 可判定未收尾→可清除
    assert not (jobs_dir / "torn.jsonl").exists()


def test_dismiss_rejects_path_traversal_job_id(tmp_path, monkeypatch):
    """修复 A1(安全):dismiss 的 job_id 走形态白名单——URL 里的 ``%5C`` 在
    Windows 下解码出 ``\\`` 被 pathlib 当路径分隔符,修复前可删除 ``jobs/``
    之外任意 ``.json``/``.jsonl``(含 ``versions/<ver>.json`` 版本清单与
    ``launcher_profiles.json``);修复后 404 且目标文件原样保留。"""
    game, client = _make_client(tmp_path, monkeypatch)
    (game / ".mcmig" / "jobs").mkdir(parents=True)
    # 诱饵 1:jobs/ 同级 .mcmig 下的 .jsonl(顶层对象 JSON 可被当 journal 读)
    decoy = game / ".mcmig" / "decoy.jsonl"
    decoy.write_text("{}", encoding="utf-8")
    # 诱饵 2:版本清单(两层上跳,拟真多行 pretty JSON)
    ver = game / "versions" / "v1" / "v1.json"
    ver.parent.mkdir(parents=True, exist_ok=True)
    ver.write_text('{\n  "id": "v1"\n}', encoding="utf-8")
    for url in (
        "/api/jobs/interrupted/..%5Cdecoy/dismiss",
        "/api/jobs/interrupted/..%5C..%5Cversions%5Cv1%5Cv1/dismiss",
    ):
        assert client.post(url, json={}).status_code == 404
    assert decoy.exists(), "jobs/ 之外的诱饵文件不得被删除"
    assert ver.exists(), "版本清单不得被删除"


def test_job_endpoints_reject_malformed_job_id(tmp_path, monkeypatch):
    """修复 A1:状态/事件/取消三端点统一输入面——形态非法(分隔符/穿越形态)
    一律 404,与「job 不存在」同型应答。"""
    game, client = _make_client(tmp_path, monkeypatch)
    assert client.get("/api/jobs/..%5Cdecoy").status_code == 404
    assert client.get("/api/jobs/..%5Cdecoy/events").status_code == 404
    assert client.post("/api/jobs/..%5Cdecoy/cancel", json={}).status_code == 404
    assert client.get("/api/jobs/..").status_code == 404


def test_dismiss_rejects_windows_device_names(tmp_path, monkeypatch):
    """评审 M1:Windows 保留设备名(NUL/CON/COM1…)此前过白名单——``jobs/NUL.jsonl``
    被 Win32 解析为设备,exists() 真而 unlink() 抛 PermissionError → 500;
    现按「首个 '.' 前的段」排除(设备名规则),一律 404 不触盘。"""
    from migration.gui.server import _valid_job_id

    assert _valid_job_id("stale1") is True            # 正常条目不受影响
    for bad in ("NUL", "con", "COM1", "lpt9.bak", "aux.jsonl",
                "NUL  .jsonl", "CON .txt", "nul."):   # 尾随空格/点变体同为设备
        assert _valid_job_id(bad) is False, bad
    game, client = _make_client(tmp_path, monkeypatch)
    (game / ".mcmig" / "jobs").mkdir(parents=True)    # 设备名路径须真实可触达
    for name in ("NUL", "con", "COM1"):
        # 修复前:jobs/<设备名>.jsonl 触发 unlink PermissionError → 500
        assert client.post(f"/api/jobs/interrupted/{name}/dismiss", json={}).status_code == 404


def test_dismiss_unlink_failure_returns_conflict(tmp_path, monkeypatch):
    """评审 M1 补:删除阶段失败(设备变体/句柄占用等)不得 500——按 409 三段式
    呈现并提示手动删除;文件保留不误删。"""
    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    JobJournal(jobs_dir, "held", "migrate", src="s", dst="d",
               game_root=str(game)).record_intent("a.txt", {"op": "copy"})
    real_unlink = Path.unlink

    def _blocked(self: Path, *a: object, **k: object) -> None:
        if self.name == "held.jsonl":
            raise PermissionError(13, "被占用(注入)")
        real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _blocked)
    resp = client.post("/api/jobs/interrupted/held/dismiss", json={})
    assert resp.status_code == 409                    # 修复前:500
    assert {"what", "why", "details"} <= set(resp.json().keys())
    assert (jobs_dir / "held.jsonl").exists()         # 失败不误删


def test_dismiss_clears_non_ascii_journal_name(tmp_path, monkeypatch):
    """修复 M2:job_id 校验放宽为「单一路径分量」语义——用户手放的中文/空格名
    journal 档案(scan_interrupted 以 p.stem 列条目)此前「横幅可见但 dismiss
    恒 404」不可清除;现可正常清除(穿越/设备名仍拒,见相邻用例)。"""
    from urllib.parse import quote

    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    j = JobJournal(jobs_dir, "我的 迁移", "migrate", src="src", dst="dst",
                   game_root=str(game))
    j.record_intent("a.txt", {"op": "copy"})          # 未收尾 → 入中断清单
    items = client.get("/api/jobs/interrupted").json()["items"]
    assert [i["job_id"] for i in items] == ["我的 迁移"]   # 横幅可列出
    url = "/api/jobs/interrupted/" + quote("我的 迁移") + "/dismiss"
    assert client.post(url, json={}).status_code == 200        # 修复前:404(不可清除)
    assert not (jobs_dir / "我的 迁移.jsonl").exists()
    assert client.post(url, json={}).status_code == 404        # 清除后幂等 404


def test_dismiss_concurrent_delete_is_idempotent_success(tmp_path, monkeypatch):
    """复核 Minor:exists() 与 unlink() 之间档案被并发 dismiss(另一标签页/
    进程)抢先删除——清除目标已达成,按幂等成功收场;不得虚构「无法删除」
    冲突让用户去手动删除一个已经不在的文件。"""
    from migration.journal import JobJournal

    game, client = _make_client(tmp_path, monkeypatch)
    jobs_dir = game / ".mcmig" / "jobs"
    jobs_dir.mkdir(parents=True)
    JobJournal(jobs_dir, "raced", "migrate", src="s", dst="d",
               game_root=str(game)).record_intent("a.txt", {"op": "copy"})
    real_unlink = Path.unlink

    def _raced(self: Path, *a: object, **k: object) -> None:
        if self.name == "raced.jsonl":
            real_unlink(self, *a, **k)                # 并发方先删
            raise FileNotFoundError(2, "并发已删(注入)")
        real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _raced)
    resp = client.post("/api/jobs/interrupted/raced/dismiss", json={})
    assert resp.status_code == 200                    # 修复前:409(虚构冲突)
    assert resp.json() == {"ok": True}
    assert not (jobs_dir / "raced.jsonl").exists()


def test_legacy_plan_legacy_layout_snapshots_drift_blocked(tmp_path, monkeypatch):
    """评审 I1:review=None 计划 + 快照仅存旧布局时,执行侧兜底校验必须与 CLI
    同参(legacy_dir=mig_legacy)——漏传会让校验材料取位失败而静默跳过,旧布局
    的旧版计划在 GUI 仍零校验(弱于 CLI)。"""
    import json as _json

    from migration.plan import MigrationPlan
    from migration.review import plan_fingerprint

    # 旧布局快照目录须在 workdir 解析前存在(resolve 时探测,路径契约 v3)
    game = _make_game(tmp_path)
    (tmp_path / ".mcmig" / "snapshots").mkdir(parents=True)
    _game, client = _make_client(tmp_path, monkeypatch, game=game)
    r = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    done = _wait_job_done(client, r.json()["job_id"])[-1]
    assert done["type"] == "done"
    # 快照搬到旧布局(cwd/.mcmig/snapshots),锚定位不再存在
    anchored = game / ".mcmig" / "snapshots"
    legacy = tmp_path / ".mcmig" / "snapshots"
    for ver in ("src", "dst"):
        (anchored / f"{ver}.snapshot.json").rename(legacy / f"{ver}.snapshot.json")
    # 剥离审阅守卫(旧版计划)并复算指纹
    plan_file = game / ".mcmig" / "plans" / "src__dst.plan.json"
    payload = _json.loads(plan_file.read_text(encoding="utf-8"))
    payload["review"] = None
    plan_file.write_text(
        _json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    legacy_id = plan_fingerprint(MigrationPlan.load(plan_file))
    (game / "versions" / "src" / "options.txt").write_text("drift\n", encoding="utf-8")

    r2 = client.post("/api/migrate", json={"src": "src", "dst": "dst", "ask_yes": [],
                                           "plan_id": legacy_id})
    events = _wait_job_done(client, r2.json()["job_id"])
    assert events[-1]["type"] == "error", "旧布局快照 + 旧版计划漂移必须阻断(评审 I1)"
    assert events[-1]["details"]["code"] == "review_state_changed"
    assert (game / "versions" / "dst" / "options.txt").read_text(
        encoding="utf-8") == "fps:60\n"


# ---- 批次I-W3 T6:plan done 载荷增 diff 摘要(spec §5.1/T7) ----


def test_plan_done_carries_diff_summary(tmp_path, monkeypatch):
    """spec §5.1/T7:done 载荷带 diff 摘要——桶计数/体积/待确认/mod 配对/
    compat 警示(不再只进 stderr 日志)/client_only/世界提示/检测范围说明。"""
    game, client = _make_client(tmp_path, monkeypatch)
    (game / "versions" / "src" / "mods").mkdir()
    (game / "versions" / "src" / "mods" / "clientish-1.0.jar").write_bytes(b"jar")
    job = client.post("/api/plan", json={"src": "src", "dst": "dst"}).json()["job_id"]
    done = _wait_job_done(client, job)[-1]
    diff = done["diff"]
    for key in ("buckets", "total_bytes", "ask_count", "overwrite_count", "mod_pairs",
                "compat_warnings", "client_only", "world_notices", "guard_scope"):
        assert key in diff, f"done.diff 缺 {key}"
    assert diff["buckets"]["must_migrate"] >= 1 and diff["guard_scope"]


# ---- 批次I-W3 T7:swap 两阶段 API(preflight/apply + 指纹重验 + 取消窗,spec §5.2/§8) ----


def _swap_layout(tmp_path, monkeypatch) -> tuple[Path, TestClient]:
    """swap 两阶段公共布局:src(旧包独有 jar+options.txt)/dst(版本 json,
    mods 空)/new_pack(含 newpack.jar);经 _make_client 注入 workdir。"""
    game = tmp_path / "game"
    src = game / "versions" / "src"
    src.mkdir(parents=True)
    (src / "options.txt").write_text("fps:120\n", encoding="utf-8")
    (src / "mods").mkdir()
    (src / "mods" / "oldpack-only.jar").write_bytes(b"OLD")
    dst = game / "versions" / "dst"
    dst.mkdir()
    (dst / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    (dst / "mods").mkdir()
    pack = tmp_path / "newpack"
    (pack / "mods").mkdir(parents=True)
    (pack / "mods" / "newpack.jar").write_bytes(b"NEW")
    return _make_client(tmp_path, monkeypatch, game=game)


def _swap_preflight_done(client: TestClient, game: Path) -> dict:
    """跑完 preflight job,返回 done 载荷(含 preflight_id 与三类决策清单)。

    new_pack 用 _swap_layout 的固定位:<tmp>/newpack(与 game 同级)。
    """
    job = client.post("/api/swap/preflight", json={
        "src": "src", "dst": "dst",
        "new_pack": str(game.parent / "newpack"),
    }).json()["job_id"]
    events = _wait_job_done(client, job)
    done = events[-1]
    assert done["type"] == "done", done
    return done


def _apply_body(pf: dict) -> dict:
    """apply 请求体:以 preflight_id 携带全部决策(默认全拒/零覆盖)。"""
    return {"preflight_id": pf["preflight_id"], "src": "src", "dst": "dst",
            "accept_incompat": False, "accept_extras": False, "overwrite_jars": []}


def test_swap_apply_rejects_changed_inputs(tmp_path, monkeypatch):
    """两阶段指纹重校验:preflight 后篡改目标 mods/(新增 jar)→ apply 拒
    (swap_inputs_changed),零写盘。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    (game / "versions" / "dst" / "mods" / "intruder.jar").write_bytes(b"X")
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err["code"] == "swap_inputs_changed"
    assert not (game / "versions" / "dst" / "mods" / "newpack.jar").exists()


def test_swap_apply_rejects_switched_game_root(tmp_path, monkeypatch):
    """评审 P2-3:preflight 后切换游戏根(POST /api/config)再 apply → 拒
    (swap_inputs_changed:指纹含规范化 game_root,跨根必失配)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    other = tmp_path / "other_root"
    # 完整版本对(评审 v4 P2-4:只有 dst 会先被 apply 的版本校验 422 挡下,
    # 触不到指纹失配分支)
    for name in ("src", "dst"):
        (other / "versions" / name).mkdir(parents=True)
    (other / "versions" / "src" / "options.txt").write_text("x\n", encoding="utf-8")
    (other / "versions" / "dst" / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.228"]}}',
        encoding="utf-8")
    assert client.post("/api/config", json={"game_root": str(other)}).status_code == 200
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err["code"] == "swap_inputs_changed"


def test_swap_apply_rejects_changed_version_json(tmp_path, monkeypatch):
    """评审 P2-3:preflight 后修改目标 <dst>.json → apply 拒(NeoForge 兼容
    判定的输入已变,兼容检查须重跑,不能沿用预检结论)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    (game / "versions" / "dst" / "dst.json").write_text(
        '{"arguments": {"game": ["--fml.neoforgeVersion", "21.1.999"]}}',
        encoding="utf-8")
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err["code"] == "swap_inputs_changed"
    assert not (game / "versions" / "dst" / "mods" / "newpack.jar").exists()


def test_swap_apply_chains_modpack_swap_replan(tmp_path, monkeypatch):
    """评审 P1-2:apply 装包后同 job 链式重扫+规划(modpack_swap=True)——done
    载荷与 plan job 同形(plan_id/persisted/diff);随后从该计划 migrate,
    旧包独有 jar 不回迁(源独有 mod 归换包排除,非 must_migrate)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    done = _wait_job_done(client, job)[-1]
    assert done["type"] == "done" and done["job_kind"] == "swap"
    assert done["plan_id"] and done["persisted"] is True
    assert done["install"]["copied"] == 1
    # done.plan 是 _group_actions 分组 {origin: {actions: [...]}}(评审 v4 P2-4:
    # 无扁平 actions 键)——遍历各组的 actions
    moved = {a["path"]
             for group in done["plan"].values()
             for a in group["actions"]
             if a["origin"] in ("must_migrate", "mod_added")}
    assert "mods/oldpack-only.jar" not in moved        # 换包排除,非可迁移
    mig = client.post("/api/migrate", json={
        "plan_id": done["plan_id"], "src": "src", "dst": "dst",
        "ask_yes": []}).json()["job_id"]
    _wait_job_done(client, mig)
    assert (game / "versions" / "dst" / "mods" / "newpack.jar").exists()
    assert not (game / "versions" / "dst" / "mods" / "oldpack-only.jar").exists()


def test_swap_cancel_window_closes_before_replan(tmp_path, monkeypatch):
    """评审 v3 P2-3:装包完成后取消窗原子关闭——重扫/规划阶段的 cancel 请求
    409,job 以携带计划的 done 正常收口(封死「done 带成功计划 vs GET 收口
    cancelled」的分歧)。"""
    import threading
    import time

    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    replan_started = threading.Event()
    real_build = server_module.build_plan

    def slow_build(*a, **k):
        replan_started.set()
        time.sleep(0.5)                                # 留出取消请求的观察窗
        return real_build(*a, **k)

    monkeypatch.setattr(server_module, "build_plan", slow_build)
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    assert replan_started.wait(timeout=10.0), "未进入链式重规划阶段"
    resp = client.post(f"/api/jobs/{job}/cancel", json={})
    assert resp.status_code == 409                     # 取消窗已关(白名单⑩)
    done = _wait_job_done(client, job)[-1]
    assert done["type"] == "done" and done.get("plan_id")


def test_cancel_endpoint_rechecks_closed_window_in_lock(tmp_path, monkeypatch):
    """评审 v4 P2-3:取消端点不得依赖锁外 can_cancel 读——白盒直接构造
    cancel_closed=True 的 swap job(关窗瞬间的形态),取消 409 且不置
    cancelling(锁内重检;若只在锁外判定,此形态会 202 并污染终态)。"""
    _game, client = _swap_layout(tmp_path, monkeypatch)
    from migration.gui.server import Job

    job = Job("swapclosed", "swap")
    job.cancel_closed = True                           # 白盒:直接构造关窗形态
    store = client.app.state.jobs                      # T7 暴露(app.state.jobs)
    store._jobs["swapclosed"] = job
    resp = client.post("/api/jobs/swapclosed/cancel", json={})
    assert resp.status_code == 409
    assert job.status != "cancelling"                  # 未被置为取消中


def test_swap_apply_replan_failure_reports_install_state(tmp_path, monkeypatch):
    """评审 v3 契约A/B:装包成功而链式规划失败——error 载荷带 install 统计与
    backup_dir(用户须知目标 mods/ 已被修改,不可静默只报规划失败)。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    # 冲突 jar 必须在 preflight **前**于两侧创建(评审 v4 P2-4:指纹覆盖两侧
    # jar 清单,预检后再加 victim.jar 会先触发 swap_inputs_changed;且新包
    # 无同名 jar 则根本没有可覆盖冲突)
    (game / "versions" / "dst" / "mods" / "victim.jar").write_bytes(b"OLD")
    (game.parent / "newpack" / "mods" / "victim.jar").write_bytes(b"NEW")
    pf = _swap_preflight_done(client, game)
    assert "victim.jar" in pf["conflicts"]             # 冲突已被预检识别

    def _boom(*a, **k):
        raise ValueError("规划失败(模拟)")

    monkeypatch.setattr(server_module, "build_plan", _boom)
    job = client.post("/api/swap/apply", json={
        **_apply_body(pf), "overwrite_jars": ["victim.jar"]}).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err.get("code") == "swap_replan_failed"
    assert err["install"]["copied"] >= 1 and err["backup_dir"]
    assert (game / "versions" / "dst" / "mods" / "newpack.jar").exists()
    backup = Path(err["backup_dir"])
    assert (backup / "victim.jar").read_bytes() == b"OLD"


def test_job_submission_rejected_while_draining(tmp_path, monkeypatch):
    """评审 v3 P2-5:begin_shutdown 置 draining 后,新 job 提交 503(plan/
    migrate/swap 三入口同一守卫,此处以 plan 代表)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    client.app.state.jobs.begin_shutdown()
    resp = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert resp.status_code == 503


def test_job_submission_rejected_when_drain_lands_during_start(tmp_path, monkeypatch):
    """评审 v4 P2-2:draining 判定必须在 JobStore.start() 的临界区内——以
    「drain-then-start」交错注入模拟「端点过检→关窗→注册」的真实交错:
    判定仅在端点锁外预检时,本交错会照常建 job(红);锁内首判 → 503(绿)。"""
    from migration.gui.server import JobStore

    game, client = _make_client(tmp_path, monkeypatch)
    real_start = JobStore.start

    def _drain_then_start(self, *a, **k):
        self.begin_shutdown()  # 注册前一瞬关窗(交错点)
        return real_start(self, *a, **k)

    monkeypatch.setattr(JobStore, "start", _drain_then_start)
    resp = client.post("/api/plan", json={"src": "src", "dst": "dst"})
    assert resp.status_code == 503


def test_version_name_error_single_component_only():
    """评审(0.12.0 复审#1):版本名必须是「单一路径分量」——绝对路径/盘符/
    分隔符/`.`/`..`/通配符/控制字符/尾随空白点一律非法;中文等正常名放行。"""
    from migration.pipeline import version_name_error

    assert version_name_error("1.21.1-NeoForge_21.1.228") is None
    assert version_name_error("我的版本") is None
    for bad in ("", ".", "..", "a/b", "a\\b", "/abs", "C:\\v", "C:v",
                "\\\\srv\\share", "a\nb", " a", "a ", "a.", "a*", "a?", "a|"):
        assert version_name_error(bad) is not None, bad


def test_version_inputs_reject_out_of_root_names(tmp_path, monkeypatch):
    """评审(0.12.0 复审#1):版本名只作单一路径分量拼接——修复前
    ``_ensure_version_dirs`` 只查 ``(versions/<v>).is_dir()``,pathlib 拼绝对
    路径时整体替换,外部已存在目录即通过受理(preflight 202/后续写盘落在
    game_root 之外);修复后 plan/migrate/swap preflight/apply 四端点对
    绝对路径与穿越形态一律 422,目标侧零写盘。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    (outside / "mods").mkdir(parents=True)
    (outside / "mods" / "victim.jar").write_bytes(b"V")
    pack = str(game.parent / "newpack")
    # 绝对路径(修复前 is_dir 通过 → 202 受理,写盘可落在 game_root 外)
    assert client.post("/api/swap/preflight", json={
        "src": "src", "dst": str(outside), "new_pack": pack}).status_code == 422
    # 穿越形态(相对 versions/ 逃出 game_root)
    assert client.post("/api/swap/preflight", json={
        "src": "src", "dst": "../outside", "new_pack": pack}).status_code == 422
    assert client.post("/api/plan", json={
        "src": str(outside), "dst": "dst"}).status_code == 422
    assert client.post("/api/migrate", json={
        "src": "src", "dst": str(outside), "ask_yes": [], "dry_run": False,
        "plan_id": "x"}).status_code == 422
    assert (outside / "mods" / "victim.jar").read_bytes() == b"V"  # 零写盘


def test_write_guard_checks_origin_and_content_type(tmp_path, monkeypatch):
    """评审(0.12.0 复审#11):写端点(POST)除 Host 外还须过 Origin 白名单与
    JSON Content-Type——修复前带外部 Origin、无 Content-Type 的停机 POST 仍
    200(浏览器 CSRF 可盲打:Host 由浏览器按目标生成必过,no-cors text/plain
    可携 JSON 体);同源/无 Origin 的 JSON 请求不受影响(非浏览器工具)。"""
    game, client = _make_client(tmp_path, monkeypatch)
    # 外部 Origin(浏览器不可伪造头):403,不触端点
    r = client.post("/api/shutdown", json={},
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    # Origin 白名单内的端口变体:放行到端点本体(此处为 200 ok)
    r_ok = client.post("/api/shutdown", json={},
                       headers={"Origin": "http://127.0.0.1:1"})
    assert r_ok.status_code == 200 and r_ok.json()["ok"] is True
    # Content-Type 非 JSON(CSRF no-cors 只能发简单类型):415
    (tmp_path / "second").mkdir()
    _, client2 = _make_client(tmp_path / "second", monkeypatch)
    r2 = client2.post("/api/shutdown", content=b"{}",
                      headers={"Content-Type": "text/plain"})
    assert r2.status_code == 415
    # 缺 Content-Type 的裸 POST(修复前直达端点):415
    r3 = client2.post("/api/shutdown", content=b"")
    assert r3.status_code == 415


def test_startup_survives_unsweepable_finished_journal(tmp_path, monkeypatch, caplog):
    """评审(0.12.0 复审#4)启动面:应用工厂同步清扫 finished journal——清扫
    失败(只读文件)不得阻断 create_app,GUI 照常起、横幅清单照常空。"""
    import os

    from migration.journal import JobJournal

    game = _make_game(tmp_path)
    jobs = game / ".mcmig" / "jobs"
    jobs.mkdir(parents=True)
    JobJournal(jobs, "stale-done", "migrate", src="s", dst="d",
               game_root=str(game)).finish()
    locked = jobs / "stale-done.jsonl"
    # 制造「不可清扫」(与 test_journal 同策):Windows=只读文件;
    # POSIX=只读父目录(unlink 权限在目录位),两平台同走 PermissionError 路径
    if os.name == "nt":
        os.chmod(locked, 0o444)
    else:
        os.chmod(jobs, 0o555)
    try:
        import migration.workdir as wd

        monkeypatch.setattr(wd, "_is_frozen", lambda: False)
        monkeypatch.chdir(tmp_path)
        w = wd.resolve_workdir(game_root=game)
        w.save_game_root(game)
        with caplog.at_level("WARNING", logger="migration.journal"):
            client = TestClient(create_app(workdir=w), base_url="http://127.0.0.1")
        assert client.get("/api/jobs/interrupted").json()["items"] == []
        assert locked.exists()
        assert "stale-done" in caplog.text
    finally:
        if os.name == "nt":
            os.chmod(locked, 0o666)
        else:
            os.chmod(jobs, 0o755)


def test_swap_apply_install_failure_reports_partial_results(tmp_path, monkeypatch):
    """评审(0.12.0 复审#5)GUI 消费面:装包中途失败(空间/文件操作)不再以
    异常吞掉已知结果——error 事件携带 install 部分统计(copied/backed_up)与
    backup_dir(用户须能判断「已改动什么、原件在哪」),code 区分于规划失败。"""
    import migration.gui.server as srv
    from migration.pipeline import SwapInstallOutcome

    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    bk = tmp_path / "bk"
    monkeypatch.setattr(
        srv, "swap_install",
        lambda *a, **k: SwapInstallOutcome(1, 0, 1, ["victim.jar"], bk, False,
                                           "磁盘空间不足:需要 9.9 MB,剩余 1.0 MB"))
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    err = _wait_job_done(client, job)[-1]
    assert err["type"] == "error" and err["code"] == "swap_install_failed"
    assert err["install"]["copied"] == 1                       # 已知部分结果保留
    assert err["install"]["backed_up"] == ["victim.jar"]
    assert "不足" in err["install"]["error"]
    # 备份位置直达(job 真实备份根 = <game>/.mcmig/backups/swap/<UTC 时间戳>)
    bd = Path(err["backup_dir"])
    assert "swap" in bd.parts and "backups" in bd.parts
    assert {"what", "why"} <= set(err.keys())                  # 三段式文案齐备


def test_sse_generator_unsubscribes_promptly_on_cancellation():
    """评审(0.12.0 复审#7):客户端断开订阅须及时释放——修复前 SSE 泵是同步
    生成器,``queue.get(0.5s)`` 阻塞在线程池线程,断开时的任务取消传不进
    同步迭代器,``finally unsubscribe`` 要等下一事件/终态才跑(订阅滞留
    ``job._subs``);改 async 泵 + ``asyncio.to_thread`` 拉取后,取消在 await
    点即时生效,finally 即刻注销。"""
    import asyncio
    from contextlib import suppress

    from migration.gui.server import Job, _sse_gen

    async def _scenario() -> tuple[int, float]:
        job = Job("j1", "migrate")
        job.emit({"type": "phase", "name": "scan"})   # seq=1 入历史
        got_first = asyncio.Event()

        async def _consume() -> None:
            # Starlette 形态:取消发生在「等下一帧」的 __anext__ 内部
            # (客户端断开时流正阻塞在取帧),而非消费侧 sleep
            frames = _sse_gen(job, 0)
            await frames.__anext__()          # 首帧(重放段)
            got_first.set()
            await frames.__anext__()          # 无新事件:持续等待(断开即在此)

        t0 = asyncio.get_running_loop().time()
        task = asyncio.create_task(_consume())
        await asyncio.wait_for(got_first.wait(), timeout=2)
        assert len(job._subs) == 1                    # 消费中:订阅在册
        task.cancel()                                 # 客户端断开(Starlette 取消)
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)   # 修复前:取消传不进 → 超时抛错
        return len(job._subs), asyncio.get_running_loop().time() - t0

    subs_left, elapsed = asyncio.run(_scenario())
    assert subs_left == 0                             # finally 即刻注销
    assert elapsed < 2                                # 未等事件/终态


# ---- Task 9:更新端点与 update job 四件契约(spec §4.3.4) ----


def _wait_status(client: TestClient, job_id: str, statuses: set[str], tries: int = 300) -> dict:
    """轮询 GET 快照直到状态命中集合(默认 3s 窗;超时抛断言)。"""
    import time as _t

    snap: dict = {}
    for _ in range(tries):
        snap = client.get(f"/api/jobs/{job_id}").json()
        if snap["status"] in statuses:
            return snap
        _t.sleep(0.01)
    raise AssertionError(f"job 未达状态 {statuses}: {snap}")


def test_update_job_is_cancellable_and_progress_in_snapshot():
    """update job 四件之二:可取消;progress 事件进 GET 快照(progress_bytes)。"""
    from migration.gui.server import Job

    job = Job("t1", "update")
    assert job.can_cancel is True
    assert Job("t2", "plan").can_cancel is False
    job.emit({"type": "progress", "received": 10, "total": 100})
    snap = job.snapshot()
    assert snap["progress_bytes"] == {"received": 10, "total": 100}


def test_update_check_source_mode(tmp_path, monkeypatch):
    from migration import _form

    monkeypatch.setattr(_form, "FORM", "source")
    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/update/check", json={})
    assert r.status_code == 200 and r.json()["mode"] == "source"
    assert r.json()["form"] == "source"   # 评审④ P2-2:形态随检查结果下发


def test_update_check_three_states(tmp_path, monkeypatch):
    """三态:发现新版(tag/asset/size)/已是最新/失败(error 三段式)。"""
    from migration import _form, updater

    monkeypatch.setattr(_form, "FORM", "onefile")
    _game, client = _make_client(tmp_path, monkeypatch)
    rel = updater.ReleaseInfo("v0.13.1", (
        updater.AssetInfo("mcmig-gui-0.13.1-win-x64.exe", 31, "u"),
        updater.AssetInfo("SHA256SUMS.txt", 1, "s"),
    ))
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: rel)
    import migration

    monkeypatch.setattr(migration, "__version__", "0.12.0")
    body = client.post("/api/update/check", json={}).json()
    assert body["newer"] is True and body["asset_name"] == "mcmig-gui-0.13.1-win-x64.exe"
    assert body["size"] == 31 and body["latest"] == "0.13.1"
    assert body["form"] == "onefile"   # 评审④ P2-2:页面据此选形态化替换指引
    monkeypatch.setattr(updater, "fetch_latest_release", lambda: None)
    body = client.post("/api/update/check", json={}).json()
    # 无任何发布:newer=False 且 latest=None(页面据 latest 呈「尚无发布」)
    assert body["newer"] is False and body["latest"] is None

    def _boom():
        raise updater.UpdateError("更新检查失败", "网络不可达")

    monkeypatch.setattr(updater, "fetch_latest_release", _boom)
    body = client.post("/api/update/check", json={}).json()
    assert body["error"]["what"] == "更新检查失败"


def test_update_check_proxy_html_structured_error(tmp_path, monkeypatch):
    """评审④ P2-6:GUI 检查端遇代理 HTML(200)返回结构化三段式,不是 500/裸异常。"""
    import httpx

    from migration import _form, updater

    monkeypatch.setattr(_form, "FORM", "onefile")
    monkeypatch.setattr(
        updater, "_open_client",
        lambda: httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, content=b"<html>login</html>",
                                           headers={"content-type": "text/html"})),
            follow_redirects=True))
    _game, client = _make_client(tmp_path, monkeypatch)
    r = client.post("/api/update/check", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["error"]["what"] == "更新检查失败" and "JSON" in body["error"]["why"]


def test_update_download_job_done_payload(tmp_path, monkeypatch):
    """update job 四件之三:done 载荷=UpdatePlan 全字段(与 GET 同源)。"""
    from migration import _form, updater
    import migration.gui.server as srv

    monkeypatch.setattr(_form, "FORM", "onefile")
    game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv, "_update_staging_root", lambda: game / "up-staging")
    plan = updater.UpdatePlan("0.13.1", "a.exe", game / "up-staging" / "u" / "a.exe", 7, "ab")
    monkeypatch.setattr(updater, "plan_update",
                        lambda root, *, progress_cb=None, should_cancel=None: plan)
    r = client.post("/api/update/download", json={})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    events = _wait_job_done(client, job_id)
    done = events[-1]
    assert done["type"] == "done" and done["job_kind"] == "update"
    snap = client.get(f"/api/jobs/{job_id}").json()
    assert snap["done"]["version"] == "0.13.1" and snap["done"]["sha256"] == "ab"
    assert snap["done"]["form"] == "onefile"   # 评审④ P2-2:刷新恢复路同源携带形态


def test_update_download_cancelled_terminal(tmp_path, monkeypatch):
    """取消:should_cancel 命中 → cancelled 终态(done.cancelled=True)。"""
    import time as _t

    from migration import _form, updater
    import migration.gui.server as srv

    monkeypatch.setattr(_form, "FORM", "onefile")
    game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv, "_update_staging_root", lambda: game / "up-staging")

    def _fake_plan(root, *, progress_cb=None, should_cancel=None):
        while should_cancel is not None and not should_cancel():
            _t.sleep(0.01)
            if progress_cb:
                progress_cb(1, 10)
        raise updater.UpdateCancelled()

    monkeypatch.setattr(updater, "plan_update", _fake_plan)
    r = client.post("/api/update/download", json={})
    job_id = r.json()["job_id"]
    _wait_status(client, job_id, {"running"})
    client.post(f"/api/jobs/{job_id}/cancel", json={})
    snap = _wait_status(client, job_id, {"cancelled", "failed", "succeeded", "partial_failed"})
    assert snap["status"] == "cancelled" and snap["done"]["cancelled"] is True


def test_update_open_location_rejects_outside_staging(tmp_path, monkeypatch):
    import migration.gui.server as srv

    _game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv, "_update_staging_root", lambda: tmp_path)
    r = client.post("/api/update/open-location", json={"path": str(tmp_path.parent / "evil.exe")})
    assert r.status_code == 400


def test_update_open_location_uses_explorer_select(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("migration.gui.server.subprocess.run",
                        lambda cmd, check=None: calls.append(cmd))
    import migration.gui.server as srv

    _game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv, "_update_staging_root", lambda: tmp_path)
    target = tmp_path / "u" / "a.exe"
    target.parent.mkdir()
    target.write_bytes(b"")
    r = client.post("/api/update/open-location", json={"path": str(target)})
    assert r.status_code == 200
    assert calls and calls[0][0] == "explorer" and calls[0][1] == "/select,"


# ---- 评审② 修复波:update runner 防御性收尾与 settle ----


def test_update_job_unexpected_error_settles(tmp_path, monkeypatch):
    """未预期异常(RuntimeError)不得让 job 永挂 running:error 终态+单锁释放。"""
    from migration import _form, updater
    import migration.gui.server as srv

    monkeypatch.setattr(_form, "FORM", "onefile")
    game, client = _make_client(tmp_path, monkeypatch)
    monkeypatch.setattr(srv, "_update_staging_root", lambda: game / "up-staging")

    def _boom(root, *, progress_cb=None, should_cancel=None):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(updater, "plan_update", _boom)
    r = client.post("/api/update/download", json={})
    job_id = r.json()["job_id"]
    snap = _wait_status(client, job_id, {"failed", "succeeded", "cancelled", "partial_failed"})
    assert snap["status"] == "failed" and snap["done"]["job_kind"] == "update"
    # 单锁已释放:后续任务可启动(不再 409)
    monkeypatch.setattr(updater, "plan_update",
                        lambda root, *, progress_cb=None, should_cancel=None: None)
    r2 = client.post("/api/update/download", json={})
    assert r2.status_code == 202


def test_update_cancel_after_plan_settles_with_cleanup(tmp_path, monkeypatch):
    """取消落在「校验完成→收尾」窗口:settle 单次判定 → cancelled+清理本次暂存,
    绝不出现 done 带成功产物而 GET 报 cancelled 的矛盾(评审② I3,与 migrate I2 同规)。"""
    import time as _t

    from migration import _form, updater
    import migration.gui.server as srv

    monkeypatch.setattr(_form, "FORM", "onefile")
    game, client = _make_client(tmp_path, monkeypatch)
    staging = game / "up-staging"
    monkeypatch.setattr(srv, "_update_staging_root", lambda: staging)

    def _fake_plan(root, *, progress_cb=None, should_cancel=None):
        while should_cancel is not None and not should_cancel():
            _t.sleep(0.01)
        sub = root / "u1"
        sub.mkdir(parents=True)
        p = sub / "a.exe"
        p.write_bytes(b"x")
        return updater.UpdatePlan("9.9.9", "a.exe", p, 1, "ab")

    monkeypatch.setattr(updater, "plan_update", _fake_plan)
    r = client.post("/api/update/download", json={})
    job_id = r.json()["job_id"]
    _wait_status(client, job_id, {"running"})
    client.post(f"/api/jobs/{job_id}/cancel", json={})
    snap = _wait_status(client, job_id, {"cancelled", "failed", "succeeded", "partial_failed"})
    assert snap["status"] == "cancelled" and snap["done"]["cancelled"] is True
    assert not (staging / "u1").exists()   # 本次暂存已清理(仅本任务子目录)


def test_swap_apply_emits_file_events_per_jar(tmp_path, monkeypatch):
    """T13.5:GUI 装包阶段逐 jar 产生 file 事件(index/total)——复用既有进度通道。"""
    game, client = _swap_layout(tmp_path, monkeypatch)
    pf = _swap_preflight_done(client, game)
    job = client.post("/api/swap/apply", json=_apply_body(pf)).json()["job_id"]
    events = _wait_job_done(client, job)
    files = [ev for ev in events if ev.get("type") == "file"]
    assert files, "装包阶段必须产生逐 jar file 事件(进度可见)"
    assert [f["index"] for f in files] == list(range(1, len(files) + 1))
    assert all(f["total"] == len(files) for f in files)
    # 终审 C?I1:file 事件必须带 status——缺省会让页面渲染「(undefined)」
    assert all(f.get("status") == "copied" for f in files)
