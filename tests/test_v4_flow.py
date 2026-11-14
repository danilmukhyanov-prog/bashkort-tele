from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.core import Base
from app.models import User, Source, Fragment, Suggestion, Assignment, Answer, Translation
from app.services import submit, moderate_answer, reconcile, get_or_create_assignment

def test_review_scoring_and_unique_votes():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session=sessionmaker(bind=engine,expire_on_commit=False)
    with Session() as db:
        users=[User(display_name=f'U{i}',lang_pair='ba-ru',points=0) for i in range(3)]
        src=Source(title='test',kind='text')
        db.add_all([src,*users]);db.flush()
        f=Fragment(source_id=src.id,source_key='x',text_ba='Нимә?',coordinates={})
        db.add(f);db.flush()
        db.add(Suggestion(fragment_id=f.id,lang_pair='ba-ru',translation='Что?',is_reference=True,origin='dataset'))
        db.commit()
        # Corpus confirmation and intentionally incorrect correction both get zero until moderation.
        a1=get_or_create_assignment(db,users[0]);r1=submit(db,users[0],a1.id,'confirm')
        assert r1['awarded']==0 and r1['validation_status']=='pending'
        a2=get_or_create_assignment(db,users[1]);r2=submit(db,users[1],a2.id,'correct','Кто?')
        assert r2['awarded']==0
        db.refresh(users[1]);assert users[1].points==0
        answer1=db.scalar(select(Answer).where(Answer.user_id==users[0].id))
        answer2=db.scalar(select(Answer).where(Answer.user_id==users[1].id))
        moderate_answer(db,answer2.id,False)
        moderate_answer(db,answer1.id,True)
        assert users[0].points==10 and users[1].points==0
        tr=db.scalar(select(Translation).where(Translation.fragment_id==f.id))
        assert tr.status=='expert_review' and tr.confirmed==1
        a3=get_or_create_assignment(db,users[2]);submit(db,users[2],a3.id,'correct','Что?')
        answer3=db.scalar(select(Answer).where(Answer.user_id==users[2].id))
        moderate_answer(db,answer3.id,True)
        db.refresh(tr)
        assert tr.status=='confirmed' and tr.confirmed==2
        assert users[2].points==15
        assert moderate_answer(db,answer3.id,True).points_awarded==15
        assert users[2].points==15

def test_no_fake_marker_from_import():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session=sessionmaker(bind=engine,expire_on_commit=False)
    with Session() as db:
        u=User(display_name='a',lang_pair='ba-ru',points=0)
        s=Source(title='book',kind='text')
        db.add_all([u,s]);db.flush()
        f=Fragment(source_id=s.id,source_key='xx',text_ba='Нимә?',coordinates={})
        db.add(f);db.flush()
        db.add(Suggestion(fragment_id=f.id,lang_pair='ba-ru',translation='Что?',is_reference=True,origin='dataset'))
        db.commit()
        for _ in range(1):
            a=get_or_create_assignment(db,u)
            assert '[проверьте перевод]' not in a.shown_translation
