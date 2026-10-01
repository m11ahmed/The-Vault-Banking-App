"""Customer banking workflows, exact-money responses and manual KYC decisions."""
from sqlalchemy import event

from app import models
from tests.support import BankingTestCase, Session


class WorkflowTests(BankingTestCase):
    def test_root_and_interactive_docs(self):
        for path in ("/", "/docs", "/openapi.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_create_zero_balance_account(self):
        account = self.account()
        self.assertEqual(account["balance"], "0.00")
        self.assertEqual(account["status"], "active")
        self.assertEqual(self.history(account["id"]), [])

    def test_opening_deposit_has_history(self):
        account = self.account(100)
        self.assertEqual(self.balance(account["id"]), 100)
        transactions = self.history(account["id"])
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]["type"], "deposit")
        self.assertEqual(transactions[0]["amount"], "100.00")
        self.assertEqual(transactions[0]["balance_after"], "100.00")

    def test_account_list_and_lookup(self):
        account = self.account()
        self.assertEqual(self.client.get(f"/accounts/{account['id']}").json(), account)
        self.assertEqual(self.client.get("/accounts/").json(), [account])

    def test_duplicate_email_rejected(self):
        account = self.account()
        response = self.client.post(
            "/accounts/", json={"owner_name": account["owner_name"], "email": account["email"]}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.client.get("/accounts/").json()), 1)

    def test_account_input_validation(self):
        for overrides in (
            {"owner_name": "A"}, {"email": "invalid"}, {"opening_deposit": -1}
        ):
            with self.subTest(overrides=overrides):
                response = self.client.post(
                    "/accounts/",
                    json={"owner_name": "Demo Customer", "email": "demo@example.com", **overrides},
                )
                self.assertEqual(response.status_code, 422)

    def test_missing_account_routes(self):
        responses = [
            self.client.get("/accounts/missing"),
            self.client.get("/accounts/missing/transactions"),
            self.client.post("/accounts/missing/deposit", json={"amount": 1}),
            self.client.post("/accounts/missing/withdraw", json={"amount": 1}),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 404)

    def test_deposit_and_withdrawal_update_balance_and_history(self):
        account_id = self.account(100)["id"]
        deposit = self.client.post(f"/accounts/{account_id}/deposit", json={"amount": 25})
        self.assertEqual(deposit.status_code, 200)
        self.assertEqual(deposit.json()["balance_after"], "125.00")
        withdrawal = self.client.post(f"/accounts/{account_id}/withdraw", json={"amount": 40})
        self.assertEqual(withdrawal.status_code, 200)
        self.assertEqual(withdrawal.json()["balance_after"], "85.00")
        self.assertEqual(self.balance(account_id), 85)
        self.assertEqual(len(self.history(account_id)), 3)

    def test_withdrawal_can_empty_account(self):
        account_id = self.account(10)["id"]
        response = self.client.post(f"/accounts/{account_id}/withdraw", json={"amount": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.balance(account_id), 0)

    def test_insufficient_withdrawal_leaves_state_unchanged(self):
        account_id = self.account(100)["id"]
        before = self.history(account_id)
        response = self.client.post(f"/accounts/{account_id}/withdraw", json={"amount": 101})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Insufficient funds.")
        self.assertEqual(self.balance(account_id), 100)
        self.assertEqual(self.history(account_id), before)

    def test_transfer_records_both_legs_and_conserves_balance(self):
        source = self.account(100)["id"]
        destination = self.account(20)["id"]
        response = self.client.post(
            "/transfer", json={"from_account_id": source, "to_account_id": destination, "amount": 30}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 1)
        outgoing = response.json()[0]
        incoming = self.history(destination)[0]
        self.assertEqual((outgoing["type"], incoming["type"]), ("transfer_out", "transfer_in"))
        self.assertEqual((outgoing["amount"], incoming["amount"]), ("30.00", "30.00"))
        self.assertEqual(outgoing["related_account_id"], destination)
        self.assertEqual(incoming["related_account_id"], source)
        self.assertEqual((self.balance(source), self.balance(destination)), (70, 50))
        self.assertEqual((len(self.history(source)), len(self.history(destination))), (2, 2))

    def test_insufficient_transfer_leaves_both_accounts_unchanged(self):
        source = self.account(100)["id"]
        destination = self.account()["id"]
        response = self.client.post(
            "/transfer", json={"from_account_id": source, "to_account_id": destination, "amount": 101}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual((self.balance(source), self.balance(destination)), (100, 0))
        self.assertEqual((len(self.history(source)), len(self.history(destination))), (1, 0))

    def test_self_transfer_rejected(self):
        account_id = self.account(100)["id"]
        response = self.client.post(
            "/transfer", json={"from_account_id": account_id, "to_account_id": account_id, "amount": 1}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.balance(account_id), 100)

    def test_missing_transfer_destination_leaves_source_unchanged(self):
        source = self.account(100)["id"]
        response = self.client.post(
            "/transfer", json={"from_account_id": source, "to_account_id": "missing", "amount": 1}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.balance(source), 100)

    def test_nonpositive_transaction_amounts_rejected(self):
        source = self.account(100)["id"]
        destination = self.account()["id"]
        for amount in (0, -1):
            for action in ("deposit", "withdraw"):
                with self.subTest(amount=amount, action=action):
                    response = self.client.post(f"/accounts/{source}/{action}", json={"amount": amount})
                    self.assertEqual(response.status_code, 422)
            response = self.client.post(
                "/transfer",
                json={"from_account_id": source, "to_account_id": destination, "amount": amount},
            )
            self.assertEqual(response.status_code, 422)

    def test_inactive_accounts_cannot_deposit_or_withdraw(self):
        account_id = self.account(100)["id"]
        for status in (models.AccountStatus.frozen, models.AccountStatus.closed):
            with Session() as session:
                session.get(models.Account, account_id).status = status
                session.commit()
            for action in ("deposit", "withdraw"):
                with self.subTest(status=status, action=action):
                    response = self.client.post(f"/accounts/{account_id}/{action}", json={"amount": 1})
                    self.assertEqual(response.status_code, 400)
            self.assertEqual(self.balance(account_id), 100)

    def test_transfer_checks_source_and_destination_status(self):
        source = self.account(100)["id"]
        destination = self.account(100)["id"]
        for inactive_id in (source, destination):
            with Session() as session:
                for account_id in (source, destination):
                    session.get(models.Account, account_id).status = (
                        models.AccountStatus.frozen if account_id == inactive_id else models.AccountStatus.active
                    )
                session.commit()
            response = self.client.post(
                "/transfer", json={"from_account_id": source, "to_account_id": destination, "amount": 1}
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual((self.balance(source), self.balance(destination)), (100, 100))

    def test_history_is_newest_first(self):
        account_id = self.account()["id"]
        for index in range(3):
            response = self.client.post(
                f"/accounts/{account_id}/deposit", json={"amount": 1, "note": str(index)}
            )
            self.assertEqual(response.status_code, 200)
        self.assertEqual([transaction["note"] for transaction in self.history(account_id)], ["2", "1", "0"])

    def test_kyc_submission_and_lookup(self):
        account_id = self.account()["id"]
        response = self.submit_kyc(account_id)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(self.client.get(f"/kyc/{account_id}").json(), response.json())

    def test_duplicate_kyc_rejected(self):
        account_id = self.account()["id"]
        self.assertEqual(self.submit_kyc(account_id).status_code, 201)
        self.assertEqual(self.submit_kyc(account_id).status_code, 400)

    def test_kyc_approval(self):
        account_id = self.account()["id"]
        self.assertEqual(self.submit_kyc(account_id).status_code, 201)
        self.reviewer()
        submitted_at = self.client.get(f"/kyc/{account_id}").json()["submitted_at"]
        response = self.client.patch(f"/kyc/{account_id}/verify", json={
            "approve": True, "reason": "Synthetic fields manually checked", "expected_submitted_at": submitted_at,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "verified")

    def test_kyc_rejection(self):
        account_id = self.account()["id"]
        self.assertEqual(self.submit_kyc(account_id).status_code, 201)
        self.reviewer()
        submitted_at = self.client.get(f"/kyc/{account_id}").json()["submitted_at"]
        response = self.client.patch(f"/kyc/{account_id}/verify", json={
            "approve": False, "reason": "Synthetic identity details do not match", "expected_submitted_at": submitted_at,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "rejected")

    def test_missing_kyc_and_account(self):
        account_id = self.account()["id"]
        self.assertEqual(self.client.get(f"/kyc/{account_id}").status_code, 404)
        self.assertEqual(self.submit_kyc("missing").status_code, 404)
        self.reviewer()
        response = self.client.patch(f"/kyc/{account_id}/verify", json={
            "approve": True, "reason": "Manual review requested", "expected_submitted_at": "2026-10-01T00:00:00Z",
        })
        self.assertEqual(response.status_code, 404)

    def test_transfer_database_failure_rolls_back_both_sides(self):
        source = self.account(100)["id"]
        destination = self.account()["id"]

        def fail_on_incoming_transaction(mapper, connection, target):
            if target.type == models.TransactionType.transfer_in:
                raise RuntimeError("Simulated database write failure")

        event.listen(models.Transaction, "before_insert", fail_on_incoming_transaction)
        try:
            response = self.client.post(
                "/transfer", json={"from_account_id": source, "to_account_id": destination, "amount": 30}
            )
        finally:
            event.remove(models.Transaction, "before_insert", fail_on_incoming_transaction)
        self.assertEqual(response.status_code, 500)
        self.assertEqual((self.balance(source), self.balance(destination)), (100, 0))
        self.assertEqual((len(self.history(source)), len(self.history(destination))), (1, 0))
