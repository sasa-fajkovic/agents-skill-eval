FROM golang:1.24-bookworm AS backend-build

WORKDIR /src/backend
COPY backend/go.mod backend/go.sum ./
RUN go mod download
COPY backend/ ./

ARG APP_VERSION=dev
ARG APP_COMMIT=dev
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-X main.buildVersion=${APP_VERSION} -X main.buildCommit=${APP_COMMIT}" -o /out/app .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    redis-server \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir anthropic openai PyPDF2 python-docx

WORKDIR /app
COPY --from=backend-build /out/app /app/bin/app
COPY frontend /app/frontend
COPY eval /app/eval

COPY docker/app-entrypoint.sh /app/app-entrypoint.sh
RUN chmod +x /app/app-entrypoint.sh /app/bin/app

EXPOSE 8080

ENTRYPOINT ["/app/app-entrypoint.sh"]
