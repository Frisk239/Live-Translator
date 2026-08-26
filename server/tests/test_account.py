import time

from fastapi.testclient import TestClient

from account import create_app
from store import SqliteStore


def client(**kw):
    return TestClient(create_app(store=SqliteStore(":memory:"), **kw))


def register(c, email="a@b.c", password="secret12"):
    res = c.post("/account/register", json={"email": email, "password": password})
    assert res.status_code == 200
    return res.json()["token"]


def test_register_then_login():
    c = client()
    created = c.post(
        "/account/register",
        json={"email": "a@b.c", "password": "secret12", "rememberMe": True},
    )
    assert created.status_code == 200
    assert created.json()["email"] == "a@b.c"
    assert created.json()["token"]

    again = c.post(
        "/account/login",
        json={"email": "a@b.c", "password": "secret12", "rememberMe": False},
    )
    assert again.status_code == 200
    assert again.json()["email"] == "a@b.c"


def test_register_duplicate_email():
    c = client()
    c.post("/account/register", json={"email": "a@b.c", "password": "secret12"})
    dup = c.post("/account/register", json={"email": "A@b.c", "password": "other"})
    assert dup.status_code == 409
    assert "登录" in dup.json()["error"]


def test_register_needs_email_and_password():
    c = client()
    bad = c.post("/account/register", json={"email": "not-an-email", "password": "x"})
    assert bad.status_code == 400


def test_login_wrong_password():
    c = client()
    register(c)
    bad = c.post("/account/login", json={"email": "a@b.c", "password": "nope"})
    assert bad.status_code == 401
    assert "密码" in bad.json()["error"]


def test_listen_rejects_bad_token():
    c = client()
    with c.websocket_connect("/listen") as ws:
        ws.send_json({"type": "auth", "token": "nope"})
        notice = ws.receive_json()
        assert notice["type"] == "notice"
        assert notice["kind"] == "auth"


def test_listen_accepts_login_token():
    c = client()
    token = register(c)
    with c.websocket_connect("/listen") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.send_json({"type": "start", "source": "system", "translate": "ct2"})
        ws.send_json({"type": "stop"})


def test_listen_start_after_auth_without_models():
    """鉴权后 start 即收下，不要求本机已有模型。"""
    c = client()
    token = register(c)
    with c.websocket_connect("/listen") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.send_json({"type": "start", "source": "system", "translate": "llm"})
        ws.send_json({"type": "switch", "source": "system"})
        ws.send_json({"type": "stop"})


# ---------- 顶号：同一账号后开顶先开（CONTEXT.md / ADR 0020） ----------


def test_same_account_second_listen_kicks_first():
    c = client()
    token = register(c)
    with c.websocket_connect("/listen") as ws1:
        ws1.send_json({"type": "auth", "token": token})
        with c.websocket_connect("/listen") as ws2:
            ws2.send_json({"type": "auth", "token": token})
            ws2.send_json({"type": "start", "source": "system", "translate": "ct2"})
            # 先开的那路收到被顶提示，不再是含糊的掉线
            assert ws1.receive_json() == {"type": "notice", "kind": "kicked"}
            ws2.send_json({"type": "stop"})


# ---------- 满员：跨账号只看路数，新开被拒、已开不动（ADR 0016） ----------


def test_full_rejects_second_account():
    c = client(max_routes=1)
    ta = register(c, "a@b.c")
    tb = register(c, "x@y.z")
    with c.websocket_connect("/listen") as ws1:
        ws1.send_json({"type": "auth", "token": ta})
        with c.websocket_connect("/listen") as ws2:
            ws2.send_json({"type": "auth", "token": tb})
            assert ws2.receive_json() == {"type": "notice", "kind": "full"}


def test_same_account_reopen_not_blocked_by_own_route():
    """路数满 1 时，同账号再开一路靠顶号让位，不撞满员。"""
    c = client(max_routes=1)
    token = register(c)
    with c.websocket_connect("/listen") as ws1:
        ws1.send_json({"type": "auth", "token": token})
        with c.websocket_connect("/listen") as ws2:
            ws2.send_json({"type": "auth", "token": token})
            assert ws1.receive_json() == {"type": "notice", "kind": "kicked"}
            ws2.send_json({"type": "start", "source": "system", "translate": "ct2"})
            ws2.send_json({"type": "stop"})


# ---------- 改密码：全部 token 作废，其它电脑在听要重新登录（ADR 0028） ----------


def test_password_change_revokes_other_tokens():
    c = client()
    register(c)
    t1 = c.post("/account/login", json={"email": "a@b.c", "password": "secret12"}).json()["token"]
    t2 = c.post("/account/login", json={"email": "a@b.c", "password": "secret12"}).json()["token"]
    res = c.post(
        "/account/password",
        json={"token": t1, "oldPassword": "secret12", "newPassword": "newpass34"},
    )
    assert res.status_code == 200
    new_token = res.json()["token"]
    # 旧 token 全作废，新 token 可用
    assert c.post("/account/session", json={"token": t1}).status_code == 401
    assert c.post("/account/session", json={"token": t2}).status_code == 401
    assert c.post("/account/session", json={"token": new_token}).status_code == 200
    # 旧密码登不上，新密码可以
    assert c.post("/account/login", json={"email": "a@b.c", "password": "secret12"}).status_code == 401
    assert c.post("/account/login", json={"email": "a@b.c", "password": "newpass34"}).status_code == 200


