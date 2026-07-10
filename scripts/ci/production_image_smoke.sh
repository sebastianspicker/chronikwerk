#!/usr/bin/env bash
set -euo pipefail

image="zammad-pdf-archiver:verify"
docker build -f Dockerfile -t "$image" .
docker run --rm "$image" python -c \
  'import weasyprint, pyhanko, asn1crypto; print("production-imports-ok")'
docker run --rm "$image" python -c \
  'import asyncio; from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf; from zammad_pdf_archiver.domain.snapshot_models import Snapshot; s=Snapshot.model_validate({"ticket":{"id":1,"number":"VERIFY-1"},"articles":[]}); p=asyncio.run(render_pdf(s)); assert p.startswith(b"%PDF"); print("unsigned-render-ok")'
docker run --rm \
  --mount "type=bind,src=$PWD/scripts/ci/verify_production_signing.py,dst=/app/verify_production_signing.py,readonly" \
  "$image" python /app/verify_production_signing.py
