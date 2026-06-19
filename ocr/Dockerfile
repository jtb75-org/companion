# Single-stage build. kaniko + multi-stage Dockerfiles hit a registry
# push-auth bug (401 on manifest push) with our zot/atlas-builder setup — the
# same reason the backend and web images are single-stage. Build deps are
# installed, used for pip + the model warmup, then purged in the same layer so
# the runtime image stays as lean as a PaddleOCR image gets.
# Python 3.10 (not 3.12): PaddleOCR's dependency tree (opencv, shapely,
# scikit-image, lanms, pyclipper, pdf2docx, ...) has prebuilt wheels for 3.10,
# so nothing compiles in kaniko (3.12 forced source builds that segfaulted),
# and stdlib distutils still exists.
# slim-bookworm (Debian 12 / glibc 2.36): paddlepaddle 2.6.2's wheel
# segfaults on bare `slim` (now Debian 13 / glibc 2.41 — too new).
FROM python:3.10-slim-bookworm

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
# curl stays for the container HEALTHCHECK. We do NOT install build-essential:
# every dep installs from a prebuilt wheel (no compilation), and on this base
# `build-essential` drags in gcc-14/libstdc++ that needs GLIBC_2.38 (> the
# base's 2.36) and breaks apt.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 \
      libglib2.0-0 \
      libgomp1 \
      libsm6 \
      libxext6 \
      libxrender1 \
      curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install in SEPARATE layers so no single image blob is ~2GB: a 2GB layer
# times out (502) pushing through the zot ingress. Splitting paddle, the CV
# stack, and paddleocr into their own ~600-800MB blobs keeps each push under
# the timeout. requirements.txt (pinned) drives versions; the already-installed
# heavy deps are skipped by the final `-r` install.
RUN pip install --no-cache-dir --upgrade pip setuptools==69.5.1 wheel==0.43.0
RUN pip install --no-cache-dir paddlepaddle==2.6.2 numpy==1.26.4
RUN pip install --no-cache-dir \
      opencv-python==4.6.0.66 opencv-contrib-python==4.6.0.66 \
      opencv-python-headless==4.11.0.86 scikit-image==0.25.2 scipy==1.15.3
RUN pip install --no-cache-dir -r requirements.txt

# NOTE: models are loaded LAZILY on first /ocr request (on a real node), not
# baked in at build — instantiating PaddleOCR (model load + a warmup inference)
# segfaults inside kaniko. First request is slower; /health stays cheap so the
# pod still goes Ready promptly. (warmup.py retained for optional local use.)
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
