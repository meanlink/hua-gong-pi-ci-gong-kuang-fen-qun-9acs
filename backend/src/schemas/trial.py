from datetime import datetime
from pydantic import BaseModel, Field


class SlotInput(BaseModel):
    reactor_id: int = Field(gt=0)
    start_at: str
    end_at: str


class TrialCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    job_id: int = Field(gt=0)
    cluster: str = Field(min_length=1, max_length=80)


class ScheduleInput(BaseModel):
    slots: list[SlotInput] = Field(min_length=1, max_length=12)
    note: str = Field(default='', max_length=300)


class CancelInput(BaseModel):
    reason: str = Field(default='', max_length=300)


class MaintenanceInput(BaseModel):
    reactor_id: int = Field(gt=0)
    start_at: str
    end_at: str
    reason: str = Field(min_length=2, max_length=200)


class MaintenanceRelease(BaseModel):
    reason: str = Field(default='', max_length=200)


def parse_local(value: str) -> float:
    """解析浏览器 datetime-local 或 ISO 时间，统一存 UTC 秒。

    无时区按服务器本地时区解释，与页面显示口径一致。
    """
    text = (value or '').strip()
    if not text:
        raise ValueError('时间不能为空')
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('时间格式须为 YYYY-MM-DDTHH:MM') from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()
