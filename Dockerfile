FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY pyproject.toml README.md LICENSE CHANGELOG.md /app/
COPY src/ /app/src/

RUN python -m pip install --no-cache-dir uv==0.10.7 \
  && uv pip install --no-cache-dir ".[redis]"


FROM python:3.12-slim@sha256:401f6e1a67dad31a1bd78e9ad22d0ee0a3b52154e6bd30e90be696bb6a3d7461 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# System deps (WeasyPrint runtime + basic fonts/mime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates=20250419 \
    curl=8.14.1-2+deb13u3 \
    fonts-dejavu-core=2.37-8 \
    libcairo2=1.18.4-1+b1 \
    libgdk-pixbuf-2.0-0=2.42.12+dfsg-4+deb13u1 \
    libpango-1.0-0=1.56.3-1 \
    libpangoft2-1.0-0=1.56.3-1 \
    shared-mime-info=2.4-5+b2 \
  && rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 10001 app \
  && adduser --system --uid 10001 --ingroup app --home /nonexistent --shell /usr/sbin/nologin app

COPY --from=builder --chown=app:app /opt/venv /opt/venv

COPY --chown=app:app config/ /app/config/

USER app:app

EXPOSE 8080

CMD ["zammad-pdf-archiver"]
