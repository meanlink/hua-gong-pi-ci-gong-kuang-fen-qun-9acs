from sqlalchemy import String, JSON, Double, ForeignKey, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class PilotTrial(Base):
    """小试单：来源获批分析，创建时冻结工况，负责人再为整单排程。

    占用由 status + slots 推导：confirmed/started 才占用，cancelled/draft 不占用。
    状态迁移与排程写入在一个事务内完成，中断只会回滚，不会留下半张占用。
    """
    __tablename__ = 'pilot_trials'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    cluster: Mapped[str] = mapped_column(String(80))
    conditions: Mapped[dict] = mapped_column(JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='draft')
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_by_name: Mapped[str] = mapped_column(String(80), default='')
    created_at: Mapped[float] = mapped_column(Double)
    confirmed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    started_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    cancelled_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    frozen_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    frozen_schedule: Mapped[list] = mapped_column(JSON, default=list)
    frozen_conditions: Mapped[dict] = mapped_column(JSON, default=dict)


class PilotSlot(Base):
    """小试单的整单排程明细。占用区间为 [start_at, end_at + 清洗时长)。"""
    __tablename__ = 'pilot_slots'
    __table_args__ = (Index('ix_pilot_slots_reactor_time', 'reactor_id', 'start_at'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey('pilot_trials.id'), index=True)
    reactor_id: Mapped[int] = mapped_column(ForeignKey('reactors.id'))
    start_at: Mapped[float] = mapped_column(Double)
    end_at: Mapped[float] = mapped_column(Double)
    cleaning_minutes: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)
    released_at: Mapped[float | None] = mapped_column(Double, nullable=True)
