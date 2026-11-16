"""小试单与实验釜排程。

业务约束：
- 小试单只能从“已完成且获批”的分析结果中选一个工况族群创建，条件在创建时快照。
- 排程按整单提交，一段排程占用 [start, end + 该釜清洗时长)。
- 草稿可以保存尚未无冲突的计划；确认时才真正占用，且全单所有段都必须无冲突。
- 已确认单改期是整单原子替换：任一段冲突则原排程保持不变。
- 开试后冻结工况和排程；取消只释放尚未开试的占用。
- 确认/取消均为幂等状态迁移，中断回滚后重试不会重复占用。
"""
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from src.config.database import engine
from src.constants.enums import TrialStatus
from src.models import (
    Dataset,
    Job,
    MaintenanceWindow,
    PilotSlot,
    PilotTrial,
    Reactor,
    ScheduleGuard,
    TrialEvent,
)
from src.schemas.trial import parse_local
from src.services.audit import record
from src.services.business import FEATURES
from src.utils.serialization import checksum

OCCUPYING = (TrialStatus.confirmed.value, TrialStatus.started.value)
EDITABLE = (TrialStatus.draft.value, TrialStatus.confirmed.value)
MAX_SLOT_SECONDS = 30 * 86400
MAX_MAINTENANCE_SECONDS = 365 * 86400
MIN_SLOT_SECONDS = 60

# SQLite 没有 SELECT ... FOR UPDATE；同进程内先用线程锁把排程写入串行化，
# 数据库里的 schedule_guards 行仍负责跨进程/MySQL 的行锁与版本落账。
_SQLITE_LOCK = threading.RLock()


class ScheduleConflict(Exception):
    """整单排程与既有占用或检修封锁冲突。"""

    def __init__(self, message, conflicts, status_code=409):
        super().__init__(message)
        self.message = message
        self.conflicts = conflicts
        self.status_code = status_code


def _is_sqlite():
    return engine.dialect.name == 'sqlite'


def _fmt(ts):
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')


def _bump_guard(db):
    """排程写事务的第一条语句：更新守卫行并持锁到提交。

    MySQL 下该 UPDATE 取得行锁；SQLite 下取得写锁。后续“查冲突 + 写入”都在锁内，
    并发确认/改期因此不会同时看到空位。
    """
    changed = db.execute(
        update(ScheduleGuard)
        .where(ScheduleGuard.id == 1)
        .values(revision=ScheduleGuard.revision + 1, updated_at=time.time())
    ).rowcount
    if changed:
        return
    try:
        with db.begin_nested():
            db.execute(insert(ScheduleGuard).values(id=1, revision=1, updated_at=time.time()))
    except IntegrityError:
        db.execute(
            update(ScheduleGuard)
            .where(ScheduleGuard.id == 1)
            .values(revision=ScheduleGuard.revision + 1, updated_at=time.time())
        )


@contextmanager
def schedule_guard(db):
    if _is_sqlite():
        _SQLITE_LOCK.acquire()
    try:
        # 鉴权等前置查询可能已开启只读事务。先结束它，让下面的守卫 UPDATE 成为本事务
        # 的第一条语句：MySQL 直接取行锁，SQLite 直接取写锁，避免“读锁升级”相互卡死。
        # 各调用方在进入守卫前都不写库，回滚不会丢失业务数据。
        db.rollback()
        _bump_guard(db)
        # 拿锁后丢弃 identity map 里的旧快照，后续查冲突读到的是已提交的最新占用。
        db.expire_all()
        yield
    except BaseException:
        db.rollback()
        raise
    finally:
        if _is_sqlite():
            _SQLITE_LOCK.release()


def _load(db, trial_id):
    trial = db.get(PilotTrial, trial_id)
    if not trial:
        raise HTTPException(404, '小试单不存在')
    return trial


def _event(db, trial_id, actor, action, detail=None):
    db.add(TrialEvent(trial_id=trial_id, actor=actor, action=action, detail=detail or {}, at=time.time()))


