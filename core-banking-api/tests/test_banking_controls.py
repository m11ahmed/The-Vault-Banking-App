"""Money correctness through the API, using real writes and concurrent requests.

Only token verification is substituted; authentication's real implementation is
covered separately. Ownership checks and all financial SQL remain unchanged.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import unittest

from fastapi import Depends, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import database, models
from app.auth import require_customer
from app.money import MAX_MINOR, money_decimal, parse_minor
from tests.support import app, engine, Session


def _database():
    with Session() as db:
        yield db


def _customer(request: Request, db=Depends(database.get_db)):
    user_id = request.headers.get("X-Test-User")
    user = db.get(models.User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    if user.role != models.UserRole.customer:
        raise HTTPException(status_code=403, detail="Customer role required.")
    return user


class BankingControlTests(unittest.TestCase):
    def setUp(self):
        models.Base.metadata.drop_all(engine)
        models.Base.metadata.create_all(engine)
        self.previous_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[database.get_db] = _database
        app.dependency_overrides[require_customer] = _customer
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.restore_overrides)
        self.number = 0

    def restore_overrides(self):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.previous_overrides)

    def user(self):
        self.number += 1
        with Session() as db:
            user = models.User(
                full_name=f"Customer {self.number}", email=f"customer{self.number}@example.com",
                password_hash="unused-test-password", role=models.UserRole.customer,
            )
            db.add(user)
            db.commit()
            return {"id": user.id, "full_name": user.full_name, "email": user.email}

    def headers(self, user, key=None):
        headers = {"X-Test-User": user["id"]}
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers

    def account(self, user, amount="0.00"):
        response = self.client.post("/accounts/", json={
            "owner_name": user["full_name"], "email": user["email"], "opening_deposit": amount,
        }, headers=self.headers(user))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def balance(self, account):
        with Session() as db:
            return db.get(models.Account, account).balance_minor

    def payment(self, user, account, amount, key="payment-1", operation="deposit"):
        return self.client.post(f"/accounts/{account}/{operation}", json={"amount": amount},
                                headers=self.headers(user, key))

    def history(self, user, account, **params):
        response = self.client.get(f"/accounts/{account}/transactions", params=params,
                                   headers=self.headers(user))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_exact_paisa_arithmetic(self):
        user = self.user()
        account = self.account(user, "0.10")
        response = self.payment(user, account, "0.20")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Decimal(str(response.json()["balance_after"])), Decimal("0.30"))
        self.assertEqual(self.balance(account), 30)

    def test_invalid_money_never_changes_balance(self):
        user = self.user()
        account = self.account(user)
        for amount in ("0.001", "1.230", "NaN", "Infinity", "-0.01", "0", "1000000000.01"):
            with self.subTest(amount=amount):
                response = self.payment(user, account, amount)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.balance(account), 0)
        self.assertEqual(self.history(user, account), [])

    def test_simultaneous_withdrawals_cannot_overspend(self):
        user = self.user()
        account = self.account(user, "100.00")
        start = Barrier(2)
        def withdraw(number):
            start.wait(timeout=10)
            return self.payment(user, account, "80.00", f"withdraw-{number}", "withdraw")
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(withdraw, (1, 2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 400],
                         [response.text for response in responses])
        self.assertEqual(self.balance(account), 2000)
        self.assertEqual(len(self.history(user, account)), 2)

    def test_simultaneous_retry_returns_original_receipt(self):
        user = self.user()
        account = self.account(user)
        start = Barrier(2)
        def deposit(_):
            start.wait(timeout=10)
            return self.payment(user, account, "10.00", "same-payment")
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(deposit, (1, 2)))
        self.assertEqual([response.status_code for response in responses], [200, 200],
                         [response.text for response in responses])
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(self.balance(account), 1000)
        self.assertEqual(len(self.history(user, account)), 1)

    def test_idempotency_persists_across_clients_and_changed_payload_conflicts(self):
        user = self.user()
        account = self.account(user, "100.00")
        first = self.payment(user, account, "5.00", "retry", "withdraw")
        with TestClient(app, raise_server_exceptions=False) as second_client:
            replay = second_client.post(f"/accounts/{account}/withdraw", json={"amount": "5.0"},
                                        headers=self.headers(user, "retry"))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(first.json(), replay.json())
        conflict = self.payment(user, account, "6.00", "retry", "withdraw")
        self.assertEqual(conflict.status_code, 409, conflict.text)
        conflict = self.payment(user, account, "5.00", "retry", "deposit")
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(self.balance(account), 9500)

    def test_keys_are_scoped_to_customer(self):
        for _ in range(2):
            user = self.user()
            account = self.account(user)
            response = self.payment(user, account, "1.00", "same-key")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.balance(account), 100)

    def test_money_operations_require_valid_idempotency_key(self):
        user = self.user()
        account = self.account(user)
        for key in (None, "", " ", " outer ", "x" * 101):
            with self.subTest(key=key):
                response = self.payment(user, account, "1.00", key)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.balance(account), 0)

    def test_account_creation_requires_registered_identity(self):
        user = self.user()
        response = self.client.post("/accounts/", json={
            "owner_name": "Another Person", "email": user["email"], "opening_deposit": "10",
        }, headers=self.headers(user))
        self.assertEqual(response.status_code, 403, response.text)
        response = self.client.post("/accounts/", json={
            "owner_name": user["full_name"], "email": "other@example.com",
        }, headers=self.headers(user))
        self.assertEqual(response.status_code, 403, response.text)

    def test_customer_cannot_read_or_write_someone_elses_account(self):
        first, second = self.user(), self.user()
        own, other = self.account(first, "10"), self.account(second, "20")
        listing = self.client.get("/accounts/", headers=self.headers(first))
        self.assertEqual([item["id"] for item in listing.json()], [own])
        for path in (f"/accounts/{other}", f"/accounts/{other}/transactions"):
            self.assertEqual(self.client.get(path, headers=self.headers(first)).status_code, 404)
        self.assertEqual(self.payment(first, other, "1").status_code, 404)
        self.assertEqual(self.payment(first, other, "1", "withdraw", "withdraw").status_code, 404)
        self.assertEqual(self.balance(other), 2000)

    def test_account_list_requires_authentication(self):
        self.assertEqual(self.client.get("/accounts/").status_code, 401)

    def test_transfer_atomic_and_private_with_persisted_retry(self):
        sender, recipient = self.user(), self.user()
        source, destination = self.account(sender, "100"), self.account(recipient, "20")
        payload = {"from_account_id": source, "to_account_id": destination, "amount": "30.01"}
        first = self.client.post("/transfer", json=payload, headers=self.headers(sender, "transfer"))
        second = self.client.post("/transfer", json=payload, headers=self.headers(sender, "transfer"))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(first.json()), 1)
        self.assertEqual(first.json()[0]["account_id"], source)
        self.assertEqual(first.json()[0]["related_account_id"], destination)
        self.assertEqual((self.balance(source), self.balance(destination)), (6999, 5001))
        self.assertEqual(self.history(recipient, destination)[0]["type"], "transfer_in")

    def test_opening_deposit_failure_rolls_back_whole_account(self):
        user = self.user()
        def fail(mapper, connection, target):
            raise RuntimeError("Simulated ledger failure")
        event.listen(models.Transaction, "before_insert", fail)
        try:
            response = self.client.post("/accounts/", json={
                "owner_name": user["full_name"], "email": user["email"], "opening_deposit": "100",
            }, headers=self.headers(user))
        finally:
            event.remove(models.Transaction, "before_insert", fail)
        self.assertEqual(response.status_code, 500, response.text)
        with Session() as db:
            self.assertEqual(db.query(models.Account).count(), 0)
            self.assertEqual(db.query(models.Transaction).count(), 0)

    def test_failed_deposit_rolls_back_balance_ledger_and_retry_key(self):
        user = self.user()
        account = self.account(user, "10")
        def fail(mapper, connection, target):
            raise RuntimeError("Simulated ledger failure")
        event.listen(models.Transaction, "before_insert", fail)
        try:
            response = self.payment(user, account, "5", "retry-after-failure")
        finally:
            event.remove(models.Transaction, "before_insert", fail)
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual(self.balance(account), 1000)
        with Session() as db:
            self.assertEqual(db.query(models.PaymentRequest).count(), 0)
            self.assertEqual(db.query(models.Transaction).count(), 1)
        response = self.payment(user, account, "5", "retry-after-failure")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.balance(account), 1500)

    def test_incoming_ledger_failure_rolls_back_both_transfer_legs(self):
        sender, recipient = self.user(), self.user()
        source, destination = self.account(sender, "100"), self.account(recipient)
        def fail(mapper, connection, target):
            if target.type == models.TransactionType.transfer_in:
                raise RuntimeError("Simulated incoming ledger failure")
        event.listen(models.Transaction, "before_insert", fail)
        try:
            response = self.client.post("/transfer", json={
                "from_account_id": source, "to_account_id": destination, "amount": "10",
            }, headers=self.headers(sender, "transfer-failure"))
        finally:
            event.remove(models.Transaction, "before_insert", fail)
        self.assertEqual(response.status_code, 500, response.text)
        self.assertEqual((self.balance(source), self.balance(destination)), (10000, 0))
        with Session() as db:
            self.assertEqual(db.query(models.PaymentRequest).count(), 0)
            self.assertEqual(db.query(models.Transaction).count(), 1)

    def test_history_filters_and_pagination(self):
        user = self.user()
        account = self.account(user, "10")
        for number in range(4):
            self.assertEqual(self.payment(user, account, "1", f"deposit-{number}").status_code, 200)
        self.assertEqual(self.payment(user, account, "1", "withdraw", "withdraw").status_code, 200)
        first = self.history(user, account, limit=2)
        second = self.history(user, account, limit=2, offset=2)
        self.assertFalse({row["id"] for row in first} & {row["id"] for row in second})
        withdrawals = self.history(user, account, type="withdrawal")
        self.assertEqual(len(withdrawals), 1)
        self.assertEqual(withdrawals[0]["type"], "withdrawal")
        self.assertEqual(self.history(user, account, from_date="2100-01-01T00:00:00Z"), [])
        response = self.client.get(f"/accounts/{account}/transactions", params={
            "from_date": "2026-01-02T00:00:00Z", "to_date": "2026-01-01T00:00:00Z",
        }, headers=self.headers(user))
        self.assertEqual(response.status_code, 422, response.text)

    def test_inactive_account_and_balance_limit_reject_without_mutation(self):
        user = self.user()
        account = self.account(user, "1000000000")
        response = self.payment(user, account, "0.01")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.balance(account), MAX_MINOR)
        with Session() as db:
            db.get(models.Account, account).status = models.AccountStatus.frozen
            db.commit()
        response = self.payment(user, account, "1", "frozen", "withdraw")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.balance(account), MAX_MINOR)

    def test_database_refuses_negative_balance(self):
        user = self.user()
        account = self.account(user)
        with Session() as db:
            db.get(models.Account, account).balance_minor = -1
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
        self.assertEqual(self.balance(account), 0)


class MoneyConversionTests(unittest.TestCase):
    def test_conversion_is_exact(self):
        self.assertEqual(parse_minor(Decimal("0.29")), 29)
        self.assertEqual(money_decimal(29), Decimal("0.29"))
        self.assertEqual(parse_minor("1000000000"), MAX_MINOR)

    def test_conversion_rejects_invalid_amounts(self):
        for value in ("invalid", "NaN", "Infinity", "-1", "0.001", "1.230", "1000000000.01"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_minor(value)
