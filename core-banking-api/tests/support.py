"""Regression fixtures use real cookie sessions and a temporary database."""
import atexit
import tempfile
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app import database, models

_temporary_directory = tempfile.TemporaryDirectory(prefix="banking-api-review-")
_database_path = Path(_temporary_directory.name) / "review.db"
engine = create_engine(f"sqlite:///{_database_path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 30})
@event.listens_for(engine, 'connect')
def pragmas(connection, _):
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=30000')
Session = sessionmaker(bind=engine, autoflush=False)
with patch.object(database, "engine", engine):
    from app.main import app

def _get_test_database():
    with Session() as session:
        yield session

def _cleanup():
    app.dependency_overrides.pop(database.get_db, None)
    engine.dispose()
    _temporary_directory.cleanup()
atexit.register(_cleanup)

class BankingClient(TestClient):
    """Workflow convenience: generate a fresh key for each intended money action.
    Tests of absent keys use plain TestClient.
    """
    def post(self, url, **kwargs):
        if url == '/transfer' or url.endswith(('/deposit', '/withdraw')):
            headers = dict(kwargs.pop('headers', {}) or {})
            headers.setdefault('Idempotency-Key', str(uuid.uuid4()))
            kwargs['headers'] = headers
        return super().post(url, **kwargs)

class BankingTestCase(unittest.TestCase):
    def setUp(self):
        from app.routers.auth import _attempts
        _attempts.clear()
        models.Base.metadata.drop_all(engine)
        models.Base.metadata.create_all(engine)
        app.dependency_overrides[database.get_db] = _get_test_database
        self.client = BankingClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self._account_number = 0
        self._cookies = {}
        response = self.client.post('/auth/register', json={'full_name': 'Demo Customer', 'email': 'demo@example.com', 'password': 'TestPassword!2026'})
        self.assertEqual(response.status_code, 201, response.text)

    def account(self, balance=0):
        self._account_number += 1
        with BankingClient(app, raise_server_exceptions=False) as other:
            name = f'Demo Customer {self._account_number}'
            email = f'demo{self._account_number}@example.com'
            registered = other.post('/auth/register', json={'full_name': name, 'email': email, 'password': 'TestPassword!2026'})
            self.assertEqual(registered.status_code, 201, registered.text)
            response = other.post('/accounts/', json={'owner_name': name, 'email': email, 'opening_deposit': str(balance)})
            self.assertEqual(response.status_code, 201, response.text)
            account = response.json()
            self._cookies[account['id']] = dict(other.cookies)
        if self._account_number == 1:
            self.client.cookies.clear()
            self.client.cookies.update(self._cookies[account['id']])
        return account

    def as_account(self, account_id):
        self.client.cookies.clear()
        self.client.cookies.update(self._cookies[account_id])

    def balance(self, account_id):
        with TestClient(app, raise_server_exceptions=False) as other:
            other.cookies.update(self._cookies[account_id])
            response = other.get(f'/accounts/{account_id}')
        self.assertEqual(response.status_code, 200, response.text)
        return Decimal(response.json()['balance'])

    def history(self, account_id):
        with TestClient(app, raise_server_exceptions=False) as other:
            other.cookies.update(self._cookies[account_id])
            response = other.get(f'/accounts/{account_id}/transactions?limit=100')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit_kyc(self, account_id):
        return self.client.post(f'/kyc/{account_id}', json={'full_name': 'Demo Customer', 'id_number': 'SYNTHETIC-ID-001', 'document_type': 'Demo ID'})

    def reviewer(self):
        from app.auth import hash_password
        with Session() as session:
            user = models.User(full_name='Demo Reviewer', email='reviewer@example.com', password_hash=hash_password('TestPassword!2026'), role=models.UserRole.reviewer)
            session.add(user)
            session.commit()
        response = self.client.post('/auth/login', json={'email': 'reviewer@example.com', 'password': 'TestPassword!2026'})
        self.assertEqual(response.status_code, 200, response.text)
