from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.database.base import Base


def make_engine(database_file):
    engine = create_engine(
        f"sqlite:///{database_file.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    return engine


def initialize_database(engine):
    from backend.models import entities  # noqa: F401 - register model metadata
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, 1)"
        )


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