def _audit(db, actor, action, trial_id, **detail):
    record(db, actor, action, {'trial_id': trial_id, **detail})


def _overlaps(a_start, a_end, b_start, b_end):
    # 半开区间：前一段结束时间等于后一段开始时间时允许衔接。
    return a_start < b_end and b_start < a_end


def _normalize_entries(db, entries):
    """entries: [{reactor_id, start_at: float, end_at: float}] → 带清洗占用的整单明细。"""
    if not entries:
        raise HTTPException(422, '至少需要为整单安排一个实验釜和起止时间')
    reactors = {r.id: r for r in db.scalars(select(Reactor))}
    items = []
    for index, raw in enumerate(entries):
        reactor = reactors.get(raw['reactor_id'])
        label = f'第 {index + 1} 段'
        if not reactor:
            raise HTTPException(422, f'{label}：实验釜不存在')
        if not reactor.active:
            raise HTTPException(422, f'{label}：实验釜 {reactor.code} 已停用')
        start_at = raw['start_at']
        end_at = raw['end_at']
        if end_at <= start_at:
            raise HTTPException(422, f'{label}：结束时间必须晚于开始时间')
        if end_at - start_at < MIN_SLOT_SECONDS:
            raise HTTPException(422, f'{label}：单段排程至少 1 分钟')
        if end_at - start_at > MAX_SLOT_SECONDS:
            raise HTTPException(422, f'{label}：单段排程不能超过 30 天')
        cleaning = raw.get('cleaning_minutes')
        if cleaning is None:
            cleaning = reactor.cleaning_minutes
        cleaning = max(0, int(cleaning or 0))
        items.append({
            'reactor': reactor,
            'reactor_id': reactor.id,
            'start_at': start_at,
            'end_at': end_at,
            'cleaning_minutes': cleaning,
            'occupied_until': end_at + cleaning * 60,
            'position': index,
        })
    for i, left in enumerate(items):
        for right in items[i + 1:]:
            if left['reactor_id'] != right['reactor_id']:
                continue
            if _overlaps(left['start_at'], left['occupied_until'], right['start_at'], right['occupied_until']):
                raise HTTPException(
                    422,
                    f'整单内的第 {left["position"] + 1}、{right["position"] + 1} 段在实验釜 '
                    f'{left["reactor"].code} 上互相重叠（清洗时段也计入占用）',
                )
    return items


def _normalize_slots(db, raw_slots):
    if not raw_slots:
        raise HTTPException(422, '至少需要为整单安排一个实验釜和起止时间')
    parsed = []
    for index, raw in enumerate(raw_slots):
        try:
            parsed.append({
                'reactor_id': raw.reactor_id,
                'start_at': parse_local(raw.start_at),
                'end_at': parse_local(raw.end_at),
            })
        except ValueError as exc:
            raise HTTPException(422, f'第 {index + 1} 段：{exc}') from exc
    return _normalize_entries(db, parsed)


def _conflict(kind, item, occ_start, occ_end, source_id=None, source_name='', source_status='', reason=''):
    reactor = item['reactor']
    overlap_start = max(item['start_at'], occ_start)
    overlap_end = min(item['occupied_until'], occ_end)
    if kind == 'trial':
        message = (
            f'实验釜 {reactor.code} 在 {_fmt(occ_start)}–{_fmt(occ_end)} 已被小试单 '
            f'#{source_id}「{source_name}」占用（含清洗时段）'
        )
    else:
        message = (
            f'实验釜 {reactor.code} 在 {_fmt(occ_start)}–{_fmt(occ_end)} 处于检修封锁'
            f'「{reason or "未填写原因"}」'
        )
    return {
        'conflict_type': kind,
        'reactor_id': reactor.id,
        'reactor_code': reactor.code,
        'reactor_name': reactor.name,
        'source_id': source_id,
        'source_name': source_name,
        'source_status': source_status,
        'reason': reason,
        'requested': {
            'start_at': item['start_at'],
            'end_at': item['end_at'],
            'occupied_until': item['occupied_until'],
            'display': f'{_fmt(item["start_at"])}–{_fmt(item["occupied_until"])}',
        },
        'occupied': {
            'start_at': occ_start,
            'end_at': occ_end,
            'display': f'{_fmt(occ_start)}–{_fmt(occ_end)}',
        },
        'overlap': {
            'start_at': overlap_start,
            'end_at': overlap_end,
            'display': f'{_fmt(overlap_start)}–{_fmt(overlap_end)}',
        },
        'message': message,
    }


