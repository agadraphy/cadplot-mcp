ARG CADPLOT_PYTHON_IMAGE

FROM ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 AS cadplot_uv

FROM ${CADPLOT_PYTHON_IMAGE}

ARG CADPLOT_PYTHON_IMAGE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PYTHON_DOWNLOADS=0

# Refuse a tag-only Python base and retain both verified source references in image metadata.
RUN python -c "import os, re, sys; ref = os.environ['CADPLOT_PYTHON_IMAGE']; sys.exit(0 if re.fullmatch(r'.+@sha256:[0-9a-f]{64}', ref) else 'CADPLOT_PYTHON_IMAGE must be an immutable sha256 reference')"
LABEL io.cadplot.build.python-image="${CADPLOT_PYTHON_IMAGE}" \
      io.cadplot.build.uv-image="ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500"

COPY --from=cadplot_uv /uv /uvx /usr/local/bin/

RUN groupadd --system --gid 10001 cadplot \
    && (getent group 1000 >/dev/null || groupadd --system --gid 1000 render-secrets) \
    && useradd --system --uid 10001 --gid cadplot \
        --groups "$(getent group 1000 | cut -d: -f1)" --create-home cadplot

WORKDIR /app
COPY packages/protocol ./packages/protocol
COPY services/gateway/pyproject.toml services/gateway/uv.lock services/gateway/README.md services/gateway/LICENSE ./services/gateway/
COPY services/gateway/cadplot_gateway ./services/gateway/cadplot_gateway
COPY services/gateway/migrations ./services/gateway/migrations
RUN uv sync --frozen --no-dev --no-editable --project services/gateway

USER 10001:10001
ENV PATH="/app/services/gateway/.venv/bin:${PATH}"
EXPOSE 10000
CMD ["cadplot-gateway"]
