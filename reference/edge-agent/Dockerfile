# Imagen del agente de borde de iTier.
#
# Multi-arch (amd64 + arm64): corre en el mini-PC x86 del appliance estándar y
# en la Raspberry Pi 5 del perfil sonda. Ver docs/adr/ADR-0007.
#
# Construida y publicada por .github/workflows/build-agent.yml a GHCR.

# ---- build ----------------------------------------------------------------
FROM python:3.12-slim AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /src
COPY pyproject.toml ./
COPY itier_agent ./itier_agent

# Instala el paquete y sus dependencias en un prefijo aislado que copiamos luego.
RUN pip install --prefix=/install .

# ---- runtime --------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Usuario sin privilegios: el agente nunca corre como root (ADR-0004, hardening).
RUN groupadd --system --gid 10001 itier \
 && useradd --system --uid 10001 --gid itier --home /home/itier --create-home itier

COPY --from=build /install /usr/local

ENV PYTHONUNBUFFERED=1 \
    ITIER_HEALTH_PORT=8080

USER itier
WORKDIR /home/itier

EXPOSE 8080

# Liveness sin depender de curl (no está en slim).
HEALTHCHECK --interval=30s --timeout=4s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status==200 else 1)"

# Servicio stub de Fase 3: valida config, expone health, queda vivo.
CMD ["python", "-m", "itier_agent.serve"]