def _conflict_summary(conflicts, limit=3):
    """把冲突来源拼成一句可直接展示的提示，完整明细仍随 conflicts 返回。"""
    messages = [item['message'] for item in conflicts[:limit]]
    more = len(conflicts) - len(messages)
    return '；'.join(messages) + (f'；另有 {more} 处冲突' if more > 0 else '')


def _trial_conflicts(db, items, exclude_trial_id=None):
    reactor_ids = {item['reactor_id'] for item in items}
    if not reactor_ids:
        return []
    stmt = (
        select(PilotSlot, PilotTrial)
        .join(PilotTrial, PilotSlot.trial_id == PilotTrial.id)
        .where(
            PilotSlot.reactor_id.in_(reactor_ids),
            PilotSlot.released_at.is_(None),
            PilotTrial.status.in_(OCCUPYING),
        )
    )
    if exclude_trial_id is not None:
        stmt = stmt.where(PilotSlot.trial_id != exclude_trial_id)
    found = []
    for slot, trial in db.execute(stmt).all():
        occ_end = slot.end_at + (slot.cleaning_minutes or 0) * 60
        for item in items:
            if item['reactor_id'] != slot.reactor_id:
                continue
            if _overlaps(item['start_at'], item['occupied_until'], slot.start_at, occ_end):
                found.append(_conflict(
                    'trial', item, slot.start_at, occ_end,
                    source_id=trial.id, source_name=trial.name, source_status=trial.status,
                ))
    return found


def _maintenance_conflicts(db, items, exclude_maintenance_id=None):
    reactor_ids = {item['reactor_id'] for item in items}
    if not reactor_ids:
        return []
    stmt = select(MaintenanceWindow).where(
        MaintenanceWindow.reactor_id.in_(reactor_ids),
        MaintenanceWindow.status == 'active',
    )
    if exclude_maintenance_id is not None:
        stmt = stmt.where(MaintenanceWindow.id != exclude_maintenance_id)
    found = []
    for window in db.scalars(stmt):
        for item in items:
            if item['reactor_id'] != window.reactor_id:
                continue
            if _overlaps(item['start_at'], item['occupied_until'], window.start_at, window.end_at):
                found.append(_conflict(
                    'maintenance', item, window.start_at, window.end_at,
                    source_id=window.id, source_name=window.reason, source_status=window.status,
                    reason=window.reason,
                ))
    return found


def _find_conflicts(db, items, exclude_trial_id=None, include_maintenance=True):
    conflicts = _trial_conflicts(db, items, exclude_trial_id)
    if include_maintenance:
        conflicts.extend(_maintenance_conflicts(db, items))
    conflicts.sort(key=lambda c: (c['occupied']['start_at'], c['reactor_code'], c['conflict_type']))
    return conflicts


def _active_slots(db, trial_id):
    return list(db.scalars(
        select(PilotSlot)
        .where(PilotSlot.trial_id == trial_id, PilotSlot.released_at.is_(None))
        .order_by(PilotSlot.position, PilotSlot.id)
    ))


def _all_slots(db, trial_id):
    return list(db.scalars(
        select(PilotSlot)
        .where(PilotSlot.trial_id == trial_id)
        .order_by(PilotSlot.position, PilotSlot.id)
    ))


