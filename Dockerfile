FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

# HF Spaces는 7860, Render는 PORT 환경변수를 동적으로 주입한다.
# shell 형식 CMD 로 $PORT 를 런타임에 확장해 두 곳 모두에서 동작하게 한다.
ENV PORT=7860
EXPOSE 7860

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}
