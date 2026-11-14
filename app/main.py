import os, csv, io, logging, uuid
from datetime import datetime, timezone, date, timedelta
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Header, Query, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session as DBSession
import jwt
from .core import Base, engine, session, pwd
from .models import *
from .services import LANGS, get_or_create_assignment, submit, export_row, moderate_answer, normalize

logging.basicConfig(level=logging.INFO)
app=FastAPI(title='Башкирская языковая школа',version='1.0.0',description='REST API: накопление подтверждённых переводов и LMS-отчёты')
app.mount('/static',StaticFiles(directory='static'),name='static')
SECRET=os.getenv('SECRET_KEY','')
class Credentials(BaseModel): email:str=Field(max_length=250); password:str=Field(min_length=8,max_length=72)
class Register(Credentials): display_name:str=Field(min_length=2,max_length=120); lang_pair:str='ba-ru'; school_id:int|None=None; class_id:int|None=None
class AnswerIn(BaseModel): assignment_id:int; action:Literal['confirm','correct']; translation:str|None=None
class PairIn(BaseModel): lang_pair:str
class SchoolIn(BaseModel): name:str=Field(min_length=2,max_length=180)
class ClassIn(BaseModel): name:str=Field(min_length=1,max_length=80); school_id:int
class StudentIn(Register): role:Literal['student']='student'
class RoleUpdateIn(BaseModel): role:Literal['student','teacher','moderator']
class PointsChangeIn(BaseModel):
    delta:int=Field(strict=True,ge=-100,le=100)
    reason:str=Field(min_length=5,max_length=500)
    idempotency_key:uuid.UUID
    @field_validator('delta')
    @classmethod
    def not_zero(cls,v):
        if v==0:raise ValueError('Изменение баллов не может быть нулевым')
        return v
class TeacherClassIn(BaseModel): name:str=Field(min_length=1,max_length=80)
class MemberIn(BaseModel): student_id:int=Field(gt=0)
class SchoolAssignIn(BaseModel): school_id:int=Field(gt=0)
class TeacherAssignIn(BaseModel): teacher_id:int=Field(gt=0)
class SourceIn(BaseModel): title:str; kind:Literal['text','audio']; external_url:str|None=None; license:str|None=None
class FragmentIn(BaseModel): source_id:int; source_key:str; text_ba:str; coordinates:dict={}; audio_url:str|None=None
class SuggestionIn(BaseModel): fragment_id:int; lang_pair:str; translation:str; is_reference:bool=False

