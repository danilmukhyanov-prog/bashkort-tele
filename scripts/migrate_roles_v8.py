"""Safe, repeatable V7 -> V8 upgrade. Run after backing up PostgreSQL.

The app must not be serving traffic during migration. No existing data is deleted.
"""
from sqlalchemy import inspect, text, select
from app.core import engine, Session, Base
from app.models import User, ClassRoom, ClassMembership

def main():
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('Production migration supports PostgreSQL only')
    with engine.begin() as conn:
        cols = {c['name'] for c in inspect(conn).get_columns('classrooms')}
        if 'teacher_id' not in cols:
            conn.execute(text('ALTER TABLE classrooms ADD COLUMN teacher_id INTEGER REFERENCES users(id)'))
        conn.execute(text('CREATE INDEX IF NOT EXISTS ix_classrooms_teacher_id ON classrooms(teacher_id)'))
        conn.execute(text("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='users_points_nonnegative') THEN ALTER TABLE users ADD CONSTRAINT users_points_nonnegative CHECK (points >= 0); END IF; END $$;"))
    Base.metadata.create_all(engine)
    owners=0; members=0; unassigned=[]
    with Session.begin() as db:
        for room in db.scalars(select(ClassRoom).order_by(ClassRoom.id)).all():
            if room.teacher_id is None:
                # Historical V7 teacher.class_id identifies only one class. Do not guess ownership
                # for orphaned classrooms: an administrator can assign them explicitly later.
                teacher=db.scalar(select(User).where(User.role=='teacher',User.class_id==room.id,
                              User.school_id==room.school_id).order_by(User.id))
                if teacher:
                    room.teacher_id=teacher.id;owners+=1
                else:
                    unassigned.append(room.id)
        for user in db.scalars(select(User).where(User.role=='student',User.class_id.is_not(None))).all():
            room=db.get(ClassRoom,user.class_id)
            if not room or room.school_id!=user.school_id:
                continue
            if not db.scalar(select(ClassMembership.id).where(ClassMembership.user_id==user.id)):
                db.add(ClassMembership(classroom_id=room.id,user_id=user.id));members+=1
    print(f'V8: {owners} class owners identified; {members} existing student memberships imported.')
    if unassigned:print('Classes without confirmed owner (admin action required):',','.join(map(str,unassigned)))
    print('No users, answers, points or translations removed.')

if __name__=='__main__':main()
