FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS dependencies
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1
WORKDIR /opt/momentum
COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca_bundle,required=false \
    if [ -s /run/secrets/build_ca_bundle ]; then \
        SSL_CERT_FILE=/run/secrets/build_ca_bundle uv sync --system-certs --frozen --no-dev --no-install-project; \
    else \
        uv sync --system-certs --frozen --no-dev --no-install-project; \
    fi

FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS runtime
ENV PATH="/opt/momentum/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /opt/momentum
RUN groupadd --gid 10001 momentum \
    && useradd --uid 10001 --gid 10001 --create-home momentum \
    && mkdir -p /opt/momentum/data /opt/momentum/artifacts \
    && chown -R 10001:10001 /opt/momentum
COPY --from=dependencies /opt/momentum/.venv /opt/momentum/.venv
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 config ./config
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.environ.get("PORT", "8000") + "/api/health", timeout=3).read()'
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --workers 1 --port \"${PORT:-8000}\""]