def forbidden(): raise HTTPException(403,'Недостаточно прав')
def user_auth(authorization:str|None=Header(None),db:DBSession=Depends(session)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Требуется авторизация')
    try:
        payload=jwt.decode(authorization[7:],SECRET,algorithms=['HS256'])
        user=db.get(User,int(payload['sub']))
    except Exception: raise HTTPException(401,'Недействительный токен')
    if not user: raise HTTPException(401,'Пользователь не найден')
    return user

def must_role(user,roles):
    if user.role not in roles: forbidden()

def token(user):
    from datetime import timedelta
    return jwt.encode({'sub':str(user.id),'exp':datetime.now(timezone.utc)+timedelta(days=7)},SECRET,algorithm='HS256')

@app.on_event('startup')
def startup():
    if len(SECRET)<32: raise RuntimeError('Установите SECRET_KEY длиной не менее 32 символов')
    Base.metadata.create_all(engine)
    email=os.getenv('ADMIN_EMAIL'); password=os.getenv('ADMIN_PASSWORD')
    if email and password and len(password)>=12:
        from .core import Session
        with Session() as db:
            if not db.scalar(select(User).where(User.email==email.lower())):
                db.add(User(email=email.lower(),password_hash=pwd.hash(password),display_name='Администратор',role='admin'));db.commit()

@app.get('/')
def index(): return FileResponse('static/index.html')
@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/v1/language-pairs')
def pairs(): return sorted(LANGS)
@app.post('/v1/auth/register',status_code=201)
def register(data:Register,db:DBSession=Depends(session)):
    if data.lang_pair not in LANGS: raise HTTPException(422,'Неподдерживаемая языковая пара')
    if db.scalar(select(User).where(User.email==data.email.lower())): raise HTTPException(409,'Email уже зарегистрирован')
    if data.school_id and not db.get(School,data.school_id): raise HTTPException(422,'Школа не найдена')
    if data.class_id:
        raise HTTPException(403,'Назначение класса выполняется учителем')
        room=db.get(ClassRoom,data.class_id)
        if not room or room.school_id!=data.school_id: raise HTTPException(422,'Класс не соответствует школе')
    u=User(email=data.email.lower(),password_hash=pwd.hash(data.password),display_name=data.display_name,school_id=data.school_id,class_id=data.class_id,lang_pair=data.lang_pair,role='student')
    db.add(u);db.commit();return {'access_token':token(u),'token_type':'bearer'}
@app.post('/v1/auth/login')
def login(data:Credentials,db:DBSession=Depends(session)):
    u=db.scalar(select(User).where(User.email==data.email.lower()))
    if not u or not u.password_hash or not pwd.verify(data.password,u.password_hash): raise HTTPException(401,'Неверный логин или пароль')
    return {'access_token':token(u),'token_type':'bearer'}
@app.get('/v1/me')
def me(u:User=Depends(user_auth),db:DBSession=Depends(session)):
    n=db.scalar(select(func.count(Answer.id)).where(Answer.user_id==u.id))
    approved_texts=db.scalars(select(Fragment.text_ba).join(Answer,Answer.fragment_id==Fragment.id).where(Answer.user_id==u.id,Answer.validation_status=='approved')).all()
    vocab=len(set(word for text in approved_texts for word in normalize(text).split()))
    badges=db.scalars(select(Badge.code).where(Badge.user_id==u.id)).all()
    dates={a.created_at.date() for a in db.scalars(select(Answer).where(Answer.user_id==u.id)).all() if a.created_at}
    today=datetime.now(timezone.utc).date()
    anchor=today if today in dates else today-timedelta(days=1)
    streak=0
    while anchor in dates:
        streak+=1
        anchor-=timedelta(days=1)
    approved=db.scalar(select(func.count(Answer.id)).where(Answer.user_id==u.id,Answer.validation_status=='approved')) or 0
    pending=db.scalar(select(func.count(Answer.id)).where(Answer.user_id==u.id,Answer.validation_status=='pending')) or 0
    return {'id':u.id,'name':u.display_name,'role':u.role,'lang_pair':u.lang_pair,'school_id':u.school_id,'class_id':u.class_id,'points':u.points,'level':1+u.points//100,'answers':n,'vocabulary':vocab,'badges':badges,'streak':streak,'approved_answers':approved,'pending_answers':pending,'next_level_points':100-(u.points%100)}
@app.patch('/v1/me/language-pair')
def change_pair(data:PairIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    if data.lang_pair not in LANGS: raise HTTPException(422,'Неподдерживаемая пара')
    u.lang_pair=data.lang_pair;db.commit();return {'lang_pair':u.lang_pair}
@app.get('/v1/daily')
def daily(u:User=Depends(user_auth),db:DBSession=Depends(session)):
    a=get_or_create_assignment(db,u)
    if a is None: return {'assignment':None,'message':'Нет доступных фрагментов для этой языковой пары'}
    db.commit();f=db.get(Fragment,a.fragment_id);s=db.get(Source,f.source_id)
    existing=db.scalar(select(Answer).where(Answer.assignment_id==a.id))
    answered=bool(existing)
    return {'assignment':{'id':a.id,'fragment_id':f.id,'text_ba':f.text_ba,'lang_pair':a.lang_pair,'translation':a.shown_translation,'source':{'title':s.title,'url':s.external_url,'coordinates':f.coordinates},'audio_url':f.audio_url,'answered':answered,'answer_status':existing.validation_status if existing else None,'answer_action':existing.action if existing else None,'answer_translation':existing.translation if existing else None,'awarded':existing.points_awarded if existing else 0}}
@app.post('/v1/answers')
def answer(data:AnswerIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    try:return submit(db,u,data.assignment_id,data.action,data.translation)
    except ValueError as e: raise HTTPException(409,str(e))
@app.get('/v1/me/history')
def answer_history(limit:int=Query(20,ge=1,le=100),u:User=Depends(user_auth),db:DBSession=Depends(session)):
    rows=db.scalars(select(Answer).where(Answer.user_id==u.id).order_by(Answer.created_at.desc()).limit(limit)).all()
    result=[]
    for a in rows:
        f=db.get(Fragment,a.fragment_id)
        result.append({'id':a.id,'text_ba':f.text_ba if f else '', 'translation':a.translation,'action':a.action,'status':a.validation_status,'points':a.points_awarded,'date':a.created_at.isoformat() if a.created_at else ''})
    return result

@app.get('/v1/practice/review')
def practice_review(limit:int=Query(12,ge=1,le=30),u:User=Depends(user_auth),db:DBSession=Depends(session)):
    # Review is a zero-point independent learning activity; never creates assignments or votes.
    rows=db.scalars(select(Answer).where(Answer.user_id==u.id).order_by(Answer.created_at.desc()).limit(200)).all()
    result=[]; seen=set()
    for a in rows:
        key=(a.fragment_id,a.lang_pair)
        if key in seen: continue
        seen.add(key)
        f=db.get(Fragment,a.fragment_id)
        if not f: continue
        refs=db.scalars(select(Suggestion.translation).where(Suggestion.fragment_id==a.fragment_id,Suggestion.lang_pair==a.lang_pair,Suggestion.is_reference==True)).all()
        result.append({'fragment_id':a.fragment_id,'lang_pair':a.lang_pair,'text_ba':f.text_ba,'previous_answer':a.translation,'reference':refs[0] if refs else None,'answer_status':a.validation_status})
        if len(result)>=limit:break
    return result

@app.get('/v1/dictionary')
def dictionary(
    q:str=Query('',max_length=80),
    include_phrases:bool=False,
    limit:int=Query(24,ge=1,le=80),
    offset:int=Query(0,ge=0,le=100000),
    u:User=Depends(user_auth),
    db:DBSession=Depends(session),
):
    """Lexicon for the signed-in user's selected pair; verified first, otherwise corpus references.

    Pending or rejected user submissions and machine-generated proposals are never exposed
    as established dictionary translations. The default only displays single-word entries.
    """
    reference=Suggestion
    checked=Translation
    # The reference join can contain several source variants; aggregate to one card per fragment.
    stmt=(select(
        Fragment.id.label('fragment_id'),
        Fragment.text_ba.label('text_ba'),
        func.coalesce(checked.translation,func.min(reference.translation)).label('translation'),
        Source.title.label('source'),
        checked.id.label('verified_id'),
    ).join(Source,Source.id==Fragment.source_id)
     .outerjoin(reference,(reference.fragment_id==Fragment.id)&(reference.lang_pair==u.lang_pair)&(reference.is_reference.is_(True)))
     .outerjoin(checked,(checked.fragment_id==Fragment.id)&(checked.lang_pair==u.lang_pair)&(checked.status=='confirmed'))
     .where(or_(reference.id.is_not(None),checked.id.is_not(None))))
    if not include_phrases:
        # The input corpus is phrase-oriented. Limit the default view to lexical entries.
        for whitespace in (' ','\t','\n','\r'):
            stmt=stmt.where(~Fragment.text_ba.contains(whitespace))
    query=q.strip()
    if query:
        term='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        stmt=stmt.where(or_(Fragment.text_ba.ilike(term,escape='\\'),
                             func.coalesce(checked.translation,reference.translation).ilike(term,escape='\\')))
    stmt=stmt.group_by(Fragment.id,Fragment.text_ba,Source.title,checked.id,checked.translation)
    # One additional entry lets the client display an honest "Load more" action.
    rows=db.execute(stmt.order_by(func.lower(Fragment.text_ba),Fragment.id)
                    .offset(offset).limit(limit+1)).all()
    return {'lang_pair':u.lang_pair,'query':query,'include_phrases':include_phrases,
            'offset':offset,'limit':limit,'has_more':len(rows)>limit,
            'items':[{'fragment_id':r.fragment_id,'text_ba':r.text_ba,
                      'translation':r.translation,'source':r.source,
                      'quality':'confirmed' if r.verified_id is not None else 'corpus'}
                     for r in rows[:limit]]}

@app.get('/v1/dictionary/{fragment_id}/audio')
async def dictionary_bashkir_audio(fragment_id:int,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    """Authenticated audio for a dictionary term, not arbitrary user-supplied TTS text."""
    fragment=db.get(Fragment,fragment_id)
    if not fragment:
        raise HTTPException(404,'Слово не найдено')
    exists=db.scalar(select(Suggestion.id).where(Suggestion.fragment_id==fragment_id,
        Suggestion.lang_pair==u.lang_pair,Suggestion.is_reference.is_(True)).limit(1))
    verified=db.scalar(select(Translation.id).where(Translation.fragment_id==fragment_id,
        Translation.lang_pair==u.lang_pair,Translation.status=='confirmed').limit(1))
    if not exists and not verified:
        raise HTTPException(404,'Слово отсутствует в выбранном словаре')
    text=fragment.text_ba.strip()
    if not text or len(text)>180:
        raise HTTPException(422,'Озвучивание поддерживается для слов и коротких фраз (до 180 символов)')
    import httpx
    service=os.getenv('BASHKIR_TTS_URL','http://tts:8010').rstrip('/')
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0,connect=5.0)) as client:
            result=await client.post(service+'/synthesize',json={'text':text})
            result.raise_for_status()
    except (httpx.HTTPError,ValueError) as exc:
        logging.warning('Bashkir TTS unavailable: %s',type(exc).__name__)
        raise HTTPException(503,'Башкирская озвучка пока недоступна. Запустите Docker-сервис voice и попробуйте снова.')
    if result.headers.get('content-type','').split(';')[0]!='audio/wav':
        raise HTTPException(502,'Неверный формат аудио от сервиса синтеза')
    if len(result.content)>12_000_000:
        raise HTTPException(502,'Слишком большой аудиофайл')
    return Response(content=result.content,media_type='audio/wav',headers={'Cache-Control':'private, max-age=86400'})

@app.get('/v1/fragments')
def fragments(limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db:DBSession=Depends(session)):
    rows=db.scalars(select(Fragment).order_by(Fragment.id).offset(offset).limit(limit)).all()
    return [{'id':f.id,'text_ba':f.text_ba,'source_id':f.source_id,'coordinates':f.coordinates,'audio_url':f.audio_url} for f in rows]
@app.get('/v1/translations')
def translations(lang_pair:str|None=None,status:str|None=None,limit:int=Query(100,ge=1,le=1000),offset:int=Query(0,ge=0),db:DBSession=Depends(session)):
    stmt=select(Translation)
    if lang_pair: stmt=stmt.where(Translation.lang_pair==lang_pair)
    if status: stmt=stmt.where(Translation.status==status)
    return [export_row(db,t) for t in db.scalars(stmt.order_by(Translation.id).offset(offset).limit(limit)).all()]
@app.get('/v1/translations/export.csv')
def export_csv(u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin','moderator'})
    buf=io.StringIO();w=csv.writer(buf);w.writerow(['fragment_id','text_ba','lang_pair','translation','source','confirmed','status'])
    for t in db.scalars(select(Translation).order_by(Translation.id)):
        row=export_row(db,t)
        import json
        w.writerow([row['fragment_id'],row['text_ba'],row['lang_pair'],row['translation'],json.dumps(row['source'],ensure_ascii=False),row['confirmed'],row['status']])
    return Response(content='\ufeff'+buf.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="translations.csv"'})
@app.get('/v1/leaderboard')
def leaderboard(period:Literal['day','week','month','year','all']='all',scope:Literal['users','classes','schools']='users',db:DBSession=Depends(session)):
    from datetime import timedelta
    from .core import utcnow
    now=utcnow();delta={'day':1,'week':7,'month':30,'year':365,'all':None}[period]
    stmt=select(Answer.user_id,Answer.action,Answer.points_awarded)
    if delta: stmt=stmt.where(Answer.created_at>=now-timedelta(days=delta))
    rows=db.execute(stmt).all();points={};activity={};corrections={}
    for uid,act,awarded in rows:
        user=db.get(User,uid)
        if not user:continue
        key=(uid if scope=='users' else user.class_id if scope=='classes' else user.school_id)
        if key is None:continue
        points[key]=points.get(key,0)+awarded
        activity[key]=activity.get(key,0)+1
        corrections[key]=corrections.get(key,0)+int(act=='correct')
    q=select(PointAdjustment)
    if delta:q=q.where(PointAdjustment.created_at>=now-timedelta(days=delta))
    for change in db.scalars(q).all():
        person=db.get(User,change.user_id)
        if not person:continue
        key=change.user_id if scope=='users' else person.class_id if scope=='classes' else person.school_id
        if key is None:continue
        points[key]=points.get(key,0)+change.delta
        activity.setdefault(key,0);corrections.setdefault(key,0)
    if period=='all' and scope=='users':
        # User.points is the authoritative account balance, including manual adjustments.
        for person in db.scalars(select(User)).all():
            if person.id in points or person.points:
                points[person.id]=person.points
                activity.setdefault(person.id,0);corrections.setdefault(person.id,0)
    data=[]
    for key,score in sorted(points.items(),key=lambda x:(-x[1],x[0]))[:50]:
        obj=db.get(User,key) if scope=='users' else db.get(ClassRoom,key) if scope=='classes' else db.get(School,key)
        data.append({'id':key,'name':obj.display_name if scope=='users' else obj.name,'channel':('Telegram' if obj.telegram_id else 'Сайт') if scope=='users' else None,'points':score,'answers':activity[key],'corrections':corrections[key]})
    return data
@app.get('/v1/schools')
def schools(db:DBSession=Depends(session)):return [{'id':x.id,'name':x.name} for x in db.scalars(select(School)).all()]
@app.get('/v1/classes')
def classes(school_id:int|None=None,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'teacher','admin'})
    q=select(ClassRoom)
    if u.role=='teacher':q=q.where(ClassRoom.teacher_id==u.id)
    if school_id:q=q.where(ClassRoom.school_id==school_id)
    return [{'id':x.id,'name':x.name,'school_id':x.school_id} for x in db.scalars(q).all()]
@app.post('/v1/schools',status_code=201)
def create_school(data:SchoolIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin'});x=School(name=data.name);db.add(x);db.commit();return {'id':x.id}
# Existing read-only /v1/classes remains public for legacy registration lists.
# All private class data is always filtered by teacher_id on the server.
def owned_room(db,u,class_id):
    room=db.get(ClassRoom,class_id)
    if not room: raise HTTPException(404,'Класс не найден')
    if u.role!='admin' and (u.role!='teacher' or room.teacher_id!=u.id):
        raise HTTPException(404,'Класс не найден')
    return room

def class_students(db,class_id):
    return db.scalars(select(User).join(ClassMembership,ClassMembership.user_id==User.id)
                      .where(ClassMembership.classroom_id==class_id).order_by(User.display_name,User.id)).all()

@app.post('/v1/classes',status_code=201)
def create_class(data:ClassIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin','teacher'})
    if u.role=='teacher' and (not u.school_id or u.school_id!=data.school_id): forbidden()
    if not db.get(School,data.school_id): raise HTTPException(404,'Школа не найдена')
    x=ClassRoom(name=data.name.strip(),school_id=data.school_id,teacher_id=u.id if u.role=='teacher' else None)
    if not x.name:raise HTTPException(422,'Введите название класса')
    if db.scalar(select(ClassRoom.id).where(ClassRoom.school_id==x.school_id,ClassRoom.name==x.name)):
        raise HTTPException(409,'Класс с таким названием уже существует')
    db.add(x);db.commit();return {'id':x.id}

@app.post('/v1/teacher/classes',status_code=201)
def teacher_create_class(data:TeacherClassIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'teacher','admin'})
    if not u.school_id:raise HTTPException(422,'Сначала администратор должен указать вашу школу')
    return create_class(ClassIn(name=data.name,school_id=u.school_id),u,db)

@app.post('/v1/admin/classes/{class_id}/teacher')
def admin_assign_class_owner(class_id:int,data:TeacherAssignIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin'})
    room=db.get(ClassRoom,class_id)
    target=db.get(User,data.teacher_id)
    if not room or not target or target.role!='teacher' or target.school_id!=room.school_id:
        raise HTTPException(404,'Класс или учитель той же школы не найден')
    room.teacher_id=target.id;db.commit()
    return {'class_id':room.id,'teacher_id':target.id}

@app.post('/v1/teacher/students',status_code=201)
def teacher_create_student(data:StudentIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    # Legacy V7 endpoint, now strictly restricted to one owned classroom.
    must_role(u,{'teacher','admin'})
    if not data.class_id:raise HTTPException(422,'Укажите свой класс')
    room=owned_room(db,u,data.class_id)
    if data.school_id!=room.school_id:raise HTTPException(422,'Школа ученика не совпадает со школой класса')
    if data.lang_pair not in LANGS:raise HTTPException(422,'Неподдерживаемая языковая пара')
    email=data.email.strip().lower()
    if db.scalar(select(User.id).where(User.email==email)):raise HTTPException(409,'Email уже зарегистрирован')
    student=User(email=email,password_hash=pwd.hash(data.password),display_name=data.display_name.strip(),
                 school_id=room.school_id,class_id=room.id,lang_pair=data.lang_pair,role='student')
    db.add(student);db.flush()
    db.add(ClassMembership(classroom_id=room.id,user_id=student.id));db.commit()
    return {'id':student.id,'class_id':room.id}

@app.get('/v1/teacher/classes')
def teacher_classes(u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'teacher','admin'})
    q=select(ClassRoom)
    if u.role=='teacher':q=q.where(ClassRoom.teacher_id==u.id)
    rows=db.scalars(q.order_by(ClassRoom.id)).all()
    return [{'id':r.id,'name':r.name,'school_id':r.school_id,'teacher_id':r.teacher_id,
             'students':db.scalar(select(func.count(ClassMembership.id)).where(ClassMembership.classroom_id==r.id)) or 0} for r in rows]

@app.get('/v1/teacher/classes/{class_id}/students')
def teacher_list_students(class_id:int,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'teacher','admin'});owned_room(db,u,class_id)
    return [{'id':s.id,'name':s.display_name,'points':s.points,'answers':db.scalar(select(func.count(Answer.id)).where(Answer.user_id==s.id)) or 0,
             'approved':db.scalar(select(func.count(Answer.id)).where(Answer.user_id==s.id,Answer.validation_status=='approved')) or 0}
            for s in class_students(db,class_id)]

@app.post('/v1/teacher/classes/{class_id}/students',status_code=201)
def teacher_add_member(class_id:int,data:MemberIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'teacher','admin'});room=owned_room(db,u,class_id)
    if data.student_id==u.id:raise HTTPException(422,'Нельзя добавить себя')
    # Do not reveal data about other teachers' students or students of other schools.
    student=db.get(User,data.student_id)
    if not student or student.role!='student' or student.school_id!=room.school_id:
        raise HTTPException(404,'Ученик не найден в этой школе')
    if db.scalar(select(ClassMembership.id).where(ClassMembership.user_id==student.id)):
        raise HTTPException(409,'Ученик уже состоит в классе')
    db.add(ClassMembership(classroom_id=class_id,user_id=student.id));student.class_id=class_id
    db.commit();return {'student_id':student.id,'class_id':class_id}

@app.delete('/v1/teacher/classes/{class_id}/students/{student_id}',status_code=204)
def teacher_remove_member(class_id:int,student_id:int,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'teacher','admin'});owned_room(db,u,class_id)
    member=db.scalar(select(ClassMembership).where(ClassMembership.classroom_id==class_id,ClassMembership.user_id==student_id))
    if not member:raise HTTPException(404,'Ученик не состоит в вашем классе')
    student=db.get(User,student_id)
    if student and student.class_id==class_id:student.class_id=None
    db.delete(member);db.commit();return Response(status_code=204)

@app.delete('/v1/teacher/classes/{class_id}',status_code=204)
def teacher_delete_class(class_id:int,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'teacher','admin'});room=owned_room(db,u,class_id)
    if db.scalar(select(ClassMembership.id).where(ClassMembership.classroom_id==class_id)):
        raise HTTPException(409,'Сначала удалите учеников из класса')
    if db.scalar(select(User.id).where(User.class_id==class_id)):
        raise HTTPException(409,'У класса остались старые привязки пользователей')
    db.delete(room);db.commit();return Response(status_code=204)