def _same_schedule(existing, items):
    if len(existing) != len(items):
        return False
    left = sorted((s.reactor_id, s.start_at, s.end_at, s.cleaning_minutes) for s in existing)
    right = sorted((i['reactor_id'], i['start_at'], i['end_at'], i['cleaning_minutes']) for i in items)
    return left == right


def _replace_slots(db, trial, items):
    now = time.time()
    for slot in _active_slots(db, trial.id):
        slot.released_at = now
    for item in items:
        db.add(PilotSlot(
            trial_id=trial.id,
            reactor_id=item['reactor_id'],
            start_at=item['start_at'],
            end_at=item['end_at'],
            cleaning_minutes=item['cleaning_minutes'],
            position=item['position'],
        ))
    db.flush()


def _slot_dict(slot, reactor):
    return {
        'id': slot.id,
        'trial_id': slot.trial_id,
        'reactor_id': slot.reactor_id,
        'reactor_code': reactor.code if reactor else '',
        'reactor_name': reactor.name if reactor else '',
        'start_at': slot.start_at,
        'end_at': slot.end_at,
        'cleaning_minutes': slot.cleaning_minutes,
        'occupied_until': slot.end_at + (slot.cleaning_minutes or 0) * 60,
        'released_at': slot.released_at,
        'active': slot.released_at is None,
        'position': slot.position,
    }


def _reactor_map(db):
    return {r.id: r for r in db.scalars(select(Reactor))}


def trial_dict(db, trial, conflicts=None, warnings=None):
    reactors = _reactor_map(db)
    slots = [_slot_dict(s, reactors.get(s.reactor_id)) for s in _all_slots(db, trial.id)]
    schedule = [s for s in slots if s['active']]
    status = trial.status
    return {
        'id': trial.id,
        'name': trial.name,
        'job_id': trial.job_id,
        'cluster': trial.cluster,
        'conditions': trial.conditions or {},
        'context': trial.context or {},
        'status': status,
        'version': trial.version,
        'created_by': trial.created_by,
        'created_by_name': trial.created_by_name,
        'created_at': trial.created_at,
        'confirmed_at': trial.confirmed_at,
        'started_at': trial.started_at,
        'cancelled_at': trial.cancelled_at,
        'frozen_at': trial.frozen_at,
        'frozen_conditions': trial.frozen_conditions or {},
        'frozen_schedule': trial.frozen_schedule or [],
        'slots': slots,
        'schedule': schedule,
        'occupying': status in OCCUPYING,
        'editable': status in EDITABLE,
        'can_confirm': status == TrialStatus.draft.value,
        'can_reschedule': status in EDITABLE,
        'can_start': status == TrialStatus.confirmed.value,
        'can_cancel': status in EDITABLE,
        'conflicts': conflicts or [],
        'warnings': warnings or [],
    }


def create(db, data, user):
    job = db.get(Job, data.job_id)
    if not job:
        raise HTTPException(404, '分析任务不存在')
    if job.status != 'completed':
        raise HTTPException(409, '仅已完成的分析结果可以创建小试单')
    if not job.approved:
        raise HTTPException(409, '该分析结果尚未获业务负责人批准，不能创建小试单')
    rows = (job.result or {}).get('table', {}).get('rows', [])
    row = next((item for item in rows if item.get('cluster') == data.cluster), None)
    if not row:
        raise HTTPException(422, '所选工况族群不在该获批分析结果中')
    conditions = {key: row.get(key) for key in FEATURES}
    if any(not isinstance(conditions[key], (int, float)) or isinstance(conditions[key], bool) for key in FEATURES):
        raise HTTPException(422, '该工况族群的中心条件不完整，无法固定工况')
    conditions['count'] = row.get('count')
    conditions['share_pct'] = row.get('share_pct')
    dataset = db.get(Dataset, job.dataset_id)
    trial = PilotTrial(
        name=data.name.strip(),
        job_id=job.id,
        cluster=data.cluster,
        conditions=conditions,
        context={
            'job_name': job.name,
            'job_id': job.id,
            'dataset_id': job.dataset_id,
            'dataset_name': dataset.name if dataset else '',
            'dataset_checksum': dataset.checksum if dataset else '',
            'params': job.params,
            'result_checksum': checksum(job.result or {}),
            'result_summary': (job.result or {}).get('summary', ''),
            'job_finished_at': job.finished_at,
            'approved': True,
        },
        status=TrialStatus.draft.value,
        version=1,
        created_by=user.id,
        created_by_name=user.name,
        created_at=time.time(),
        frozen_conditions=deepcopy(conditions),
    )
    db.add(trial)
    db.flush()
    _event(db, trial.id, user.email, 'create', {
        'cluster': trial.cluster,
        'conditions': conditions,
        'job_id': job.id,
        'result_checksum': trial.context['result_checksum'],
    })
    _audit(db, user.email, 'trial.create', trial.id, cluster=trial.cluster, job_id=job.id)
    db.commit()
    return trial_dict(db, trial)


