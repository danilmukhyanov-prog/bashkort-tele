"""Optional Yandex Cloud Translate adapter. No model training required."""
import os, httpx
LANG={'ba':'ba','ru':'ru','kk':'kk','alt':'alt','ar':'ar','sah':'sah','zh':'zh'}
async def translate(text,source='ba',target='ru'):
    key=os.getenv('YANDEX_API_KEY');folder=os.getenv('YANDEX_FOLDER_ID')
    if not key or not folder:raise RuntimeError('Set YANDEX_API_KEY and YANDEX_FOLDER_ID')
    endpoint=os.getenv('YANDEX_TRANSLATE_ENDPOINT','https://translate.api.cloud.yandex.net/translate/v2/translate')
    payload={'folderId':folder,'texts':[text],'sourceLanguageCode':source,'targetLanguageCode':target}
    async with httpx.AsyncClient(timeout=30) as client:
        r=await client.post(endpoint,headers={'Authorization':f'Api-Key {key}'},json=payload)
        r.raise_for_status();return r.json()['translations'][0]['text']
