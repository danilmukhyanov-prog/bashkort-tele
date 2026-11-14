"""Import licensed text datasets from Hugging Face; run inside API container.
Usage: python scripts/import_hf.py --dataset phrasebooks --limit 1000
       python scripts/import_hf.py --dataset books --limit 300
       python scripts/import_hf.py --dataset periodicals --limit 300
"""
import argparse, json, urllib.request, urllib.parse, re, logging
from sqlalchemy import select
from app.core import Session, Base, engine
from app.models import Source, Fragment, Suggestion

DATASETS={'phrasebooks':'bashkorttele/trilingual-parallel-phrasebooks-bgpu',
          'books':'bashkorttele/books-kitap','periodicals':'bashkorttele/periodicals-izddom'}
def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':'BashkirSchoolImporter/1.0'})
    with urllib.request.urlopen(req,timeout=40) as resp: return json.load(resp)
def api(endpoint,**params):
    return get('https://datasets-server.huggingface.co/'+endpoint+'?'+urllib.parse.urlencode(params))
def segment(text,max_len=420):
    if not isinstance(text,str):return []
    text=re.sub(r'\s+',' ',text).strip()
    if not text:return []
    sentences=re.split(r'(?<=[.!?])\s+',text)
    return [s.strip() for s in sentences if 12<=len(s.strip())<=max_len]
def insert(db,src,key,text,coords,translations):
    if not isinstance(text,str) or not text.strip():return False
    frag=db.scalar(select(Fragment).where(Fragment.source_id==src.id,Fragment.source_key==key))
    if not frag:
        frag=Fragment(source_id=src.id,source_key=key,text_ba=text.strip(),coordinates=coords)
        db.add(frag);db.flush()
    for pair,translation in translations.items():
        if not isinstance(translation,str) or not translation.strip():continue
        existing=db.scalar(select(Suggestion).where(Suggestion.fragment_id==frag.id,Suggestion.lang_pair==pair,Suggestion.translation==translation.strip()))
        if not existing:db.add(Suggestion(fragment_id=frag.id,lang_pair=pair,translation=translation.strip(),origin='dataset',is_reference=True))
    return True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dataset',choices=DATASETS,default='phrasebooks');parser.add_argument('--limit',type=int,default=1000)
    args=parser.parse_args();dataset=DATASETS[args.dataset]
    Base.metadata.create_all(engine)
    splits=api('splits',dataset=dataset)['splits']
    print('Available splits:',[(x['config'],x['split']) for x in splits])
    with Session() as db:
        src=db.scalar(select(Source).where(Source.external_url=='https://huggingface.co/datasets/'+dataset))
        if not src:
            src=Source(title=dataset,kind='text',external_url='https://huggingface.co/datasets/'+dataset,license='Check dataset card',metadata_json={'provider':'Hugging Face'})
            db.add(src);db.flush()
        count=0
        for split in splits:
            offset=0
            while count<args.limit:
                batch=api('rows',dataset=dataset,config=split['config'],split=split['split'],offset=offset,length=min(100,args.limit-count))
                rows=batch.get('rows',[])
                if not rows:break
                for row in rows:
                    r=row['row'];rid=str(r.get('id') or row['row_idx'])
                    coords={'dataset':dataset,'config':split['config'],'split':split['split'],'row':row['row_idx'],'record_id':rid}
                    if args.dataset=='phrasebooks':
                        pair={'kk':'ba-kk','alt':'ba-alt','ar':'ba-ar','sah':'ba-sah','zh':'ba-zh','zh-cn':'ba-zh','cn':'ba-zh'}.get(str(r.get('lang_other','')).lower())
                        translated={'ba-ru':r.get('text_ru')}
                        if pair: translated[pair]=r.get('text_other')
                        if insert(db,src,f"{split['config']}:{split['split']}:{rid}",r.get('text_ba'),coords,translated):count+=1
                    else:
                        # Corpora have different schemas; inspect and extract explicitly known text fields.
                        raw=r.get('text_ba') or r.get('text') or r.get('content')
                        if isinstance(raw,str):
                            for i,sentence in enumerate(segment(raw)):
                                if count>=args.limit:break
                                c={**coords,'sentence':i}
                                if insert(db,src,f"{split['config']}:{split['split']}:{rid}:{i}",sentence,c,{}):count+=1
                offset+=len(rows)
                db.commit()
                print(f'{dataset}: {count} imported',flush=True)
                if offset>=split.get('num_rows',10**12):break
            if count>=args.limit:break
    if count == 0:
        raise SystemExit('ERROR: No fragments imported. Inspect the dataset schema/network and retry.')
    print(f'Done: {count} fragments processed. For phrasebooks, daily tasks are now available for ba-ru and supported third languages.')
    print('Text-only fragments without parallel translations need translation before daily tasks.')
if __name__=='__main__':main()
