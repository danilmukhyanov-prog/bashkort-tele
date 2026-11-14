"""One-time, repeatable schema/score migration. Run BEFORE updated api/bot containers.

Usage: docker compose exec api python -m scripts.migrate_scoring
"""
from sqlalchemy import text, select, func
from app.core import engine, Session
from app.models import Answer, Assignment, User, Suggestion, Translation, PointAdjustment
from app.services import normalize


def main():
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE answers ADD COLUMN IF NOT EXISTS points_awarded INTEGER NOT NULL DEFAULT 0'))
        conn.execute(text("ALTER TABLE answers ADD COLUMN IF NOT EXISTS validation_status VARCHAR(20) NOT NULL DEFAULT 'pending'"))
    with Session() as db:
        refs = {}
        for f, pair, translation in db.execute(select(Suggestion.fragment_id, Suggestion.lang_pair, Suggestion.translation).where(Suggestion.is_reference.is_(True))):
            refs.setdefault((f,pair),set()).add(normalize(translation))
        totals = {}
        num=0
        for a in db.scalars(select(Answer).order_by(Answer.id)):
            task = db.get(Assignment,a.assignment_id)
            valid = refs.get((a.fragment_id,a.lang_pair),set())
            matched = normalize(a.translation) in valid if valid else False
            shown_matched = normalize(task.shown_translation) in valid if valid and task else False
            if a.validation_status in ('approved', 'rejected'):
                pts = a.points_awarded
            else:
                pts = 10 if a.action == 'confirm' and matched and shown_matched else 0
                a.points_awarded = pts
                a.validation_status = 'reference_match' if pts else 'pending'
            totals[a.user_id] = totals.get(a.user_id,0) + pts
            num += 1
        for a in db.scalars(select(PointAdjustment)):
            totals[a.user_id]=totals.get(a.user_id,0)+a.delta
        for u in db.scalars(select(User)):
            u.points = totals.get(u.id,0)
        # Do not retain automatically confirmed translations that contradict the source reference.
        for tr in db.scalars(select(Translation).where(Translation.status == 'confirmed')):
            if normalize(tr.translation) not in refs.get((tr.fragment_id,tr.lang_pair),set()):
                tr.status='expert_review'
        db.commit()
        print(f'Migrated {num} answers; recomputed balances for {len(totals)} participants.')
        print('All user corrections are pending independent moderation and earn 0 until approved.')

if __name__ == '__main__':
    main()
