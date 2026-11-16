from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .settings import DATABASE_URL

class Base(DeclarativeBase):
    pass

# SQLite 本地开发时给并发确认/改期留出等待锁的时间；
# MySQL 用 READ COMMITTED，确保取得 schedule_guards 行锁后，后续查冲突看到的是
# 前一个事务已提交的占用，而不是登录查询时建立的 REPEATABLE READ 旧快照。
connect_args = {'timeout': 30, 'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}
engine_kwargs = {'pool_pre_ping': True, 'pool_recycle': 1800, 'connect_args': connect_args}
if not DATABASE_URL.startswith('sqlite'):
    engine_kwargs['isolation_level'] = 'READ COMMITTED'
engine = create_engine(DATABASE_URL, **engine_kwargs)
Session = sessionmaker(engine, expire_on_commit=False)

def get_db():
    with Session() as db:
        yield db
