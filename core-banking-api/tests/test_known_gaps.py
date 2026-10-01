"""The original seven banking gaps, now required regression behavior."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import event

from app import models
from app.routers import transactions
from tests.support import BankingTestCase


class KnownGapTests(BankingTestCase):
    def test_money_arithmetic_is_exact(self):
        account_id = self.account(0.1)["id"]
        response = self.client.post(f"/accounts/{account_id}/deposit", json={"amount": 0.2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(str(self.balance(account_id))), Decimal("0.30"))

    def test_amount_smaller_than_one_paisa_is_rejected(self):
        account_id = self.account()["id"]
        response = self.client.post(f"/accounts/{account_id}/deposit", json={"amount": 0.001})
        self.assertEqual(response.status_code, 422, response.text)

    def test_two_concurrent_withdrawals_cannot_spend_the_same_funds(self):
        account_id = self.account(100)["id"]
        start = Barrier(2)

        def withdraw():
            start.wait(timeout=10)
            return self.client.post(f"/accounts/{account_id}/withdraw", json={"amount": 80})

        # Start requests together; the application must serialize business reads.
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: withdraw(), range(2)))
        observed = {
            "statuses": sorted(response.status_code for response in responses),
            "balance": self.balance(account_id),
            "withdrawals": [item["amount"] for item in self.history(account_id) if item["type"] == "withdrawal"],
        }
        self.assertEqual(observed["statuses"], [200, 400], str(observed))
        self.assertEqual(self.balance(account_id), 20)
        self.assertEqual(len(self.history(account_id)), 2)

    def test_opening_deposit_failure_rolls_back_account_creation(self):
        def fail_opening_transaction(mapper, connection, target):
            raise RuntimeError("Simulated opening transaction write failure")

        event.listen(models.Transaction, "before_insert", fail_opening_transaction)
        try:
            response = self.client.post(
                "/accounts/",
                json={"owner_name": "Demo Customer", "email": "demo@example.com", "opening_deposit": 100},
            )
        finally:
            event.remove(models.Transaction, "before_insert", fail_opening_transaction)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.client.get("/accounts/").json(), [])

    def test_retried_transfer_does_not_send_money_twice(self):
        source = self.account(100)["id"]
        destination = self.account()["id"]
        payload = {"from_account_id": source, "to_account_id": destination, "amount": 10}
        for _ in range(2):
            response = self.client.post(
                "/transfer", json=payload, headers={"Idempotency-Key": "demo-retry-001"}
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual((self.balance(source), self.balance(destination)), (90, 10))

    def test_account_list_requires_authentication(self):
        self.account()
        self.client.cookies.clear()
        response = self.client.get("/accounts/")
        self.assertIn(response.status_code, (401, 403))

    def test_kyc_approval_requires_reviewer_authorization(self):
        account_id = self.account()["id"]
        self.assertEqual(self.submit_kyc(account_id).status_code, 201)
        response = self.client.patch(f"/kyc/{account_id}/verify?approve=true")
        self.assertIn(response.status_code, (401, 403))