@app.get('/v1/teacher/class-report')
def class_report(class_id:int,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    return teacher_list_students(class_id,u,db)

@app.post('/v1/admin/users/{user_id}/role')
def admin_set_role(user_id:int,data:RoleUpdateIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin'})
    target=db.get(User,user_id)
    if not target:raise HTTPException(404,'Пользователь не найден')
    if target.id==u.id:raise HTTPException(409,'Нельзя менять собственную роль')
    if target.role=='admin':raise HTTPException(403,'Роль администратора защищена')
    if data.role!='student' and db.scalar(select(ClassMembership.id).where(ClassMembership.user_id==target.id)):
        raise HTTPException(409,'Сначала исключите пользователя из класса')
    if target.role=='teacher' and db.scalar(select(ClassRoom.id).where(ClassRoom.teacher_id==target.id)):
        raise HTTPException(409,'Сначала передайте или удалите классы учителя')
    target.role=data.role;db.commit();return {'id':target.id,'role':target.role}

@app.post('/v1/admin/users/{user_id}/school')
def admin_set_school(user_id:int,data:SchoolAssignIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin'})
    target=db.get(User,user_id);school=db.get(School,data.school_id)
    if not target or not school:raise HTTPException(404,'Пользователь или школа не найдены')
    if target.class_id or db.scalar(select(ClassMembership.id).where(ClassMembership.user_id==target.id)) or db.scalar(select(ClassRoom.id).where(ClassRoom.teacher_id==target.id)):
        raise HTTPException(409,'Сначала освободите пользователя от класса')
    target.school_id=school.id;db.commit();return {'id':target.id,'school_id':school.id}

@app.post('/v1/moderator/users/{user_id}/points')
def moderator_adjust_points(user_id:int,data:PointsChangeIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'moderator','admin'})
    if user_id==u.id:raise HTTPException(403,'Нельзя изменять свои баллы')
    key=str(data.idempotency_key)
    db.scalar(select(User).where(User.id==u.id).with_for_update())  # serialize idempotency retries by same moderator
    prior=db.scalar(select(PointAdjustment).where(PointAdjustment.moderator_id==u.id,PointAdjustment.idempotency_key==key))
    if prior:
        if prior.user_id!=user_id or prior.delta!=data.delta or prior.reason!=data.reason.strip():
            raise HTTPException(409,'Ключ запроса уже использован для другой операции')
        return {'id':prior.id,'user_id':prior.user_id,'delta':prior.delta,'balance':prior.balance_after,'replayed':True}
    target=db.scalar(select(User).where(User.id==user_id).with_for_update())
    if not target:raise HTTPException(404,'Пользователь не найден')
    if target.role=='admin':raise HTTPException(403,'Нельзя корректировать баллы администратора')
    new_balance=target.points+data.delta
    if new_balance<0:raise HTTPException(409,'Баланс не может стать отрицательным')
    reason=data.reason.strip()
    if len(reason)<5:raise HTTPException(422,'Укажите содержательную причину')
    target.points=new_balance
    adj=PointAdjustment(moderator_id=u.id,user_id=target.id,delta=data.delta,reason=reason,
                        idempotency_key=key,balance_after=new_balance)
    db.add(adj);db.commit()
    return {'id':adj.id,'user_id':target.id,'delta':adj.delta,'balance':new_balance,'replayed':False}

@app.get('/v1/moderator/adjustments')
def moderator_adjustment_history(user_id:int|None=None,limit:int=Query(30,ge=1,le=100),u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'moderator','admin'})
    q=select(PointAdjustment)
    if user_id is not None:q=q.where(PointAdjustment.user_id==user_id)
    rows=db.scalars(q.order_by(PointAdjustment.created_at.desc(),PointAdjustment.id.desc()).limit(limit)).all()
    return [{'id':a.id,'user_id':a.user_id,'moderator_id':a.moderator_id,'delta':a.delta,
             'reason':a.reason,'balance_after':a.balance_after,'created_at':a.created_at.isoformat()} for a in rows]

@app.get('/v1/moderation')
def moderation(u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'moderator','admin'})
    return [export_row(db,t) for t in db.scalars(select(Translation).where(Translation.status=='expert_review').order_by(Translation.id).limit(500)).all()]
