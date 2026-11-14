from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Boolean, JSON, Float, CheckConstraint
from .core import Base, utcnow
class School(Base):
    __tablename__='schools'
    id=Column(Integer,primary_key=True); name=Column(String(180),unique=True,nullable=False)
class ClassRoom(Base):
    __tablename__='classrooms'
    id=Column(Integer,primary_key=True); name=Column(String(80),nullable=False)
    school_id=Column(Integer,ForeignKey('schools.id'),nullable=False)
    teacher_id=Column(Integer,ForeignKey('users.id'),nullable=True)
    __table_args__=(UniqueConstraint('school_id','name'),)
class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True); email=Column(String(250),unique=True); password_hash=Column(String(250))
    display_name=Column(String(120),nullable=False); role=Column(String(20),default='student',nullable=False)
    school_id=Column(Integer,ForeignKey('schools.id')); class_id=Column(Integer,ForeignKey('classrooms.id'))
    lang_pair=Column(String(12),default='ba-ru',nullable=False)
    telegram_id=Column(String(32),unique=True)
    points=Column(Integer,default=0,nullable=False); created_at=Column(DateTime(timezone=True),default=utcnow)
class Source(Base):
    __tablename__='sources'
    id=Column(Integer,primary_key=True); title=Column(String(250),nullable=False); kind=Column(String(20),nullable=False)
    external_url=Column(Text); license=Column(String(100)); metadata_json=Column(JSON,default=dict)
class Fragment(Base):
    __tablename__='fragments'
    id=Column(Integer,primary_key=True); source_id=Column(Integer,ForeignKey('sources.id'),nullable=False)
    source_key=Column(String(220),nullable=False); text_ba=Column(Text,nullable=False)
    coordinates=Column(JSON,nullable=False,default=dict)
    audio_url=Column(Text)
    __table_args__=(UniqueConstraint('source_id','source_key'),)
class Suggestion(Base):
    __tablename__='suggestions'
    id=Column(Integer,primary_key=True); fragment_id=Column(Integer,ForeignKey('fragments.id'),nullable=False)
    lang_pair=Column(String(12),nullable=False); translation=Column(Text,nullable=False)
    is_reference=Column(Boolean,default=False); origin=Column(String(25),default='machine')
    __table_args__=(UniqueConstraint('fragment_id','lang_pair','translation'),)
class Assignment(Base):
    __tablename__='assignments'
    id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    fragment_id=Column(Integer,ForeignKey('fragments.id'),nullable=False)
    lang_pair=Column(String(12),nullable=False); shown_translation=Column(Text,nullable=False)
    created_date=Column(String(10),nullable=False)
    __table_args__=(UniqueConstraint('user_id','created_date'),)
class Answer(Base):
    __tablename__='answers'
    id=Column(Integer,primary_key=True); assignment_id=Column(Integer,ForeignKey('assignments.id'),unique=True,nullable=False)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    fragment_id=Column(Integer,ForeignKey('fragments.id'),nullable=False)
    lang_pair=Column(String(12),nullable=False); translation=Column(Text,nullable=False)
    action=Column(String(20),nullable=False); points_awarded=Column(Integer,nullable=False,default=0); validation_status=Column(String(20),nullable=False,default='pending'); created_at=Column(DateTime(timezone=True),default=utcnow)
class Translation(Base):
    __tablename__='translations'
    id=Column(Integer,primary_key=True); fragment_id=Column(Integer,ForeignKey('fragments.id'),nullable=False)
    lang_pair=Column(String(12),nullable=False); translation=Column(Text,nullable=False)
    confirmed=Column(Integer,default=0); status=Column(String(20),default='expert_review',nullable=False)
    updated_at=Column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('fragment_id','lang_pair'),)
class Badge(Base):
    __tablename__='badges'
    id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    code=Column(String(40),nullable=False)
    __table_args__=(UniqueConstraint('user_id','code'),)
class BotState(Base):
    __tablename__='bot_states'
    telegram_id=Column(String(32),primary_key=True); state=Column(String(40),nullable=False)
    payload=Column(JSON,default=dict); updated_at=Column(DateTime(timezone=True),default=utcnow)


class ClassMembership(Base):
    __tablename__='class_memberships'
    id=Column(Integer,primary_key=True)
    classroom_id=Column(Integer,ForeignKey('classrooms.id',ondelete='CASCADE'),nullable=False)
    user_id=Column(Integer,ForeignKey('users.id',ondelete='CASCADE'),nullable=False,unique=True)
    joined_at=Column(DateTime(timezone=True),default=utcnow,nullable=False)
    __table_args__=(UniqueConstraint('classroom_id','user_id'),)

class PointAdjustment(Base):
    __tablename__='point_adjustments'
    id=Column(Integer,primary_key=True)
    moderator_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    delta=Column(Integer,nullable=False)
    reason=Column(String(500),nullable=False)
    idempotency_key=Column(String(36),nullable=False)
    balance_after=Column(Integer,nullable=False)
    created_at=Column(DateTime(timezone=True),default=utcnow,nullable=False)
    __table_args__=(CheckConstraint('delta <> 0 AND delta BETWEEN -100 AND 100',name='point_delta_limit'),
                      CheckConstraint('balance_after >= 0',name='point_balance_nonnegative'),
                      UniqueConstraint('moderator_id','idempotency_key'),)
