import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, Reactor, PilotTrial, MaintenanceWindow
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum

REACTORS = [
    ('R-101', '1号实验釜 · 50L', 60, '通用温压工况，适合中温段'),
    ('R-102', '2号实验釜 · 50L', 60, '通用温压工况，与1号同规格'),
    ('R-201', '3号实验釜 · 20L', 45, '小容积快循环，适合短停留试跑'),
    ('R-202', '4号实验釜 · 20L', 45, '小容积快循环，与3号同规格'),
    ('R-301', '5号实验釜 · 100L', 90, '高压大容积，需更长清洗与检查'),
]


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        for code,name,cleaning,notes in REACTORS:
            if not db.scalar(select(Reactor).where(Reactor.code==code)):
                db.add(Reactor(code=code,name=name,cleaning_minutes=cleaning,active=True,notes=notes))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        db.flush()
        from src.services import trials as trial_service
        from src.schemas.trial import TrialCreate, ScheduleInput, SlotInput, MaintenanceInput
        from datetime import datetime, timedelta
        admin=db.scalar(select(User).where(User.role=='admin'))
        analyst=db.scalar(select(User).where(User.role=='analyst'))
        baseline=db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name']))
        # 小试演示单幂等生成：只补缺失的演示单，不覆盖用户改动。
        if baseline and not db.scalar(select(PilotTrial).where(PilotTrial.name=='工况族群2 · 中温复核小试')):
            db.commit()
            base=datetime.now().replace(minute=0,second=0,microsecond=0)+timedelta(days=1)
            rows=baseline.result['table']['rows']
            demo=[('工况族群2 · 中温复核小试', rows[1]['cluster'], 0),('工况族群1 · 中温复核小试', rows[0]['cluster'], 5)]
            for trial_name,cluster,shift in demo:
                created=trial_service.create(db, TrialCreate(name=trial_name,job_id=baseline.id,cluster=cluster), analyst or admin)
                start=base+timedelta(hours=shift)
                def slot(reactor_id,hours):
                    return SlotInput(reactor_id=reactor_id,start_at=(start+timedelta(hours=hours)).isoformat(),end_at=(start+timedelta(hours=hours+3)).isoformat())
                trial_service.schedule(db, created['id'], ScheduleInput(slots=[slot(1,0),slot(3,5)]), admin)
                trial_service.confirm(db, created['id'], admin)
            if not db.scalar(select(MaintenanceWindow)):
                maintenance_start=base+timedelta(days=3)
                trial_service.create_maintenance(db, MaintenanceInput(
                    reactor_id=2,
                    start_at=maintenance_start.isoformat(),
                    end_at=(maintenance_start+timedelta(hours=6)).isoformat(),
                    reason='季度压力表校验',
                ), admin)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,Reactor,PilotTrial,MaintenanceWindow]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