def test_password_change_requires_old_password():
    c = client()
    t = register(c)
    res = c.post(
        "/account/password",
        json={"token": t, "oldPassword": "wrong", "newPassword": "newpass34"},
    )
    assert res.status_code == 401
    assert "旧密码" in res.json()["error"]


def test_password_change_kicks_other_device_listen():
    c = client()
    register(c)
    t1 = c.post("/account/login", json={"email": "a@b.c", "password": "secret12"}).json()["token"]
    t2 = c.post("/account/login", json={"email": "a@b.c", "password": "secret12"}).json()["token"]
    with c.websocket_connect("/listen") as ws2:
        ws2.send_json({"type": "auth", "token": t2})
        res = c.post(
            "/account/password",
            json={"token": t1, "oldPassword": "secret12", "newPassword": "newpass34"},
        )
        assert res.status_code == 200
        # 其它电脑正在开听：停听并说明要重新登录（不是「已在别处开听」）
        assert ws2.receive_json() == {"type": "notice", "kind": "auth"}


# ---------- 退出：只作废这枚 token ----------


def test_logout_revokes_token():
    c = client()
    t = register(c)
    assert c.post("/account/session", json={"token": t}).status_code == 200
    assert c.post("/account/logout", json={"token": t}).status_code == 200
    assert c.post("/account/session", json={"token": t}).status_code == 401
    with c.websocket_connect("/listen") as ws:
        ws.send_json({"type": "auth", "token": t})
        assert ws.receive_json() == {"type": "notice", "kind": "auth"}


# ---------- 登录防爆破：按来源暂拒，不锁账号（ADR 0030） ----------


def test_login_throttle_by_source():
    c = client(login_max_fails=2, login_window_s=60, login_cooldown_s=60)
    register(c)
    for _ in range(2):
        assert c.post("/account/login", json={"email": "a@b.c", "password": "bad"}).status_code == 401
    refused = c.post("/account/login", json={"email": "a@b.c", "password": "secret12"})
    assert refused.status_code == 429
    assert "勤" in refused.json()["error"]


# ---------- 空闲超时：没动静当断开、放名额（ADR 0029） ----------


def test_idle_timeout_frees_route():
    c = client(max_routes=1, idle_timeout=0.3)
    ta = register(c, "a@b.c")
    tb = register(c, "x@y.z")
    with c.websocket_connect("/listen") as ws1:
        ws1.send_json({"type": "auth", "token": ta})
        time.sleep(0.9)  # 静默到点：服务端收掉这路
        with c.websocket_connect("/listen") as ws2:
            ws2.send_json({"type": "auth", "token": tb})
            ws2.send_json({"type": "start", "source": "system", "translate": "ct2"})
            ws2.send_json({"type": "stop"})


# ---------- 优雅退出：停接新路，与满员同一句说明（ADR 0026） ----------


def test_drain_rejects_new_listens():
    c = client()
    t = register(c)
    c.app.state.routes.draining = True
    with c.websocket_connect("/listen") as ws:
        ws.send_json({"type": "auth", "token": t})
        assert ws.receive_json() == {"type": "notice", "kind": "full"}


# ---------- 反代真实 IP：信任代理时限流按 XFF 最右一跳（ADR 0030 后续） ----------


def test_login_throttle_uses_xff_rightmost_when_trusting_proxy():
    c = client(
        login_max_fails=2,
        login_window_s=60,
        login_cooldown_s=60,
        trust_proxy=True,
    )
    register(c)
    bad = {"email": "a@b.c", "password": "bad"}
    # 同一个真实客户端（XFF 最右 1.2.3.4）：两次失败后被暂拒
    for _ in range(2):
        assert (
            c.post("/account/login", json=bad, headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4"}).status_code
            == 401
        )
    refused = c.post(
        "/account/login",
        json={"email": "a@b.c", "password": "secret12"},
        headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4"},
    )
    assert refused.status_code == 429
    # 另一个客户端不受前一个的连累
    other = c.post(
        "/account/login",
        json={"email": "a@b.c", "password": "secret12"},
        headers={"X-Forwarded-For": "9.9.9.9, 5.6.7.8"},
    )
    assert other.status_code == 200


def test_login_throttle_ignores_xff_without_trust():
    c = client(login_max_fails=2, login_window_s=60, login_cooldown_s=60)
    register(c)
    bad = {"email": "a@b.c", "password": "bad"}
    # 不信任代理时 XFF 换着花样来也不拆分来源：仍按直连对端一起计
    for i in range(2):
        assert (
            c.post("/account/login", json=bad, headers={"X-Forwarded-For": f"1.2.3.{i}"}).status_code
            == 401
        )
    refused = c.post(
        "/account/login", json=bad, headers={"X-Forwarded-For": "1.2.3.99"}
    )
    assert refused.status_code == 429


# ---------- CORS：默认全开，配了白名单只认白名单 ----------


def test_cors_default_open_for_dev():
    c = client()
    res = c.options(
        "/account/login",
        headers={
            "Origin": "https://anything.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert res.headers.get("access-control-allow-origin") == "*"


def test_cors_whitelist_restricts_origin():
    c = client(cors_origins=["http://tauri.localhost"])
    allowed = c.options(
        "/account/login",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://tauri.localhost"
    blocked = c.options(
        "/account/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert blocked.headers.get("access-control-allow-origin") is None
