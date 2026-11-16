from sqlalchemy import String, Double, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MaintenanceWindow(Base):
    """实验釜检修封锁。active 时阻止小试占用（含清洗时段）覆盖。"""
    __tablename__ = 'maintenance_windows'
    __table_args__ = (Index('ix_maintenance_reactor_time', 'reactor_id', 'start_at'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    reactor_id: Mapped[int] = mapped_column(ForeignKey('reactors.id'))
    start_at: Mapped[float] = mapped_column(Double)
    end_at: Mapped[float] = mapped_column(Double)
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default='active')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_by_name: Mapped[str] = mapped_column(String(80), default='')
    created_at: Mapped[float] = mapped_column(Double)
    released_at: Mapped[float | None] = mapped_column(Double, nullable=True)