class ReviewIn(BaseModel):
    approved: bool

@app.get('/v1/moderation/answers')
def moderation_answers(u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'moderator','admin'})
    answers=db.scalars(select(Answer).where(Answer.validation_status=='pending').order_by(Answer.id).limit(200)).all()
    result=[]
    for a in answers:
        f=db.get(Fragment,a.fragment_id)
        refs=db.scalars(select(Suggestion.translation).where(
            Suggestion.fragment_id==a.fragment_id, Suggestion.lang_pair==a.lang_pair,
            Suggestion.is_reference.is_(True))).all()
        result.append({'answer_id':a.id,'user_id':a.user_id,'text_ba':f.text_ba,
                       'lang_pair':a.lang_pair,'proposed_translation':a.translation,
                       'reference_translations':refs,'action':a.action,
                       'status':a.validation_status})
    return result

@app.post('/v1/moderation/answers/{answer_id}/decision')
def moderation_decide(answer_id:int,data:ReviewIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'moderator','admin'})
    candidate=db.get(Answer,answer_id)
    if candidate is not None and candidate.user_id==u.id: raise HTTPException(403,'Нельзя проверять собственный ответ')
    try:
        a=moderate_answer(db,answer_id,data.approved)
    except ValueError as exc:
        raise HTTPException(409,str(exc))
    return {'answer_id':a.id,'status':a.validation_status,'awarded':a.points_awarded}

