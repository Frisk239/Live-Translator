from fastapi.testclient import TestClient

from account import create_app


def client() -> TestClient:
    return TestClient(create_app())


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


def test_login_wrong_password():
    c = client()
    c.post("/account/register", json={"email": "a@b.c", "password": "secret12"})
    bad = c.post("/account/login", json={"email": "a@b.c", "password": "nope"})
    assert bad.status_code == 401
    assert "密码" in bad.json()["error"]
