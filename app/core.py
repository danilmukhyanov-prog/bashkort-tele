import os
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from passlib.context import CryptContext

DB_URL=os.getenv('DATABASE_URL','postgresql+psycopg://school:school_change_me@localhost:5432/school')
engine=create_engine(DB_URL, pool_pre_ping=True)
Session=sessionmaker(bind=engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
pwd=CryptContext(schemes=['bcrypt'], deprecated='auto')
def utcnow(): return datetime.now(timezone.utc)
def session():
    with Session() as db:
        yield db