@app.post('/v1/sources',status_code=201)
def source_add(data:SourceIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin'});s=Source(**data.model_dump());db.add(s);db.commit();return {'id':s.id}
@app.post('/v1/fragments',status_code=201)
def fragment_add(data:FragmentIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin'});
    if not db.get(Source,data.source_id):raise HTTPException(404,'Источник не найден')
    f=Fragment(**data.model_dump());db.add(f);db.commit();return {'id':f.id}
@app.post('/v1/suggestions',status_code=201)
def suggestion_add(data:SuggestionIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    must_role(u,{'admin'})
    if data.lang_pair not in LANGS or not db.get(Fragment,data.fragment_id):raise HTTPException(422,'Некорректная пара или фрагмент')
    s=Suggestion(**data.model_dump(),origin='teacher');db.add(s);db.commit();return {'id':s.id}
class AutoTranslateIn(BaseModel):
    fragment_id:int
    lang_pair:str='ba-ru'
@app.post('/v1/suggestions/auto',status_code=201)
async def auto_translate(data:AutoTranslateIn,u:User=Depends(user_auth),db:DBSession=Depends(session)):
    """Creates a machine translation candidate; never marks it as validated."""
    must_role(u,{'admin'})
    if data.lang_pair not in LANGS:raise HTTPException(422,'Неподдерживаемая языковая пара')
    f=db.get(Fragment,data.fragment_id)
    if not f:raise HTTPException(404,'Фрагмент не найден')
    from .translate import translate
    try:value=await translate(f.text_ba,'ba',data.lang_pair.split('-')[1])
    except Exception as e:
        logging.exception('Machine translation unavailable')
        raise HTTPException(502,'Ошибка сервиса машинного перевода; проверьте ключ, каталог и поддержку языков')
    existing=db.scalar(select(Suggestion).where(Suggestion.fragment_id==f.id,Suggestion.lang_pair==data.lang_pair,Suggestion.translation==value))
    if existing:return {'id':existing.id,'translation':value}
    obj=Suggestion(fragment_id=f.id,lang_pair=data.lang_pair,translation=value,is_reference=False,origin='yandex')
    db.add(obj);db.commit();return {'id':obj.id,'translation':value}
