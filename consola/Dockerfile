# Consola de iTier — imagen de la web del plano de control.
# Sin compilación nativa: argon2-cffi/segno/httpx/uvicorn traen wheels, así que
# una imagen slim alcanza. Corre como usuario no-root; el estado vive en /data.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ITIER_CONSOLE_DB=/data/consola.sqlite3 \
    ITIER_CONSOLE_PORT=8080

WORKDIR /app

COPY pyproject.toml /app/
COPY itier_console /app/itier_console

RUN pip install . \
    && useradd -r -u 10001 -d /app -s /usr/sbin/nologin itier \
    && mkdir -p /data \
    && chown -R itier:itier /data /app

USER itier
EXPOSE 8080

# El /healthz no requiere sesión; sirve de liveness/readiness del contenedor.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["itier-console"]
