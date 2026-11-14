"""Resolve pre-V9 pending answers against current references, preserving manual adjustments.
Run once after backing up database: python -m scripts.migrate_instant_v9
Idempotent: subsequent runs find no pending answers.
"""
from sqlalchemy import select
from app.core import Session
from app.models import Answer, Assignment, User
from app.services import evaluate_answer, reconcile

def main():
    changed=0; awarded_total=0
    with Session.begin() as db:
        pending_ids=db.scalars(select(Answer.id).where(Answer.validation_status=='pending').order_by(Answer.id)).all()
    for answer_id in pending_ids:
        with Session.begin() as db:
            answer=db.scalar(select(Answer).where(Answer.id==answer_id).with_for_update())
            if answer is None or answer.validation_status!='pending':continue
            user=db.scalar(select(User).where(User.id==answer.user_id).with_for_update())
            assignment=db.get(Assignment,answer.assignment_id)
            if not user or not assignment:
                # Preserve irregular historical records rather than deleting or changing them.
                continue
            points,status=evaluate_answer(db,assignment,answer.translation,answer.action)
            difference=points-answer.points_awarded
            if user.points+difference<0:
                # Do not break minimum balance; log and let operator investigate.
                print(f'SKIP answer #{answer.id}: balance would be negative')
                continue
            answer.points_awarded=points
            answer.validation_status=status
            user.points+=difference
            db.flush()
            reconcile(db,answer.fragment_id,answer.lang_pair)
            changed+=1;awarded_total+=difference
    print(f'V9 migration finished: {changed} historical pending answers resolved; points change {awarded_total:+d}')
if __name__=='__main__':main()
