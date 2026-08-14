FROM python:3.11-slim

# 한글 차트 라벨용 폰트 (matplotlib)
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces는 컨테이너를 uid 1000으로 실행한다. root 소유 디렉터리에 쓰면
# 권한 오류가 나므로 동일 uid의 user를 만들고 그 홈에서 앱을 돌린다.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    MPLCONFIGDIR=/home/user/.cache/matplotlib \
    PYTHONUNBUFFERED=1
WORKDIR $HOME/app

COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=user app ./app
COPY --chown=user web ./web

# HF Spaces는 7860, 그 밖의 호스팅은 PORT를 주입하기도 한다.
# shell 형식 CMD로 런타임에 확장해 양쪽 모두에서 동작하게 한다.
ENV PORT=7860
EXPOSE 7860

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}