def check(db, trial_id, data):
    trial = _load(db, trial_id)
    items = _normalize_slots(db, data.slots)
    conflicts = _find_conflicts(db, items, exclude_trial_id=trial.id)
    reactors = {item['reactor_id']: item['reactor'] for item in items}
    preview = [{
        'reactor_id': item['reactor_id'],
        'reactor_code': reactors[item['reactor_id']].code,
        'reactor_name': reactors[item['reactor_id']].name,
        'start_at': item['start_at'],
        'end_at': item['end_at'],
        'cleaning_minutes': item['cleaning_minutes'],
        'occupied_until': item['occupied_until'],
        'position': item['position'],
    } for item in items]
    return {'ok': not conflicts, 'conflicts': conflicts, 'schedule': preview}


def schedule(db, trial_id, data, user):
    with schedule_guard(db):
        trial = _load(db, trial_id)
        if trial.status == TrialStatus.started.value:
            raise HTTPException(409, '小试单已开试，工况和排程已冻结，不能再改期')
        if trial.status == TrialStatus.cancelled.value:
            raise HTTPException(409, '小试单已取消，不能继续排程')
        items = _normalize_slots(db, data.slots)
        existing = _active_slots(db, trial.id)
        if _same_schedule(existing, items):
            db.commit()
            return {**trial_dict(db, trial), 'changed': False, 'conflicts': []}
        rescheduling = trial.status == TrialStatus.confirmed.value
        # 草稿允许多次保存（包括暂有冲突的计划）；已确认单的改期同样整单预检，
        # 但任一冲突都不替换原排程，只在历史里留 rejection。
        conflicts = _find_conflicts(db, items, exclude_trial_id=trial.id)
        if rescheduling and conflicts:
            detail = {
                'from': [str(s.start_at) for s in existing],
                'to': [{k: v for k, v in item.items() if k != 'reactor'} for item in items],
                'conflicts': conflicts,
            }
            _event(db, trial.id, user.email, 'reschedule.rejected', detail)
            _audit(db, user.email, 'trial.reschedule.rejected', trial.id, conflicts=len(conflicts))
            # 先落历史再抛冲突；排程替换尚未开始，原占用完整保留。
            db.commit()
            raise ScheduleConflict(
                '整单改期失败：所选时段存在占用或检修封锁，原排程保持不变。'
                + _conflict_summary(conflicts),
                conflicts,
            )
        _replace_slots(db, trial, items)
        trial.version += 1
        action = 'reschedule' if rescheduling else 'schedule'
        _event(db, trial.id, user.email, action, {
            'from': [{
                'reactor_id': s.reactor_id,
                'start_at': s.start_at,
                'end_at': s.end_at,
                'cleaning_minutes': s.cleaning_minutes,
            } for s in existing],
            'to': [{
                'reactor_id': item['reactor_id'],
                'start_at': item['start_at'],
                'end_at': item['end_at'],
                'cleaning_minutes': item['cleaning_minutes'],
            } for item in items],
            'note': data.note,
            'conflicts_at_draft': len(conflicts),
        })
        _audit(db, user.email, f'trial.{action}', trial.id, segments=len(items))
        db.commit()
        # 草稿允许保存冲突计划；把检查结果作为 warning 返回，确认时会再次拦截。
        return {**trial_dict(db, trial, warnings=conflicts if not rescheduling else []), 'changed': True}


