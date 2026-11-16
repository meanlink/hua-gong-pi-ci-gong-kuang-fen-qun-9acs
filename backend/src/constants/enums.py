from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'
class TrialStatus(str, Enum):
    draft = 'draft'          # 已创建，未确认；不占用釜
    confirmed = 'confirmed'  # 已确认并占用釜与清洗时段
    started = 'started'      # 已开试；工况与排程冻结，仍占用至结束+清洗
    cancelled = 'cancelled'  # 已取消；释放未开试占用，不改写已开试历史
