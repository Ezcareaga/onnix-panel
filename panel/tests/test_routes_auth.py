"""
Tests for app/routes/auth.py

Covers: GET /login, POST /login (valid/invalid), GET /logout.
"""
import os

import pytest

_TEST_PASSWORD = os.environ.get("TEST_ADMIN_PASSWORD", "test-fallback-only")


class TestGetLogin:
    async def test_returns_200(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200

    async def test_contains_form_fields(self, client):
        resp = await client.get("/login")
        assert b"email" in resp.content
        assert b"password" in resp.content


class TestPostLogin:
    async def test_valid_admin_redirects_to_dashboard(self, client):
        resp = await client.post("/login", data={
            "email": "ez@onnix.com.py",
            "password": _TEST_PASSWORD,
        })
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard"

    async def test_valid_login_sets_session_cookie(self, client):
        resp = await client.post("/login", data={
            "email": "ez@onnix.com.py",
            "password": _TEST_PASSWORD,
        })
        assert "onnix_session" in resp.cookies

    async def test_invalid_password_returns_401(self, client):
        resp = await client.post("/login", data={
            "email": "ez@onnix.com.py",
            "password": "wrongpassword",
        })
        assert resp.status_code == 401

    async def test_unknown_email_returns_401(self, client):
        resp = await client.post("/login", data={
            "email": "nobody@onnixtest.com",
            "password": _TEST_PASSWORD,
        })
        assert resp.status_code == 401

    async def test_missing_fields_returns_error(self, client):
        resp = await client.post("/login", data={"email": ""})
        assert resp.status_code in (401, 422)


class TestLogout:
    async def test_redirects_to_login(self, admin_client):
        resp = await admin_client.get("/logout")
        assert resp.status_code == 303
        assert "/login" in resp.headers["location"]

    async def test_clears_session_cookie(self, admin_client):
        resp = await admin_client.get("/logout")
        # Cookie should be cleared (expires in past or empty)
        cookie = resp.cookies.get("onnix_session", "")
        assert cookie == "" or "null" in str(resp.headers.get("set-cookie", ""))
