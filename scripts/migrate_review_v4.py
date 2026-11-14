"""Run once after backup, with the new API container image: python -m scripts.migrate_review_v4.
Reverses unreviewed automatic points and recalculates consensus from approved votes.
Idempotent: safe to run again; keeps users, assignments and replies.
"""
from sqlalchemy import select
from app.core import Session
from app.models import User, Answer, Translation
from app.services import reconcile

def main():
    with Session() as db:
        impacted=set()
        for a in db.scalars(select(Answer)).all():
            impacted.add((a.fragment_id,a.lang_pair))
            if a.validation_status not in ('approved','rejected'):
                a.validation_status='pending'
                a.points_awarded=0
        balances={}
        for a in db.scalars(select(Answer)).all():
            balances[a.user_id]=balances.get(a.user_id,0)+a.points_awarded
        for u in db.scalars(select(User)).all():
            u.points=balances.get(u.id,0)
        for tr in db.scalars(select(Translation)).all():
            impacted.add((tr.fragment_id,tr.lang_pair))
        for fragment_id,lang_pair in impacted:
            reconcile(db,fragment_id,lang_pair)
        db.commit()
        print(f'Updated {len(impacted)} fragment/pair groups; unreviewed answers earn 0 points.')
if __name__=='__main__':
    main()