def confirm(db, trial_id, user, note=''):
    with schedule_guard(db):
        trial = _load(db, trial_id)
        if trial.status in OCCUPYING:
            return {**trial_dict(db, trial), 'replayed': True}
        if trial.status == TrialStatus.cancelled.value:
            raise HTTPException(409, '小试单已取消，不能确认占用')
        items_from_slots = _active_slots(db, trial.id)
        if not items_from_slots:
            raise HTTPException(422, '整单尚未安排实验釜和时间段，不能确认')
        items = _normalize_entries(db, [{
            'reactor_id': slot.reactor_id,
            'start_at': slot.start_at,
            'end_at': slot.end_at,
            'cleaning_minutes': slot.cleaning_minutes,
        } for slot in items_from_slots])
        conflicts = _find_conflicts(db, items, exclude_trial_id=trial.id)
        if conflicts:
            _event(db, trial.id, user.email, 'confirm.rejected', {
                'conflicts': conflicts,
                'note': note,
            })
            _audit(db, user.email, 'trial.confirm.rejected', trial.id, conflicts=len(conflicts))
            db.commit()
            raise ScheduleConflict(
                '确认失败：整单并非所有时段都有空位，本次未占用任何实验釜。'
                + _conflict_summary(conflicts),
                conflicts,
            )
        trial.status = TrialStatus.confirmed.value
        trial.confirmed_at = time.time()
        trial.version += 1
        _event(db, trial.id, user.email, 'confirm', {'segments': len(items), 'note': note})
        _audit(db, user.email, 'trial.confirm', trial.id, segments=len(items))
        # 槽位在草稿阶段已落库，确认只切换状态；提交后重试会命中幂等分支，不会重复占用。
        db.commit()
        return {**trial_dict(db, trial), 'replayed': False}


def start(db, trial_id, user, note=''):
    with schedule_guard(db):
        trial = _load(db, trial_id)
        if trial.status == TrialStatus.started.value:
            return {**trial_dict(db, trial), 'replayed': True}
        if trial.status == TrialStatus.cancelled.value:
            raise HTTPException(409, '小试单已取消，不能开试')
        if trial.status != TrialStatus.confirmed.value:
            raise HTTPException(409, '小试单尚未确认占用，不能开试')
        slots = _active_slots(db, trial.id)
        if not slots:
            raise HTTPException(409, '整单没有有效排程，不能开试')
        trial.status = TrialStatus.started.value
        trial.started_at = time.time()
        trial.frozen_at = trial.started_at
        trial.frozen_conditions = deepcopy(trial.conditions or {})
        reactors = _reactor_map(db)
        trial.frozen_schedule = [{
            'reactor_id': slot.reactor_id,
            'reactor_code': reactors[slot.reactor_id].code if slot.reactor_id in reactors else '',
            'reactor_name': reactors[slot.reactor_id].name if slot.reactor_id in reactors else '',
            'start_at': slot.start_at,
            'end_at': slot.end_at,
            'cleaning_minutes': slot.cleaning_minutes,
            'occupied_until': slot.end_at + (slot.cleaning_minutes or 0) * 60,
            'position': slot.position,
        } for slot in slots]
        trial.version += 1
        _event(db, trial.id, user.email, 'start', {
            'frozen_conditions': trial.frozen_conditions,
            'frozen_schedule': trial.frozen_schedule,
            'note': note,
        })
        _audit(db, user.email, 'trial.start', trial.id)
        db.commit()
        return {**trial_dict(db, trial), 'replayed': False}


