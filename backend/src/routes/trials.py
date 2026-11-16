from fastapi import APIRouter, Depends, HTTPException
from src.config.database import get_db
from src.middlewares.auth import current_user, admin_only
from src.schemas.trial import (
    CancelInput,
    MaintenanceInput,
    MaintenanceRelease,
    ScheduleInput,
    TrialCreate,
)
from src.services import trials

router = APIRouter(prefix='/trials')


@router.get('')
def listing(db=Depends(get_db), user=Depends(current_user)):
    return trials.listing(db)


@router.get('/board')
def board(db=Depends(get_db), user=Depends(current_user)):
    return trials.board(db)


@router.get('/maintenance')
def maintenance(db=Depends(get_db), user=Depends(current_user)):
    return trials.maintenance_listing(db)


@router.post('/maintenance', status_code=201)
def create_maintenance(data: MaintenanceInput, db=Depends(get_db), user=Depends(admin_only)):
    return trials.create_maintenance(db, data, user)


@router.post('/maintenance/{maintenance_id}/release')
def release_maintenance(maintenance_id: int, data: MaintenanceRelease | None = None, db=Depends(get_db), user=Depends(admin_only)):
    return trials.release_maintenance(db, maintenance_id, user, data.reason if data else '')


@router.post('', status_code=201)
def create(data: TrialCreate, db=Depends(get_db), user=Depends(current_user)):
    if user.role not in ('admin', 'analyst'):
        raise HTTPException(403, '观察员只有读取权限')
    return trials.create(db, data, user)


@router.get('/{trial_id}')
def detail(trial_id: int, db=Depends(get_db), user=Depends(current_user)):
    return trials.detail(db, trial_id)


@router.get('/{trial_id}/history')
def history(trial_id: int, db=Depends(get_db), user=Depends(current_user)):
    return trials.history(db, trial_id)


@router.post('/{trial_id}/check')
def check(trial_id: int, data: ScheduleInput, db=Depends(get_db), user=Depends(current_user)):
    return trials.check(db, trial_id, data)


@router.put('/{trial_id}/schedule')
def schedule(trial_id: int, data: ScheduleInput, db=Depends(get_db), user=Depends(admin_only)):
    return trials.schedule(db, trial_id, data, user)


@router.post('/{trial_id}/confirm')
def confirm(trial_id: int, db=Depends(get_db), user=Depends(admin_only)):
    return trials.confirm(db, trial_id, user)


@router.post('/{trial_id}/start')
def start(trial_id: int, db=Depends(get_db), user=Depends(admin_only)):
    return trials.start(db, trial_id, user)


@router.post('/{trial_id}/cancel')
def cancel(trial_id: int, data: CancelInput | None = None, db=Depends(get_db), user=Depends(admin_only)):
    return trials.cancel(db, trial_id, user, data.reason if data else '')

