"""Environment-selected database and a versioned, backed-up SQLite v1 migration."""
import os
from pathlib import Path
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / '.env', override=False)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(PROJECT_DIR / 'banking.db').as_posix()}")
options = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite:") else {}
engine = create_engine(DATABASE_URL, connect_args=options, pool_pre_ping=True)

@event.listens_for(engine, "connect")
def database_pragmas(connection, _):
    if engine.dialect.name == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def initialize_database(target_engine=None):
    from .migrations import migrate
    migrate(target_engine or engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
