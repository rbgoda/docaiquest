# syntax=docker/dockerfile:1.7

# ---------- Stage 1: build a venv with CPU torch + all deps --------------
# Using a venv as the single hand-off artifact between stages. This is
# the cleanest pattern for shrinking multi-stage Python images:
#   · No wheel directory carried over (saved 3.26GB on the prior build)
#   · Builder + runtime share the same Python ABI · venv is portable
#   · CPU torch installed FIRST inside the venv; the requirements.txt
#     install sees torch is already present and skips re-downloading
#     a CUDA-flavored torch (multi-GB regression we saw on a prior
#     attempt using --prefix instead of a venv).
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc \
 && rm -rf /var/lib/apt/lists/*

# Create + activate the venv. Subsequent pip calls install INTO it.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# M43.P1 · CPU torch from the PyTorch CPU index. Pinned to 2.6.x for
# compatibility with sentence-transformers + docling transformers range.
# ~200MB instead of the multi-GB CUDA build.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.6.0

COPY requirements.txt .
RUN pip install -r requirements.txt

# ---------- Stage 2: runtime ---------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# M40 Phase F · Tesseract OCR for deterministic per-field bboxes on image
# documents (passport, driver licence, ACRA bizfile screenshots, etc).
# libgl1 + libglib2.0-0 required by opencv-python (rapidocr>=3.9.0 + camelot-py).
# ghostscript required by camelot-py for PDF rendering.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libgl1 \
    libglib2.0-0 \
    ghostscript \
 && rm -rf /var/lib/apt/lists/*

# OPTIONAL · LibreOffice for legacy MS-Office/ODF/RTF → PDF conversion. OFF by
# default (adds ~700MB-1GB + a conversion attack surface). Build with
# `--build-arg INSTALL_LIBREOFFICE=true` AND run with
# DOCAIQ_OFFICE_CONVERT_ENABLED=true to enable. .pptx works WITHOUT this.
ARG INSTALL_LIBREOFFICE=false
RUN if [ "$INSTALL_LIBREOFFICE" = "true" ]; then \
      apt-get update \
   && apt-get install -y --no-install-recommends libreoffice-impress libreoffice-writer libreoffice-calc \
   && rm -rf /var/lib/apt/lists/* ; \
    fi

# Non-root user. Create first so chown can apply at COPY time, avoiding
# the disastrous `chown -R /app` pattern that copy-on-writes every file
# in the layer (was inflating the image by 2.3GB).
RUN groupadd --system --gid 1001 docaiq \
 && useradd --system --uid 1001 --gid docaiq --create-home docaiq

WORKDIR /app

# Single venv copy · NOT wheels. One layer, the actually-needed bytes.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# ═══════════════════════════════════════════════════════════════════════════
# ML model pre-downloads — BEFORE app code so this layer caches independently.
# Only re-downloads when the venv changes (requirements.txt). Code-only deploys
# skip this entirely → ~2-3 min instead of ~15 min.
# ═══════════════════════════════════════════════════════════════════════════
ENV HF_HOME=/home/docaiq/.cache/huggingface \
    HF_HUB_DOWNLOAD_TIMEOUT=30
USER docaiq

# Reranker: ms-marco-MiniLM-L-6-v2 (~90MB, CPU-fast). Default reranker.
RUN timeout 300 python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', max_length=512, device='cpu')" \
 || echo "Reranker pre-download skipped — model will download on first use"

# Embedder: all-MiniLM-L6-v2 (~90MB). Default local embedding model.
RUN timeout 480 python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2', device='cpu')" \
 || echo "MiniLM embedder pre-download skipped — model will download on first use"

# Embedder v2: BGE-M3 (~2.2GB, 1024d, multilingual). embed_v2 backend.
RUN timeout 900 python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cpu')" \
 || echo "BGE-M3 pre-download skipped — model will download on first use"

# ═══════════════════════════════════════════════════════════════════════════
# App code — BELOW model downloads so code changes don't re-download models.
# ═══════════════════════════════════════════════════════════════════════════
USER root
COPY --chown=docaiq:docaiq app          ./app
COPY --chown=docaiq:docaiq migrations   ./migrations
COPY --chown=docaiq:docaiq alembic.ini  ./
COPY --chown=docaiq:docaiq entrypoint.sh ./
RUN chmod +x ./entrypoint.sh
USER docaiq

EXPOSE 8001

# Per-customer overrides come in via env vars (DOCAIQ_TENANT_ID, DOCAIQ_DATABASE_URL, ...)
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/api/health').status==200 else 1)"

CMD ["./entrypoint.sh"]
