FROM golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0 AS backend-build

WORKDIR /src/backend
COPY backend/go.mod backend/go.sum ./
RUN go mod download
COPY backend/ ./

ARG APP_VERSION=dev
ARG APP_COMMIT=dev
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-X main.buildVersion=${APP_VERSION} -X main.buildCommit=${APP_COMMIT}" -o /out/app .

FROM python:3.12.16-slim-trixie@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS runtime

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
