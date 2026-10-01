"""Check real legacy SQLite migration, preservation, backup and rollback."""
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from sqlalchemy import create_engine, inspect
from app import models
from app.migrations import migrate


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='vault-migration-')
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'legacy.db'
        self.engine = create_engine(f'sqlite:///{self.path.as_posix()}')
        self.addCleanup(self.engine.dispose)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript('''
                CREATE TABLE accounts(id TEXT PRIMARY KEY, owner_name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, balance FLOAT NOT NULL, status TEXT, created_at TEXT);
                CREATE TABLE transactions(id TEXT PRIMARY KEY, account_id TEXT NOT NULL, type TEXT NOT NULL, amount FLOAT NOT NULL, balance_after FLOAT NOT NULL, related_account_id TEXT, note TEXT, created_at TEXT);
                CREATE TABLE kyc_records(id TEXT PRIMARY KEY, account_id TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL, id_number TEXT NOT NULL, document_type TEXT NOT NULL, status TEXT, submitted_at TEXT);
                INSERT INTO accounts VALUES ('a','Legacy Person','legacy@example.com',0.30000000000000004,'active','2026-09-01 12:00:00');
                INSERT INTO transactions VALUES ('t','a','deposit',0.3,0.30000000000000004,NULL,'Original','2026-09-01 12:00:00');
                INSERT INTO kyc_records VALUES ('k','a','Legacy Person','SAMPLE-ID','Demo ID','pending','2026-09-01 12:00:00');
            ''')

    def test_preserves_ids_and_records_and_does_not_claim_legacy_accounts(self):
        migrate(self.engine)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            self.assertEqual(conn.execute('SELECT id,balance_minor,owner_user_id FROM accounts').fetchone(), ('a', 30, None))
            self.assertEqual(conn.execute('SELECT id,amount_minor,balance_after_minor FROM transactions').fetchone(), ('t', 30, 30))
            self.assertEqual(conn.execute('SELECT id,reviewed_by FROM kyc_records').fetchone(), ('k', None))
        backups = list((self.path.parent / '.data' / 'backups').glob('*.db'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertEqual(backup.execute('SELECT balance FROM accounts').fetchone()[0], 0.30000000000000004)
        migrate(self.engine)
        self.assertEqual(len(list((self.path.parent / '.data' / 'backups').glob('*.db'))), 1)

    def test_bad_legacy_money_stops_without_losing_original_tables(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('UPDATE accounts SET balance=-10')
        with self.assertRaises(ValueError):
            migrate(self.engine)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT balance FROM accounts').fetchone()[0], -10)
            self.assertEqual(conn.execute('SELECT count(*) FROM transactions').fetchone()[0], 1)
        self.assertNotIn('balance_minor', {col['name'] for col in inspect(self.engine).get_columns('accounts')})

    def test_fresh_database_supports_new_writes(self):
        fresh_path = self.path.parent / 'fresh.db'
        fresh_engine = create_engine(f'sqlite:///{fresh_path.as_posix()}')
        try:
            migrate(fresh_engine)
            with fresh_engine.begin() as conn:
                conn.execute(models.Account.__table__.insert().values(id='new', owner_name='New Person', email='new@example.com', balance_minor=100))
            self.assertIn('auth_sessions', inspect(fresh_engine).get_table_names())
        finally:
            fresh_engine.dispose()