def cancel(db, trial_id, user, reason=''):
    with schedule_guard(db):
        trial = _load(db, trial_id)
        if trial.status == TrialStatus.cancelled.value:
            return {**trial_dict(db, trial), 'replayed': True}
        if trial.status == TrialStatus.started.value:
            raise HTTPException(409, '小试单已开试，不能取消；请保留实际占用与清洗记录')
        released = _active_slots(db, trial.id)
        now = time.time()
        for slot in released:
            slot.released_at = now
        trial.status = TrialStatus.cancelled.value
        trial.cancelled_at = now
        trial.version += 1
        _event(db, trial.id, user.email, 'cancel', {
            'reason': reason,
            'released_slots': len(released),
        })
        _audit(db, user.email, 'trial.cancel', trial.id, released=len(released))
        # 释放与状态切换同事务提交；提交后重试走幂等分支，不会重复释放或重新占用。
        db.commit()
        return {**trial_dict(db, trial), 'replayed': False}


def history(db, trial_id):
    _load(db, trial_id)
    return [{
        'id': event.id,
        'trial_id': event.trial_id,
        'actor': event.actor,
        'action': event.action,
        'detail': event.detail,
        'at': event.at,
    } for event in db.scalars(
        select(TrialEvent).where(TrialEvent.trial_id == trial_id).order_by(TrialEvent.id.desc())
    )]


def detail(db, trial_id):
    """详情附带当前排程的冲突来源，方便页面在刷新后仍能看到被拒原因。"""
    trial = _load(db, trial_id)
    conflicts = []
    if trial.status in EDITABLE:
        slots = _active_slots(db, trial.id)
        if slots:
            try:
                items = _normalize_entries(db, [{
                    'reactor_id': slot.reactor_id,
                    'start_at': slot.start_at,
                    'end_at': slot.end_at,
                    'cleaning_minutes': slot.cleaning_minutes,
                } for slot in slots])
                conflicts = _find_conflicts(db, items, exclude_trial_id=trial.id)
            except HTTPException:
                conflicts = []
    return trial_dict(db, trial, warnings=conflicts)


def listing(db):
    trials = list(db.scalars(select(PilotTrial).order_by(PilotTrial.id.desc()).limit(100)))
    return [trial_dict(db, trial) for trial in trials]


def board(db, start_at=None, end_at=None):
    now = time.time()
    low = start_at if start_at is not None else now - 7 * 86400
    high = end_at if end_at is not None else now + 30 * 86400
    if high <= low:
        raise HTTPException(422, '结束时间必须晚于开始时间')
    reactors = list(db.scalars(select(Reactor).order_by(Reactor.code)))
    occupancies = []
    for slot, trial in db.execute(
        select(PilotSlot, PilotTrial)
        .join(PilotTrial, PilotSlot.trial_id == PilotTrial.id)
        .where(
            PilotSlot.released_at.is_(None),
            PilotTrial.status.in_(OCCUPYING),
            PilotSlot.start_at < high,
        )
        .order_by(PilotSlot.start_at)
    ).all():
        reactor = next((r for r in reactors if r.id == slot.reactor_id), None)
        occupied_until = slot.end_at + (slot.cleaning_minutes or 0) * 60
        if occupied_until <= low:
            continue
        occupancies.append({
            'kind': 'trial',
            'trial_id': trial.id,
            'trial_name': trial.name,
            'trial_status': trial.status,
            'slot_id': slot.id,
            'reactor_id': slot.reactor_id,
            'reactor_code': reactor.code if reactor else '',
            'start_at': slot.start_at,
            'end_at': slot.end_at,
            'cleaning_minutes': slot.cleaning_minutes,
            'occupied_until': occupied_until,
        })
    maintenance = []
    for window in db.scalars(select(MaintenanceWindow).where(MaintenanceWindow.status == 'active')):
        if window.end_at <= low or window.start_at >= high:
            continue
        reactor = next((r for r in reactors if r.id == window.reactor_id), None)
        maintenance.append({
            'kind': 'maintenance',
            'maintenance_id': window.id,
            'reactor_id': window.reactor_id,
            'reactor_code': reactor.code if reactor else '',
            'start_at': window.start_at,
            'end_at': window.end_at,
            'reason': window.reason,
            'created_by_name': window.created_by_name,
        })
    return {
        'from': low,
        'to': high,
        'reactors': [{
            'id': reactor.id,
            'code': reactor.code,
            'name': reactor.name,
            'cleaning_minutes': reactor.cleaning_minutes,
            'active': reactor.active,
            'notes': reactor.notes,
        } for reactor in reactors],
        'occupancies': occupancies,
        'maintenance': maintenance,
    }


