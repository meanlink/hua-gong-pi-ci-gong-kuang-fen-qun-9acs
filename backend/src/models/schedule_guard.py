from sqlalchemy import Double, Integer
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class ScheduleGuard(Base):
    """全局排程写锁与版本计数。

    所有确认、改期、开试、取消、检修写入先 UPDATE 此行：MySQL 取得行锁，
    SQLite 取得写锁，从而把“查冲突 + 落排程”串行化，避免并发确认撞釜。
    revision 同时作为排程全局版本，便于诊断。
    """
    __tablename__ = 'schedule_guards'
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[float] = mapped_column(Double, default=0)
