FROM golang:1.26.10-bookworm@sha256:5de7c99eb78ca5e515a4c340235d0955ec5964a5071c492c15503d7c267da473 AS backend-build

WORKDIR /src/backend
COPY backend/go.mod backend/go.sum ./
RUN go mod download
COPY backend/ ./

ARG APP_VERSION=dev
ARG APP_COMMIT=dev
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-X main.buildVersion=${APP_VERSION} -X main.buildCommit=${APP_COMMIT}" -o /out/app .

FROM python:3.15.0-slim-trixie@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    redis-server \
    && rm -rf /var/lib/apt/lists/*

COPY .claude/skills/skill-evaluation/requirements.txt /tmp/skill-evaluation-requirements.txt
RUN pip install --no-cache-dir -r /tmp/skill-evaluation-requirements.txt

WORKDIR /app
COPY --from=backend-build /out/app /app/bin/app
COPY backend/config.json /app/config.json
COPY frontend /app/frontend
COPY .claude/skills/skill-evaluation /app/skill-evaluation

COPY docker/app-entrypoint.sh /app/app-entrypoint.sh
RUN chmod +x /app/app-entrypoint.sh /app/bin/app

EXPOSE 8080

ENTRYPOINT ["/app/app-entrypoint.sh"]
