"""Authentication and authorization through real cookie sessions and a test DB."""
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import database, models
from app.auth import COOKIE_NAME, hash_password, session_hash, verify_password
from app.routers.auth import _attempts
from tests.support import app, engine, Session, _get_test_database

PASSWORD = "TestPassword!2026"


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        _attempts.clear()
        models.Base.metadata.drop_all(engine)
        models.Base.metadata.create_all(engine)
        self.previous_overrides = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        app.dependency_overrides[database.get_db] = _get_test_database
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.restore_overrides)

    def restore_overrides(self):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.previous_overrides)

    def register(self, client=None, email="customer@example.com", name="Test Customer"):
        client = client or self.client
        response = client.post("/auth/register", json={"full_name": name, "email": email, "password": PASSWORD})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["user"]

    def account(self, client, user):
        response = client.post("/accounts/", json={
            "owner_name": user["full_name"], "email": user["email"], "opening_deposit": "10.00",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def reviewer(self, client=None):
        client = client or self.client
        with Session() as db:
            reviewer = models.User(full_name="Test Reviewer", email="reviewer@example.com",
                                   password_hash=hash_password(PASSWORD), role=models.UserRole.reviewer)
            db.add(reviewer)
            db.commit()
        response = client.post("/auth/login", json={"email": "reviewer@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["user"]

    def test_registration_sets_private_cookie_and_exposes_only_public_identity(self):
        response = self.client.post("/auth/register", json={
            "full_name": "Test Customer", "email": "Customer@example.com", "password": PASSWORD,
        })
        self.assertEqual(response.status_code, 201, response.text)
        user = response.json()["user"]
        self.assertEqual(set(user), {"id", "full_name", "email", "role"})
        self.assertEqual(user["email"], "customer@example.com")
        self.assertEqual(user["role"], "customer")
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertIn("path=/", cookie)
        self.assertIn("max-age=28800", cookie)
        self.assertEqual(self.client.get("/auth/me").json(), user)
        self.assertNotIn(PASSWORD, response.text)
        self.assertNotIn(self.client.cookies[COOKIE_NAME], response.text)
        with Session() as db:
            stored = db.get(models.User, user["id"])
            self.assertNotEqual(stored.password_hash, PASSWORD)
            self.assertTrue(stored.password_hash.startswith("pbkdf2_sha256$"))
            self.assertTrue(verify_password(PASSWORD, stored.password_hash))
            self.assertFalse(verify_password("IncorrectPassword", stored.password_hash))
            session = db.query(models.AuthSession).one()
            self.assertEqual(session.token_hash, session_hash(self.client.cookies[COOKIE_NAME]))
            self.assertNotEqual(session.token_hash, self.client.cookies[COOKIE_NAME])

    def test_password_hashes_are_salted(self):
        first, second = hash_password(PASSWORD), hash_password(PASSWORD)
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password(PASSWORD, first))
        self.assertTrue(verify_password(PASSWORD, second))

    def test_registration_cannot_escalate_role(self):
        response = self.client.post("/auth/register", json={
            "full_name": "Test Customer", "email": "customer@example.com", "password": PASSWORD,
            "role": "reviewer",
        })
        self.assertEqual(response.status_code, 422, response.text)
        with Session() as db:
            self.assertEqual(db.query(models.User).count(), 0)

    def test_duplicate_registration_case_insensitive(self):
        self.register()
        response = self.client.post("/auth/register", json={
            "full_name": "Other Customer", "email": "CUSTOMER@example.com", "password": PASSWORD,
        })
        self.assertEqual(response.status_code, 409, response.text)
        with Session() as db:
            self.assertEqual(db.query(models.User).count(), 1)

    def test_successful_login_rotates_and_revokes_previous_session(self):
        user = self.register()
        old = self.client.cookies[COOKIE_NAME]
        response = self.client.post("/auth/login", json={"email": "CUSTOMER@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 200, response.text)
        current = self.client.cookies[COOKIE_NAME]
        self.assertNotEqual(current, old)
        self.assertEqual(self.client.get("/auth/me").json()["id"], user["id"])
        with TestClient(app) as other:
            self.assertEqual(other.get("/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code, 401)
            self.assertEqual(other.get("/auth/me", headers={"Authorization": f"Bearer {current}"}).status_code, 200)
        with Session() as db:
            self.assertEqual(db.query(models.AuthSession).count(), 1)

    def test_invalid_login_has_generic_error(self):
        self.register()
        responses = [self.client.post("/auth/login", json={"email": email, "password": "IncorrectPassword"})
                     for email in ("customer@example.com", "unknown@example.com")]
        self.assertEqual([response.status_code for response in responses], [401, 401])
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(responses[0].json()["detail"], "Email or password is incorrect.")
        self.assertNotIn("password_hash", responses[0].text)

    def test_logout_revokes_session_and_clears_cookie(self):
        self.register()
        token = self.client.cookies[COOKIE_NAME]
        response = self.client.post("/auth/logout")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertNotIn(COOKIE_NAME, self.client.cookies)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        with TestClient(app) as other:
            self.assertEqual(other.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)
        with Session() as db:
            self.assertEqual(db.query(models.AuthSession).count(), 0)

    def test_expired_session_is_rejected(self):
        self.register()
        with Session() as db:
            db.query(models.AuthSession).one().expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/accounts/").status_code, 401)

    def test_forged_and_oversized_tokens_are_rejected(self):
        for token in ("unrecognized-token", "x" * 257):
            with self.subTest(token_length=len(token)):
                response = self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401, response.text)

    def test_unauthenticated_private_endpoints_require_login(self):
        account_id = "nonexistent-account"
        requests = [
            ("get", "/auth/me", {}),
            ("get", "/accounts/", {}),
            ("get", f"/accounts/{account_id}", {}),
            ("get", f"/accounts/{account_id}/transactions", {}),
            ("get", f"/kyc/{account_id}", {}),
            ("get", "/kyc/reviews/pending", {}),
            ("post", "/assistant/chat", {"json": {"message": "What is my balance?", "account_id": account_id}}),
            ("post", f"/kyc/{account_id}/analyze-document", {"files": {"file": ("demo.txt", b"Name: Test Customer", "text/plain")}}),
            ("post", f"/accounts/{account_id}/deposit", {"json": {"amount": "1.00"}, "headers": {"Idempotency-Key": "unauth"}}),
        ]
        for method, path, kwargs in requests:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401, response.text)

    def test_cross_owner_account_history_assistant_and_document_are_private(self):
        user = self.register()
        own_account = self.account(self.client, user)
        with TestClient(app, raise_server_exceptions=False) as other:
            other_user = self.register(other, "other@example.com", "Other Customer")
            other_account = self.account(other, other_user)
            other.post(f"/kyc/{other_account}", json={
                "full_name": "Other Customer", "id_number": "TEST-ID-0001", "document_type": "Demo ID",
            })
        response = self.client.get("/accounts/")
        self.assertEqual([item["id"] for item in response.json()], [own_account])
        requests = [
            ("get", f"/accounts/{other_account}", {}),
            ("get", f"/accounts/{other_account}/transactions", {}),
            ("get", f"/kyc/{other_account}", {}),
            ("get", f"/kyc/{other_account}/reviews", {}),
            ("post", "/assistant/chat", {"json": {"message": "What is my balance?", "account_id": other_account}}),
            ("post", f"/kyc/{other_account}/analyze-document", {"files": {"file": ("demo.txt", b"Name: Other Customer", "text/plain")}}),
            ("post", f"/accounts/{other_account}/withdraw", {"json": {"amount": "1.00"}, "headers": {"Idempotency-Key": "other-money"}}),
            ("post", f"/kyc/{other_account}", {"json": {"full_name": "Other Customer", "id_number": "TEST-ID-0002", "document_type": "Demo ID"}}),
        ]
        for method, path, kwargs in requests:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 404, response.text)

    def test_customer_cannot_access_reviewer_actions(self):
        user = self.register()
        account = self.account(self.client, user)
        self.assertEqual(self.client.get("/kyc/reviews/pending").status_code, 403)
        response = self.client.patch(f"/kyc/{account}/verify", json={"approve": True, "reason": "Test review"})
        self.assertEqual(response.status_code, 403, response.text)

    def test_reviewer_cannot_use_customer_banking_or_ai_actions(self):
        with TestClient(app, raise_server_exceptions=False) as customer:
            user = self.register(customer)
            account = self.account(customer, user)
        self.reviewer()
        self.assertEqual(self.client.get("/kyc/reviews/pending").status_code, 200)
        requests = [
            ("get", "/accounts/", {}),
            ("get", f"/accounts/{account}", {}),
            ("get", f"/accounts/{account}/transactions", {}),
            ("post", f"/accounts/{account}/deposit", {"json": {"amount": "1.00"}, "headers": {"Idempotency-Key": "reviewer"}}),
            ("post", "/assistant/chat", {"json": {"message": "What is my balance?", "account_id": account}}),
            ("post", f"/kyc/{account}/analyze-document", {"files": {"file": ("demo.txt", b"Name: Test Customer", "text/plain")}}),
        ]
        for method, path, kwargs in requests:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 403, response.text)

    def test_cross_origin_and_cross_site_mutations_are_blocked(self):
        user = self.register()
        account = self.account(self.client, user)
        for headers in ({"Origin": "https://untrusted.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}):
            with self.subTest(headers=headers):
                response = self.client.post(f"/accounts/{account}/deposit", json={"amount": "1.00"},
                                            headers={"Idempotency-Key": "cross-origin", **headers})
                self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.client.get(f"/accounts/{account}").json()["balance"], "10.00")
        response = self.client.post(f"/accounts/{account}/deposit", json={"amount": "1.00"},
                                    headers={"Idempotency-Key": "same-origin", "Origin": "http://testserver"})
        self.assertEqual(response.status_code, 200, response.text)

    def test_private_response_cannot_be_cached(self):
        self.register()
        response = self.client.get("/auth/me")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_cookie_secure_can_be_enabled(self):
        with patch.dict(os.environ, {"COOKIE_SECURE": "true"}):
            response = self.client.post("/auth/register", json={
                "full_name": "Secure Customer", "email": "secure@example.com", "password": PASSWORD,
            })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn("secure", response.headers["set-cookie"].lower())

    def test_demo_mode_false_is_reported_to_dashboard(self):
        with patch.dict(os.environ, {"DEMO_MODE": "false"}):
            response = self.client.get("/auth/config")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"demo_mode": False})
