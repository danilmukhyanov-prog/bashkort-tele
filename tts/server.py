"""Optional Bashkir voice microservice. Model license: CC-BY-NC-4.0."""
import hashlib
import io
import os
from pathlib import Path
from threading import Lock
from wave import open as wave_open

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

app = FastAPI(title="Bashkir dictionary TTS")
cache_path = Path(os.environ.get('TTS_CACHE_DIR','/models/audio'))
lock = Lock()
model = None
tokenizer = None

class Request(BaseModel):
    text: str = Field(min_length=1,max_length=180)

def get_model():
    global model, tokenizer
    if model is None:
        from transformers import AutoTokenizer, VitsModel
        model = VitsModel.from_pretrained('facebook/mms-tts-bak')
        tokenizer = AutoTokenizer.from_pretrained('facebook/mms-tts-bak')
        model.eval()
    return model, tokenizer

@app.get('/health')
def health(): return {'status':'ok','model_loaded':model is not None}

@app.post('/synthesize')
def synthesize(req:Request):
    import torch
    text=req.text.strip()
    if not text or len(text)>180: raise HTTPException(422,'Некорректный текст')
    cache_path.mkdir(parents=True,exist_ok=True)
    target=cache_path/(hashlib.sha256(text.encode('utf-8')).hexdigest()+'.wav')
    if target.is_file(): return Response(content=target.read_bytes(),media_type='audio/wav')
    try:
        with lock:
            if target.is_file():return Response(content=target.read_bytes(),media_type='audio/wav')
            m,t=get_model()
            torch.manual_seed(21)
            inputs=t(text,return_tensors='pt')
            with torch.no_grad(): samples=m(**inputs).waveform.squeeze().detach().cpu().numpy()
            # Convert audio to 16-bit PCM WAV, using the model's actual sampling rate.
            samples=np.clip(samples,-1,1)
            pcm=(samples*32767).astype('<i2')
            buf=io.BytesIO()
            with wave_open(buf,'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(int(m.config.sampling_rate))
                wav.writeframes(pcm.tobytes())
            payload=buf.getvalue()
            temp=target.with_suffix('.tmp')
            temp.write_bytes(payload)
            temp.replace(target)
            return Response(content=payload,media_type='audio/wav')
    except Exception as exc:
        import logging
        logging.exception('TTS generation failed')
        raise HTTPException(503,'Модель синтеза речи пока недоступна') from exc
