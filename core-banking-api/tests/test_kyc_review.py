"""KYC decisions require real sessions, ownership and an atomic review audit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import event

from app import models
from app.auth import hash_password
from app.routers.auth import _attempts
from tests.support import BankingTestCase, Session, app, engine


class KYCReviewTests(BankingTestCase):
    password = "Synthetic-Demo-Password-42"
    submission = {
        "full_name": "Ayesha Khan",
        "id_number": "SYNTHETIC-ID-001",
        "document_type": "Demo ID",
    }

    def setUp(self):
        super().setUp()
        _attempts.clear()
        self.client.cookies.clear()
        self._register(self.client, "ayesha@example.com", "Ayesha Khan")
        response = self.client.post("/accounts/", json={
            "owner_name": "Ayesha Khan", "email": "ayesha@example.com", "opening_deposit": "0.00",
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.account_id = response.json()["id"]
        with Session() as session:
            reviewer = models.User(
                full_name="Demo Reviewer", email="reviewer@example.com",
                password_hash=hash_password(self.password), role=models.UserRole.reviewer,
            )
            session.add(reviewer)
            session.commit()
            self.reviewer_id = reviewer.id
        self.reviewer = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.reviewer.close)
        response = self.reviewer.post("/auth/login", json={
            "email": "reviewer@example.com", "password": self.password,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _register(self, client, email, name):
        response = client.post("/auth/register", json={
            "full_name": name, "email": email, "password": self.password,
        })
        self.assertEqual(response.status_code, 201, response.text)
        response = client.post("/auth/login", json={"email": email, "password": self.password})
        self.assertEqual(response.status_code, 200, response.text)

    def submit(self, **changes):
        return self.client.post(f"/kyc/{self.account_id}", json={**self.submission, **changes})

    def review(self, approve=True, reason="Synthetic document manually checked", expected_submitted_at=None):
        if expected_submitted_at is None:
            expected_submitted_at = self.reviewer.get(f"/kyc/{self.account_id}").json()["submitted_at"]
        return self.reviewer.patch(f"/kyc/{self.account_id}/verify", json={
            "approve": approve, "reason": reason, "expected_submitted_at": expected_submitted_at,
        })

    def test_all_kyc_routes_require_a_session(self):
        record = self.submit().json()
        with TestClient(app, raise_server_exceptions=False) as anonymous:
            responses = [
                anonymous.post(f"/kyc/{self.account_id}", json=self.submission),
                anonymous.get(f"/kyc/{self.account_id}"),
                anonymous.get(f"/kyc/{self.account_id}/reviews"),
                anonymous.get("/kyc/reviews/pending"),
                anonymous.patch(f"/kyc/{self.account_id}/verify", json={
                    "approve": True, "reason": "Checked", "expected_submitted_at": record["submitted_at"],
                }),
            ]
        self.assertEqual([r.status_code for r in responses], [401] * len(responses))

    def test_customer_can_read_only_owned_kyc(self):
        record = self.submit().json()
        self.assertEqual(record["owner_name"], "Ayesha Khan")
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}").json(), record)
        with TestClient(app, raise_server_exceptions=False) as other:
            self._register(other, "other@example.com", "Other Customer")
            responses = [
                other.get(f"/kyc/{self.account_id}"),
                other.get(f"/kyc/{self.account_id}/reviews"),
                other.post(f"/kyc/{self.account_id}", json=self.submission),
            ]
        self.assertEqual([r.status_code for r in responses], [404, 404, 404])

    def test_customer_cannot_review_or_list_review_queue(self):
        record = self.submit().json()
        self.assertEqual(self.client.get("/kyc/reviews/pending").status_code, 403)
        response = self.client.patch(f"/kyc/{self.account_id}/verify", json={
            "approve": True, "reason": "Checked", "expected_submitted_at": record["submitted_at"],
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}").json()["status"], "pending")

    def test_pending_queue_includes_customer_context_and_removes_reviewed_record(self):
        record = self.submit().json()
        response = self.reviewer.get("/kyc/reviews/pending")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [record])
        self.assertEqual(self.review().status_code, 200)
        self.assertEqual(self.reviewer.get("/kyc/reviews/pending").json(), [])

    def test_approval_persists_reviewer_reason_time_and_audit(self):
        record = self.submit().json()
        response = self.review(reason="  Checked synthetic name and ID  ")
        self.assertEqual(response.status_code, 200, response.text)
        reviewed = response.json()
        self.assertEqual(reviewed["status"], "verified")
        self.assertEqual(reviewed["reviewed_by"], self.reviewer_id)
        self.assertEqual(reviewed["review_reason"], "Checked synthetic name and ID")
        self.assertIsNotNone(reviewed["reviewed_at"])
        audit = self.client.get(f"/kyc/{self.account_id}/reviews").json()
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["record_id"], record["id"])
        self.assertEqual(audit[0]["reviewer_id"], self.reviewer_id)
        self.assertEqual(audit[0]["decision"], "verified")
        self.assertEqual(audit[0]["reason"], reviewed["review_reason"])
        self.assertEqual(audit, self.reviewer.get(f"/kyc/{self.account_id}/reviews").json())

    def test_rejected_customer_can_resubmit_and_audit_survives(self):
        original = self.submit().json()
        self.assertEqual(self.review(False, "ID text needs correction").status_code, 200)
        response = self.submit(id_number="CORRECTED-ID-002")
        self.assertEqual(response.status_code, 201, response.text)
        updated = response.json()
        self.assertEqual(updated["id"], original["id"])
        self.assertEqual(updated["status"], "pending")
        self.assertEqual(updated["id_number"], "CORRECTED-ID-002")
        self.assertIsNone(updated["reviewed_at"])
        self.assertIsNone(updated["reviewed_by"])
        self.assertIsNone(updated["review_reason"])
        self.assertEqual(len(self.client.get(f"/kyc/{self.account_id}/reviews").json()), 1)
        self.assertEqual(self.review().status_code, 200)
        audit = self.client.get(f"/kyc/{self.account_id}/reviews").json()
        self.assertEqual([item["decision"] for item in audit], ["verified", "rejected"])

    def test_pending_and_verified_records_cannot_be_overwritten(self):
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.submit(id_number="OVERWRITE-001").status_code, 400)
        self.assertEqual(self.review().status_code, 200)
        self.assertEqual(self.submit(id_number="OVERWRITE-002").status_code, 400)
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}").json()["id_number"], self.submission["id_number"])

    def test_final_decision_cannot_be_changed_or_add_another_audit(self):
        self.assertEqual(self.submit().status_code, 201)
        self.assertEqual(self.review().status_code, 200)
        self.assertEqual(self.review(False, "Attempted second decision").status_code, 409)
        self.assertEqual(len(self.client.get(f"/kyc/{self.account_id}/reviews").json()), 1)

    def test_review_requires_explicit_json_decision_and_nonblank_reason(self):
        record = self.submit().json()
        for body in ({"approve": True}, {"reason": "Checked"}, {"approve": True, "reason": "   "}):
            with self.subTest(body=body):
                response = self.reviewer.patch(f"/kyc/{self.account_id}/verify", json={
                    "expected_submitted_at": record["submitted_at"], **body,
                })
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.reviewer.patch(f"/kyc/{self.account_id}/verify?approve=true").status_code, 422)
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}").json()["status"], "pending")

    def test_review_requires_the_version_from_the_opened_submission(self):
        self.assertEqual(self.submit().status_code, 201)
        response = self.reviewer.patch(f"/kyc/{self.account_id}/verify", json={
            "approve": True, "reason": "Checked fields",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}").json()["status"], "pending")

    def test_stale_reviewer_card_cannot_approve_a_resubmission(self):
        original = self.submit().json()
        self.assertEqual(self.review(False, "Correct the submitted ID").status_code, 200)
        self.assertEqual(self.submit(id_number="CORRECTED-ID-002").status_code, 201)
        response = self.review(expected_submitted_at=original["submitted_at"])
        self.assertEqual(response.status_code, 409, response.text)
        record = self.client.get(f"/kyc/{self.account_id}").json()
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["id_number"], "CORRECTED-ID-002")
        self.assertEqual(len(self.client.get(f"/kyc/{self.account_id}/reviews").json()), 1)

    def test_version_comparison_normalizes_equivalent_timezones(self):
        record = self.submit().json()
        submitted = datetime.fromisoformat(record["submitted_at"]).replace(tzinfo=timezone.utc)
        equivalent = submitted.astimezone(timezone(timedelta(hours=5))).isoformat()
        response = self.review(expected_submitted_at=equivalent)
        self.assertEqual(response.status_code, 200, response.text)

    def test_submission_rejects_short_id_after_trimming(self):
        self.assertEqual(self.submit(id_number="   AB   ").status_code, 422)

    def test_audit_write_failure_rolls_back_review_decision(self):
        self.assertEqual(self.submit().status_code, 201)

        def fail_audit(mapper, connection, target):
            raise RuntimeError("Simulated audit write failure")

        event.listen(models.KYCReviewAudit, "before_insert", fail_audit)
        try:
            response = self.review()
        finally:
            event.remove(models.KYCReviewAudit, "before_insert", fail_audit)
        self.assertEqual(response.status_code, 500)
        record = self.client.get(f"/kyc/{self.account_id}").json()
        self.assertEqual(record["status"], "pending")
        self.assertIsNone(record["reviewed_by"])
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}/reviews").json(), [])

    def test_concurrent_decisions_create_exactly_one_audit(self):
        self.assertEqual(self.submit().status_code, 201)
        ready = Barrier(2)

        def synchronize_updates(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("UPDATE KYC_RECORDS"):
                ready.wait(timeout=10)

        event.listen(engine, "before_cursor_execute", synchronize_updates)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda approve: self.review(approve), [True, False]))
        finally:
            event.remove(engine, "before_cursor_execute", synchronize_updates)
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409],
                         [response.text for response in responses])
        self.assertEqual(len(self.client.get(f"/kyc/{self.account_id}/reviews").json()), 1)

    def test_delayed_review_cannot_decide_a_newer_submission(self):
        self.assertEqual(self.submit().status_code, 201)
        changed = False

        def replace_submission(connection, cursor, statement, parameters, context, executemany):
            nonlocal changed
            if statement.lstrip().upper().startswith("UPDATE KYC_RECORDS") and not changed:
                changed = True
                with Session() as session:
                    record = session.query(models.KYCRecord).filter_by(account_id=self.account_id).one()
                    record.submitted_at += timedelta(seconds=1)
                    record.id_number = "NEW-SUBMISSION-ID-002"
                    session.commit()

        event.listen(engine, "before_cursor_execute", replace_submission)
        try:
            response = self.review()
        finally:
            event.remove(engine, "before_cursor_execute", replace_submission)
        self.assertEqual(response.status_code, 409, response.text)
        record = self.client.get(f"/kyc/{self.account_id}").json()
        self.assertEqual(record["status"], "pending")
        self.assertEqual(record["id_number"], "NEW-SUBMISSION-ID-002")
        self.assertEqual(self.client.get(f"/kyc/{self.account_id}/reviews").json(), [])
