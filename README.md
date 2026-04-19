# agents-skill-eval

`agents-skill-eval` evaluates agent skill packages from uploaded files or GitHub URLs.

Live site: `https://agents-skill-eval.com`

## What It Does

- Accepts a `SKILL.md` plus optional supporting files.
- Performs upload-time security screening before a job is queued.
- Runs deterministic evaluation inside a locked-down Docker container.
- Returns deterministic findings, summary, progress, and overall score by default without sending uploaded contents to third-party AI providers.

## Architecture

The app has three main parts:

1. `frontend/`
   Simple static UI for uploads, GitHub URLs, polling, and result display.

2. `backend/main.go`
   Go HTTP server, embedded worker, Redis-backed queue, upload validation, GitHub fetch logic, secret redaction, deterministic scoring, build metadata, and privacy-safe infrastructure telemetry.

3. `.claude/skills/skill-evaluation/scripts/eval.py`
   Deterministic evaluator that runs inside the isolated container and is also the skill-owned script used by the LLM workflow.

4. Host-side optional review
    If the user explicitly opts in and the selected provider is configured, the backend sends the uploaded skill content and supporting context to that provider for an extra review step after deterministic evaluation.

There is also a local skill package under `.claude/skills/skill-evaluation/` used to evaluate `SKILL.md` files against the agentskills.io standard.

## Evaluation Flow

1. User uploads files or submits a GitHub URL.
2. Backend validates file names, MIME types, and content before queueing.
3. Files are written into a temporary input directory.
4. Worker launches the bundled deterministic evaluator process inside the app container.
5. `.claude/skills/skill-evaluation/scripts/eval.py` reads the uploaded skill package, runs deterministic checks, gathers supporting context, and returns deterministic results as JSON.
6. Go parses that JSON, computes the final summary/score, optionally calls the selected LLM provider for opt-in review output, stores the final payload in Redis, and exposes it via `/result/{jobId}`.

## Security Model

The main hardening goal is to treat uploaded skills and supporting files as untrusted input.

### Upload and Fetch Defenses

- Blocks dangerous filenames such as `.env`, private keys, credential files, and key material.
- Blocks suspicious text patterns including prompt injection, secret exfiltration, metadata-service access, container escape hints, and embedded credentials.
- Rejects supporting files that try to access secrets, environment variables, local credential files, subprocess execution, or network calls.
- Rejects PDFs with active content markers such as JavaScript, launch actions, or embedded files.
- Restricts GitHub fetches to `github.com` and `raw.githubusercontent.com`.
- Restricts GitHub directory imports to a small allowlist of file types relevant to skills.

### Runtime Isolation

The production deployment runs as a single application container that bundles:

- the Go backend
- the static frontend
- the deterministic evaluator Python runtime
- a local Redis instance for queue and progress state

This removes GitHub-to-DigitalOcean secret propagation. Runtime API keys now live only on the droplet.

### Secret Handling

- Progress lines, stored errors, and stored results are redacted before persistence.
- Uploaded skill contents are not sent to third-party AI or observability providers unless the user explicitly enables optional LLM review for that run.
- When optional LLM review is enabled, the uploaded skill content and supporting context for that run are sent to the selected provider to generate the additional review fields.

This keeps the default path on our infrastructure only, with a clearly separated opt-in provider path.

## Default Evaluation Mode

The default path is deterministic-only:

- Container: deterministic parsing and extraction only
- Host: deterministic scoring, summary generation, and result storage

That keeps the privacy boundary smaller and easier to audit.

An optional provider-backed review path can be enabled explicitly per run. If it is not selected, uploaded content stays on the deterministic-only path.

## API Endpoints

- `GET /health`
  Returns app and Redis health.

- `GET /version`
  Returns deployed version and commit metadata.

- `GET /about`
  Serves the supporting product context that used to live on the homepage.

- `POST /upload`
  Accepts multipart file uploads and/or a `githubUrl` form field.

- `GET /result/{jobId}`
  Returns pending status with progress, final result, or structured error.

- `GET /robots.txt`
- `GET /sitemap.xml`
- `GET /`

## Frontend Behavior

- The homepage keeps only the evaluator flow and live run/result surfaces.
- Supporting product context now lives on `/about` so the evaluator stays focused.
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

### Start Locally

```bash
make build
make start
```