def maintenance_listing(db):
    reactors = _reactor_map(db)
    return [{
        'id': window.id,
        'reactor_id': window.reactor_id,
        'reactor_code': reactors[window.reactor_id].code if window.reactor_id in reactors else '',
        'start_at': window.start_at,
        'end_at': window.end_at,
        'reason': window.reason,
        'status': window.status,
        'created_by_name': window.created_by_name,
        'created_at': window.created_at,
        'released_at': window.released_at,
    } for window in db.scalars(select(MaintenanceWindow).order_by(MaintenanceWindow.id.desc()).limit(100))]


def create_maintenance(db, data, user):
    with schedule_guard(db):
        reactor = db.get(Reactor, data.reactor_id)
        if not reactor:
            raise HTTPException(422, '实验釜不存在')
        try:
            start_at = parse_local(data.start_at)
            end_at = parse_local(data.end_at)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if end_at <= start_at:
            raise HTTPException(422, '检修结束时间必须晚于开始时间')
        if end_at - start_at > MAX_MAINTENANCE_SECONDS:
            raise HTTPException(422, '单个检修封锁不能超过 365 天')
        item = {
            'reactor': reactor,
            'reactor_id': reactor.id,
            'start_at': start_at,
            'end_at': end_at,
            'cleaning_minutes': 0,
            'occupied_until': end_at,
            'position': 0,
        }
        # 检修封锁反向检查已有小试占用：不能覆盖任何已确认/已开试的釜时（含清洗）。
        conflicts = _trial_conflicts(db, [item]) + _maintenance_conflicts(db, [item])
        if conflicts:
            record(db, user.email, 'maintenance.rejected', {
                'reactor_id': reactor.id,
                'start_at': start_at,
                'end_at': end_at,
                'conflicts': len(conflicts),
            })
            db.commit()
            raise ScheduleConflict(
                '检修封锁失败：该时段已有小试占用或另一段检修，未覆盖任何已有安排。'
                + _conflict_summary(conflicts),
                conflicts,
            )
        window = MaintenanceWindow(
            reactor_id=reactor.id,
            start_at=start_at,
            end_at=end_at,
            reason=data.reason.strip(),
            status='active',
            created_by=user.id,
            created_by_name=user.name,
            created_at=time.time(),
        )
        db.add(window)
        db.flush()
        record(db, user.email, 'maintenance.create', {
            'maintenance_id': window.id,
            'reactor_id': reactor.id,
            'start_at': start_at,
            'end_at': end_at,
        })
        db.commit()
        return next(item for item in maintenance_listing(db) if item['id'] == window.id)


def release_maintenance(db, maintenance_id, user, reason=''):
    with schedule_guard(db):
        window = db.get(MaintenanceWindow, maintenance_id)
        if not window:
            raise HTTPException(404, '检修封锁不存在')
        if window.status == 'released':
            return next(item for item in maintenance_listing(db) if item['id'] == maintenance_id)
        window.status = 'released'
        window.released_at = time.time()
        record(db, user.email, 'maintenance.release', {
            'maintenance_id': window.id,
            'reason': reason,
        })
        db.commit()
        return next(item for item in maintenance_listing(db) if item['id'] == maintenance_id)
