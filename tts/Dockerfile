FROM python:3.11-slim
WORKDIR /srv
# CPU-only PyTorch avoids downloading CUDA runtimes for the optional TTS service.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1
RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn==0.34.2 transformers==4.44.2 numpy==1.26.4
COPY server.py /srv/server.py
EXPOSE 8010
CMD ["uvicorn","server:app","--host","0.0.0.0","--port","8010"]