Set only the provider API keys you want to make available for optional review:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`

Optional per-provider model overrides:

- `ANTHROPIC_MODEL`
- `OPENAI_MODEL`

If no provider is selected for a run, or the selected provider is not configured on the server, the evaluation falls back to deterministic-only output.

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
- score/summary helpers
- GitHub target resolution
- finalization flow with real opt-in provider parsing and deterministic fallback behavior
- `skill-evaluation` deterministic checks for the new MCP prohibition

The `Makefile` test target currently runs:

- `python3 -m py_compile .claude/skills/skill-evaluation/scripts/eval.py`
- Python unit tests for `.claude/skills/skill-evaluation/tests`
- `go test ./...` in `backend/`

## Preferred Script Types In Skills

When a skill bundles helper scripts, prefer:

- `.sh`
- `.py`

Why these two:

- both are commonly available on Linux and macOS without asking the user to install another runtime first
- shell scripts can run directly via `/bin/sh` or `/bin/bash`
- Python 3 is standard across Linux environments and widely available in agent tooling setups
- neither requires a compile step or runtime manager such as `nvm` or `go install`

Discouraged for portable skills:

- `.js`
- `.ts`
- `.go`
- other runtime-dependent script formats

These are not banned by the website evaluator, but the local `skill-evaluation` skill now flags them as soft portability warnings because they require extra runtime setup and reduce cross-agent portability.

## Skill-Evaluation Skill

The local skill at `.claude/skills/skill-evaluation/` evaluates `SKILL.md` files against the agentskills.io standard.

It now explicitly treats MCP usage as disallowed in evaluated skills:

- positive instructions to use MCP servers or `mcp__*` tools are deterministic security failures
- documentation now recommends CLI or direct API alternatives instead
- bundled scripts are checked too, not just markdown prose

This keeps evaluated skills portable, lower-cost, and easier to audit.

## GitHub Actions

There is one workflow:

### `publish-app.yml`

- builds the single runtime app image
- embeds the commit SHA as build metadata
- pushes `ghcr.io/sasa-fajkovic/agents-skill-eval-app:latest`
- pushes an immutable commit tag alongside `latest`

The actions in these workflows were updated to newer major versions, including:

- `actions/checkout@v6`
- `actions/setup-go@v6`
- `docker/build-push-action@v7`

## Deployment Notes

- App is deployed on a DigitalOcean droplet.
- The droplet should poll GHCR locally for the latest app image and restart itself after a successful health check.
- Runtime API keys should be stored only on the droplet, not in GitHub Actions secrets.
- Caddy handles HTTP serving and domain routing.
- `agents-skill-eval.com` is live.
- `www` redirect behavior was fixed separately and is expected to keep pointing at the root domain.

### Droplet Bootstrap

For a fresh droplet, the minimum working setup is:

1. Install Docker and make sure the `deploy` user is in the `docker` group.
2. Create `/home/deploy/.zshenv` with runtime secrets and model settings:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
export ANTHROPIC_MODEL=claude-sonnet-4-6
export OPENAI_MODEL=gpt-5.3-chat-latest
export SENTRY_ENVIRONMENT=production
export APP_ENV=production
export DISABLE_ABUSE_PROTECTION=false
```

3. Set ownership and permissions:

```bash
chown deploy:deploy /home/deploy/.zshenv
chmod 600 /home/deploy/.zshenv
```

4. Install `docker/deploy-check.sh` to `/opt/agents-skill-eval/deploy-check.sh` and make it executable:

```bash
sudo install -o root -g root -m 755 docker/deploy-check.sh /opt/agents-skill-eval/deploy-check.sh
```

5. Install systemd units:

`/etc/systemd/system/agents-skill-eval-poll.service`

```ini
[Unit]
Description=Poll GHCR and redeploy agents-skill-eval when needed
After=docker.service network-online.target
Wants=docker.service network-online.target

[Service]
Type=oneshot
User=deploy
Group=deploy
Environment=HOME=/home/deploy
ExecStart=/opt/agents-skill-eval/deploy-check.sh
```

`/etc/systemd/system/agents-skill-eval-poll.timer`

```ini
[Unit]
Description=Run agents-skill-eval deployment poll every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
Unit=agents-skill-eval-poll.service

[Install]
WantedBy=timers.target
```

6. Reload and enable the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now agents-skill-eval-poll.timer
```

7. If public GHCR pulls fail unexpectedly, remove any stale Docker auth left on the droplet:

```bash
rm -f /home/deploy/.docker/config.json
```

8. Start one manual deployment check:

```bash
sudo systemctl restart agents-skill-eval-poll.service
curl -fsS http://127.0.0.1:8080/health
curl -fsS http://127.0.0.1:8080/version
```

The timer then checks GHCR every 60 seconds and restarts the app container when a new image digest appears.

If the poll service fails immediately, verify `/home/deploy/.zshenv` is owned by `deploy:deploy` and still has `600` permissions.

## Environment Variables

Backend/runtime variables used by the app include:

- `PORT`
- `REDIS_ADDR`
- `SENTRY_DSN`
- `SENTRY_ENVIRONMENT`
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
- Abuse protection includes queue-depth limiting and IP rate limiting, with Cloudflare recommended in front of the app for bot and DDoS protection.
