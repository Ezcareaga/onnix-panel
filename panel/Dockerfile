FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# System deps for asyncpg and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Layer order below is deliberate: most stable first, most edited last, so that
# a change to app/ or tests/ does not re-copy the 100 MB Tailwind binary.

# Tailwind standalone CLI v3.4.17, vendored — see Phase 116.
# Binary integrity verified pre-commit via panel/vendor/tailwindcss.sha256.
# NOTE: build context is ./panel/, so COPY uses vendor/tailwindcss (NOT panel/vendor/...)
COPY vendor/tailwindcss /usr/local/bin/tailwindcss
RUN chmod +x /usr/local/bin/tailwindcss

# Tailwind config (before app code so it's available for CSS build)
COPY tailwind.config.js .

# Vendored scripts for in-container test parity (STAB-06 / TD-116-04).
# tests/test_twilio_templates_m3_script.py resolves the script to container-root
# /scripts/ (Path(__file__).parent.parent.parent / "scripts"), so COPY to /scripts
# (NOT /app/scripts). Canonical copy is the repo-root scripts/; this is the vendored
# build-context copy under panel/scripts/. World-readable so appuser can collect it.
COPY scripts/ /scripts/

# Alembic config + migrations (applied at startup by entrypoint)
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Startup entrypoint (runs alembic upgrade head then execs uvicorn)
COPY entrypoint.sh ./
RUN chmod +x ./entrypoint.sh

# Application code
COPY app/ ./app/

# Build Tailwind CSS — scans app/templates, so it must follow COPY app/
RUN /usr/local/bin/tailwindcss -c ./tailwind.config.js \
    -i ./app/static/css/input.css -o ./app/static/css/tailwind.css --minify

# Test suite (M6.2 CLEAN-09 close — enables in-container pytest).
# Last: the most-edited tree invalidates nothing above it.
COPY tests/ ./tests/

# Run as non-root for CIS Docker Benchmark compliance.
# Host log dirs must allow writes from uid 1001:
#   chmod 777 /home/onnix/logs/prod /home/onnix/logs/dev
RUN adduser --disabled-password --gecos "" --uid 1001 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Identidad de la imagen. Va acá abajo a propósito: invalida solo esta capa.
# Horneado en la imagen y no inyectado al arrancar, para que un rollback a una
# tag vieja reporte SU revisión y no la del deploy que se está revirtiendo.
ARG GIT_SHA=""
ENV APP_REVISION=${GIT_SHA}
LABEL org.opencontainers.image.revision=${GIT_SHA}

CMD ["./entrypoint.sh"]
