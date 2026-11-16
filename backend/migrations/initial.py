from sqlalchemy import text
from src.config.database import Base, engine
from src import models


def _ensure_guard_row():
    """schedule_guards 固定一行；确认/改期/检修写入先更新它来取得排程写锁。"""
    with engine.begin() as conn:
        exists = conn.execute(text('SELECT COUNT(*) FROM schedule_guards')).scalar()
        if not exists:
            conn.execute(text('INSERT INTO schedule_guards (id, revision, updated_at) VALUES (1, 0, 0)'))


def migrate():
    Base.metadata.create_all(engine)
    _ensure_guard_row()


if __name__ == '__main__':
    migrate()
