import re, hashlib, random
from datetime import date, datetime, timedelta, timezone
from collections import Counter
from sqlalchemy import select, func
from .models import *

LANGS={'ba-ru','ba-kk','ba-alt','ba-ar','ba-sah','ba-zh'}
def normalize(text):
    return ' '.join(re.findall(r'\w+',text.casefold(),flags=re.UNICODE))
def get_or_create_assignment(db,user):
    today=date.today().isoformat()
    old=db.scalar(select(Assignment).where(Assignment.user_id==user.id,Assignment.created_date==today))
    if old:
        # One daily task per user; allow switching languages before submitting the answer.
        if old.lang_pair == user.lang_pair or db.scalar(select(Answer.id).where(Answer.assignment_id == old.id)):
            return old
        db.delete(old)
        db.flush()
    candidates=db.execute(select(Fragment.id).join(Suggestion,Fragment.id==Suggestion.fragment_id).where(Suggestion.lang_pair==user.lang_pair).distinct()).scalars().all()
    if not candidates: return None
    answered=set(db.scalars(select(Answer.fragment_id).where(Answer.user_id==user.id,Answer.lang_pair==user.lang_pair)).all())
    available=[i for i in candidates if i not in answered] or candidates
    seed=int(hashlib.sha256(f'{user.id}:{today}:{user.lang_pair}'.encode()).hexdigest()[:12],16)
    rng=random.Random(seed); fid=rng.choice(available)
    suggestions=db.scalars(select(Suggestion).where(Suggestion.fragment_id==fid,Suggestion.lang_pair==user.lang_pair)).all()
    preferred=next((x for x in suggestions if x.is_reference),suggestions[0])
    # Inject errors ONLY from deliberately curated alternatives, never from arbitrary suggestions.
    bad_variants=[x.translation for x in suggestions if x.origin == 'deliberate_error' and not x.is_reference]
    shown=rng.choice(bad_variants) if seed%5==0 and bad_variants else preferred.translation
    obj=Assignment(user_id=user.id,fragment_id=fid,lang_pair=user.lang_pair,shown_translation=shown,created_date=today)
    db.add(obj);db.flush();return obj

def evaluate_answer(db, assignment, value, action):
    """A corpus reference is a hint, not an independent correctness assessment.

    Until a qualified reviewer verifies an answer, awarding points risks rewarding
    wrong translations and inconsistent imported parallel data.
    """
    return 0, 'pending'


def moderate_answer(db, answer_id, approved):
    """Approve or reject a pending answer; repeated decisions are idempotent."""
    answer = db.get(Answer, answer_id)
    if answer is None:
        raise ValueError('Ответ не найден')
    if answer.validation_status in ('approved', 'rejected'):
        if (answer.validation_status == 'approved') != approved:
            raise ValueError('Ответ уже рассмотрен; изменять решение запрещено')
        return answer
    if answer.validation_status != 'pending':
        raise ValueError('Этот ответ не ожидает модерации')
    points = (15 if answer.action == 'correct' else 10) if approved else 0
    answer.points_awarded = points
    answer.validation_status = 'approved' if approved else 'rejected'
    user = db.get(User, answer.user_id)
    user.points += points
    reconcile(db, answer.fragment_id, answer.lang_pair)
    db.commit()
    return answer

def reconcile(db,fragment_id,lang_pair):
    # One APPROVED vote per user. Rejected, pending and outdated votes cannot
    # establish consensus. A moderator decides semantic equivalence separately.
    answers=db.scalars(select(Answer).where(
        Answer.fragment_id==fragment_id, Answer.lang_pair==lang_pair,
        Answer.validation_status=='approved').order_by(Answer.id)).all()
    latest_by_user={a.user_id:a for a in answers}
    groups={}
    for a in latest_by_user.values():
        norm=normalize(a.translation)
        if norm:
            groups.setdefault(norm,[]).append(a.translation)
    obj=db.scalar(select(Translation).where(
        Translation.fragment_id==fragment_id, Translation.lang_pair==lang_pair))
    if not groups:
        if obj:
            obj.confirmed=0
            obj.status='expert_review'
        return obj
    winner,variants=max(groups.items(),key=lambda x:len(x[1]))
    count=len(variants); total=sum(map(len,groups.values()))
    status='confirmed' if count>=2 and count*2>total else 'expert_review'
    if obj is None:
        obj=Translation(fragment_id=fragment_id,lang_pair=lang_pair)
        db.add(obj)
    obj.translation=Counter(variants).most_common(1)[0][0]
    obj.confirmed=count
    obj.status=status
    obj.updated_at=datetime.now(timezone.utc)
    return obj

def submit(db,user,assignment_id,action,translation=None):
    assignment=db.get(Assignment,assignment_id)
    if not assignment or assignment.user_id!=user.id: raise ValueError('Чужое или несуществующее задание')
    if db.scalar(select(Answer).where(Answer.assignment_id==assignment.id)): raise ValueError('Ответ уже отправлен')
    if action not in {'confirm','correct'}: raise ValueError('Неизвестное действие')
    value=assignment.shown_translation if action=='confirm' else (translation or '').strip()
    if not value or len(value)>5000: raise ValueError('Введите перевод длиной 1–5000 символов')
    awarded, validation = evaluate_answer(db, assignment, value, action)
    answer=Answer(user_id=user.id,assignment_id=assignment.id,fragment_id=assignment.fragment_id,lang_pair=assignment.lang_pair,translation=value,action=action,points_awarded=awarded,validation_status=validation)
    db.add(answer);db.flush()
    user.points += awarded
    n=db.scalar(select(func.count(Answer.id)).where(Answer.user_id==user.id))
    for code,trigger in [('first_answer',1),('five_answers',5),('twenty_answers',20)]:
        if n>=trigger and not db.scalar(select(Badge).where(Badge.user_id==user.id,Badge.code==code)):
            db.add(Badge(user_id=user.id,code=code))
    result=reconcile(db,assignment.fragment_id,assignment.lang_pair)
    db.commit();return {'points':user.points,'awarded':awarded,'validation_status':validation,'level':1+user.points//100,'translation_status':result.status if result else None}

def export_row(db,t):
    f=db.get(Fragment,t.fragment_id);s=db.get(Source,f.source_id)
    return {'fragment_id':f.id,'text_ba':f.text_ba,'lang_pair':t.lang_pair,'translation':t.translation,
            'source':{'title':s.title,'url':s.external_url,'kind':s.kind,'coordinates':f.coordinates},
            'confirmed':t.confirmed,'status':t.status}
