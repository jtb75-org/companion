# Single-stage build. kaniko + multi-stage Dockerfiles hit a registry
# push-auth bug (401 on manifest push) with our zot/atlas-builder setup — the
# same reason the backend and web images are single-stage. Build deps are
# installed, used for pip + the model warmup, then purged in the same layer so
# the runtime image stays as lean as a PaddleOCR image gets.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt ./

# System libs PaddleOCR / OpenCV / PyMuPDF need at runtime:
#   libgl1, libglib2.0-0   -> OpenCV (cv2) shared libs
#   libgomp1               -> OpenMP runtime paddle/numpy use
#   libglib2.0-0           -> gobject deps pulled by opencv
# curl stays for the container HEALTHCHECK. build-essential is only needed to
# compile any sdist wheels and is purged in this same layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libgl1 \
      libglib2.0-0 \
      libgomp1 \
      libsm6 \
      libxext6 \
      libxrender1 \
      curl ca-certificates \
 && pip install --upgrade pip setuptools wheel \
 && pip install -r requirements.txt \
 && apt-get purge -y --auto-remove build-essential \
 && rm -rf /var/lib/apt/lists/*

# Pre-download the PaddleOCR models into the image so the container ships with
# them — no cold first-request download. Models land under /root/.paddleocr.
COPY warmup.py ./warmup.py
RUN python warmup.py

COPY server.py ./server.py

ARG BUILD_COMMIT=unknown
ENV BUILD_COMMIT=${BUILD_COMMIT}

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Single uvicorn worker: PaddleOCR holds a large model in memory and we
# serialize inference behind a process-level lock, so extra workers would just
# multiply RSS. Scale with replicas, not workers.
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
