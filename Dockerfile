FROM python:3.11-slim

ARG APT_MIRROR_HOST=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    HIREMATE_DB_PATH=/app/data/hiremate.db \
    HIREMATE_LEGACY_DATA_DIR=/app/bootstrap_data \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}

WORKDIR /app

RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR_HOST}/debian|g; s|http://security.debian.org/debian-security|https://${APT_MIRROR_HOST}/debian-security|g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR_HOST}/debian|g; s|http://security.debian.org/debian-security|https://${APT_MIRROR_HOST}/debian-security|g" /etc/apt/sources.list; \
    fi; \
    printf 'Acquire::Retries \"5\";\nAcquire::https::Timeout \"30\";\nAcquire::http::Timeout \"30\";\n' > /etc/apt/apt.conf.d/99hiremate-retries; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-chi-sim \
        poppler-utils; \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && python -c "import pymysql, cryptography; print('mysql deps ok')" \
    && python - <<'PY'
from pathlib import Path
import streamlit

streamlit_root = Path(streamlit.__file__).resolve().parent
static_root = streamlit_root / "static"
print(f"streamlit version: {streamlit.__version__}")
print(f"streamlit static root: {static_root}")
if not static_root.exists():
    raise SystemExit("streamlit static assets directory is missing")
if not any(static_root.rglob('*.js')):
    raise SystemExit("streamlit static assets do not include js files")
print("streamlit static assets ok")
PY

COPY . .
RUN mkdir -p /app/bootstrap_data /app/data \
    && if [ -d /app/data ]; then cp -r /app/data/. /app/bootstrap_data/ 2>/dev/null || true; fi

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
    CMD python -c "import sys, urllib.request; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).getcode() == 200 else sys.exit(1)"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
