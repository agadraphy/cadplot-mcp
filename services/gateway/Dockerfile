ARG CADPLOT_PYTHON_IMAGE
FROM ${CADPLOT_PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system --gid 10001 cadplot \
    && useradd --system --uid 10001 --gid cadplot --create-home cadplot

WORKDIR /app
COPY packages/protocol ./packages/protocol
COPY services/gateway/pyproject.toml services/gateway/uv.lock services/gateway/README.md services/gateway/LICENSE ./services/gateway/
COPY services/gateway/cadplot_gateway ./services/gateway/cadplot_gateway
COPY services/gateway/migrations ./services/gateway/migrations
RUN uv sync --frozen --no-dev --no-editable --project services/gateway

USER 10001:10001
ENV PATH="/app/services/gateway/.venv/bin:${PATH}"
EXPOSE 8000
CMD ["cadplot-gateway"]
