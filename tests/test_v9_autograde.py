"""Run alongside V8 integration tests. Existing V8 tests set up isolated DB."""
from app.services import evaluate_answer
from app.models import Source, Fragment, Suggestion, Assignment
from app.core import Session

def test_v9_instant_known_and_unknown():
    with Session.begin() as db:
        src=Source(title='Проверка V9',kind='text'); db.add(src); db.flush()
        f=Fragment(source_id=src.id,source_key='instant-test',text_ba='Нимә?',coordinates={}); db.add(f);db.flush()
        db.add(Suggestion(fragment_id=f.id,lang_pair='ba-ru',translation='Что?',is_reference=True,origin='dataset'))
        db.flush()
        good=Assignment(fragment_id=f.id,lang_pair='ba-ru',shown_translation='Что?',user_id=9999,created_date='2000-01-01')
        wrong=Assignment(fragment_id=f.id,lang_pair='ba-ru',shown_translation='Кто?',user_id=9999,created_date='2000-01-02')
        assert evaluate_answer(db,good,'Что?','confirm')==(10,'approved')
        assert evaluate_answer(db,good,'Кто?','correct')==(0,'rejected')
        assert evaluate_answer(db,wrong,'Что','correct')==(15,'approved')
        assert evaluate_answer(db,wrong,'Кто?','confirm')==(0,'rejected')
