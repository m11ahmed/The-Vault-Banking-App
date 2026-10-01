"""Version 2: exact money and access controls, preserving v1 records and a backup.

Legacy accounts remain unowned: registering a matching email never claims them.
Original amounts are rounded to paisa during migration; the original file is backed up.
"""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from sqlalchemy import inspect, select
from .database import Base

VERSION = 2


def _minor(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0 or amount > 1_000_000_000:
        raise ValueError('Legacy money is outside supported bounds. Migration stopped.')
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _date(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def migrate(engine):
    from . import models
    inspector = inspect(engine)
    names = inspector.get_table_names()
    if 'schema_version' in names:
        with engine.connect() as conn:
            versions = conn.execute(select(models.SchemaVersion.version)).scalars().all()
        if versions and max(versions) > VERSION:
            raise RuntimeError('Database schema is newer than this application.')
    legacy = 'accounts' in names and 'balance_minor' not in {col['name'] for col in inspector.get_columns('accounts')}
    if legacy:
        if engine.dialect.name != 'sqlite':
            raise RuntimeError('The legacy migration supports SQLite. Export and migrate other databases explicitly.')
        path = engine.url.database
        if path and path != ':memory:':
            backup_dir = Path(path).resolve().parent / '.data' / 'backups'
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_file = backup_dir / ('banking-v1-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f') + '.db')
            with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup_file)) as destination:
                source.backup(destination)
        with engine.connect() as conn:
            conn.exec_driver_sql('PRAGMA foreign_keys=OFF')
            conn.commit()
            # SQLite's default driver does not transact DDL without an explicit BEGIN.
            conn.exec_driver_sql('BEGIN IMMEDIATE')
            try:
                accounts = [dict(row) for row in conn.exec_driver_sql('SELECT * FROM accounts').mappings()]
                transactions = [dict(row) for row in conn.exec_driver_sql('SELECT * FROM transactions').mappings()] if 'transactions' in names else []
                kycs = [dict(row) for row in conn.exec_driver_sql('SELECT * FROM kyc_records').mappings()] if 'kyc_records' in names else []
                for row in accounts:
                    row['balance_minor'] = _minor(row.pop('balance'))
                    row['owner_user_id'] = None
                    row['created_at'] = _date(row['created_at'])
                for row in transactions:
                    row['amount_minor'] = _minor(row.pop('amount'))
                    row['balance_after_minor'] = _minor(row.pop('balance_after'))
                    row['created_at'] = _date(row['created_at'])
                for row in kycs:
                    row['submitted_at'] = _date(row['submitted_at'])
                for table in ('kyc_records', 'transactions', 'accounts'):
                    if table in names:
                        conn.exec_driver_sql(f'DROP TABLE {table}')
                Base.metadata.create_all(conn)
                if accounts:
                    conn.execute(models.Account.__table__.insert(), accounts)
                if transactions:
                    conn.execute(models.Transaction.__table__.insert(), transactions)
                if kycs:
                    conn.execute(models.KYCRecord.__table__.insert(), kycs)
                conn.execute(models.SchemaVersion.__table__.insert().values(version=VERSION))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.exec_driver_sql('PRAGMA foreign_keys=ON')
                conn.commit()
    else:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            if conn.execute(select(models.SchemaVersion.version).where(models.SchemaVersion.version == VERSION)).first() is None:
                conn.execute(models.SchemaVersion.__table__.insert().values(version=VERSION))
