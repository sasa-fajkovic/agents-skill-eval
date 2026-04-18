# agents-skill-eval

`agents-skill-eval` evaluates agent skill packages from uploaded files or GitHub URLs.

Live site: `https://agents-skill-eval.com`

## What It Does

- Accepts a `SKILL.md` plus optional supporting files.
- Performs upload-time security screening before a job is queued.
- Runs deterministic evaluation inside a locked-down Docker container.
- Performs the LLM analysis on the host side in Go.
- Returns a combined result with deterministic findings, LLM analysis, summary, progress, and overall score.

## Architecture

The app has three main parts:

1. `frontend/`
   Simple static UI for uploads, GitHub URLs, polling, and result display.

2. `backend/main.go`
   Go HTTP server, Redis-backed queue, worker, upload validation, GitHub fetch logic, container orchestration, secret redaction, and host-side Anthropic call.

3. `eval/run_eval.py`
   Deterministic-only evaluator that runs inside the isolated container and returns structured JSON.

There is also a local skill package under `.claude/skills/skill-evaluation/` used to evaluate `SKILL.md` files against the agentskills.io standard.

## Evaluation Flow

1. User uploads files or submits a GitHub URL.
2. Backend validates file names, MIME types, and content before queueing.
3. Files are written into a temporary input directory.
4. Worker launches a Docker container with a read-only input mount and no network.
5. `eval/run_eval.py` discovers files, reads the primary `SKILL.md`, gathers supporting context, and returns deterministic results as JSON.
6. Go parses that JSON and performs the Anthropic call on the host.
7. Backend combines deterministic and LLM results, stores the final payload in Redis, and exposes it via `/result/{jobId}`.

## Security Model

The main hardening goal is to treat uploaded skills and supporting files as untrusted input.

### Upload and Fetch Defenses

- Blocks dangerous filenames such as `.env`, private keys, credential files, and key material.
- Blocks suspicious text patterns including prompt injection, secret exfiltration, metadata-service access, container escape hints, and embedded credentials.
- Rejects supporting files that try to access secrets, environment variables, local credential files, subprocess execution, or network calls.
- Rejects PDFs with active content markers such as JavaScript, launch actions, or embedded files.
- Restricts GitHub fetches to `github.com` and `raw.githubusercontent.com`.
- Restricts GitHub directory imports to a small allowlist of file types relevant to skills.

### Container Isolation

The evaluator container runs with these restrictions:

- `--network none`
- `--read-only`
- `--tmpfs /tmp:size=50m`
- memory, CPU, and PID limits
- `--security-opt no-new-privileges`
- `--cap-drop ALL`
- non-root user
- read-only bind mount for `/input`

### Secret Handling

- `ANTHROPIC_API_KEY` is not passed into the evaluator container.
- Anthropic API access happens only on the host side in Go.
- Progress lines, stored errors, and stored results are redacted before persistence.

This split is intentional: the untrusted container never receives the API key.

## Why The LLM Call Moved Out Of The Container

Earlier designs that gave the container both network access and the Anthropic key created an unnecessary exfiltration risk.

The current design removes that trust assumption:

- Container: deterministic parsing and extraction only
- Host: Anthropic request, JSON parsing, scoring, summary generation

That keeps the secret boundary smaller and easier to audit.

## API Endpoints

- `GET /health`
  Returns app and Redis health.

- `POST /upload`
  Accepts multipart file uploads and/or a `githubUrl` form field.

- `GET /result/{jobId}`
  Returns pending status with progress, final result, or structured error.

- `GET /robots.txt`
- `GET /sitemap.xml`
- `GET /`

## Frontend Behavior

- The `Run evaluation` button is intentionally large and clear.
- The button is disabled for the full lifetime of an in-flight job.
- While a job is running, the label changes to `Evaluation running...`.
- Result cards are emphasized so the important findings are easy to scan.

## Local Development

### Requirements

- Go
- Docker
- Redis
- Python 3
- `ANTHROPIC_API_KEY` for end-to-end local runs

### Start Locally

```bash
make build
ANTHROPIC_API_KEY=... make start
```

Health check:

```bash
make health
```

Stop the app:

```bash
make stop
```

## Tests

Run all tests:

```bash
make test
```

Current test coverage focus is security-heavy:

- backend upload scanning and filename validation
- secret redaction
- LLM JSON parsing
- score/summary helpers
- GitHub target resolution
- finalization flow with a stubbed host-side LLM runner
- `skill-evaluation` deterministic checks for the new MCP prohibition

The `Makefile` test target currently runs:

- `python3 -m py_compile eval/run_eval.py`
- Python unit tests for `.claude/skills/skill-evaluation/tests`
- `go test ./...` in `backend/`

## Skill-Evaluation Skill

The local skill at `.claude/skills/skill-evaluation/` evaluates `SKILL.md` files against the agentskills.io standard.

It now explicitly treats MCP usage as disallowed in evaluated skills:

- positive instructions to use MCP servers or `mcp__*` tools are deterministic security failures
- documentation now recommends CLI or direct API alternatives instead
- bundled scripts are checked too, not just markdown prose

This keeps evaluated skills portable, lower-cost, and easier to audit.

## GitHub Actions

There are two workflows:

### `deploy-app.yml`

- builds the Go app
- copies backend, frontend, Docker, eval files, and `Caddyfile` to the droplet
- writes `app.env`
- builds the local evaluator image on the droplet
- restarts the app under the `deploy` user
- validates health locally on the server
- restarts Caddy

### `deploy-image.yml`

- builds the evaluator image
- pushes it to GHCR
- pulls the latest image on the droplet

The actions in these workflows were updated to newer major versions, including:

- `actions/checkout@v6`
- `actions/setup-go@v6`
- `docker/build-push-action@v7`
- `appleboy/ssh-action@v1.2.5`
- `appleboy/scp-action@v1.0.0`

## Deployment Notes

- App is deployed on a DigitalOcean droplet.
- Caddy handles HTTP serving and domain routing.
- `agents-skill-eval.com` is live.
- `www` redirect behavior was fixed separately and is expected to keep pointing at the root domain.

## Environment Variables

Backend/runtime variables used by the app include:

- `PORT`
- `REDIS_ADDR`
- `EVAL_DOCKER_IMAGE`
- `EVAL_DOCKER_NETWORK`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_MODEL`
- `ANTHROPIC_MAX_TOKENS`
- `SENTRY_DSN`
- `DISABLE_ABUSE_PROTECTION`

## Repository Layout

```text
.
├── .claude/skills/skill-evaluation/
├── .github/workflows/
├── backend/
├── docker/
├── eval/
├── frontend/
├── Caddyfile
├── Makefile
└── README.md
```

## Notes

- The evaluator intentionally does not expose token estimates in the product surface.
- The Redis queue is used for asynchronous job processing and progress tracking.
- Abuse protection includes queue-depth limiting, IP rate limiting, and optional ASN-based blocking when the MaxMind database is present.
