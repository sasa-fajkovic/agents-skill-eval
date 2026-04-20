# agents-skill-eval

`agents-skill-eval` is two things in one repository:

- a hosted web app for evaluating skill packages from local folders or GitHub URLs
- a reusable agent skill package under `.claude/skills/skill-evaluation/`

Live site: `https://agents-skill-eval.com`

## Use the Skill Directly in Your AI Agent

The evaluator is itself a portable agent skill. You can download the full package and use it directly in any compatible agent runtime to evaluate other skills from your own workflow, no web app needed.

Download the full folder:

```
.claude/skills/skill-evaluation/
```

Keep the whole package, not just `SKILL.md`, because the skill depends on `scripts/`, `references/`, and `tests/`.

Browse and download on GitHub:

- [`.claude/skills/skill-evaluation/`](https://github.com/sasa-fajkovic/agents-skill-eval/tree/main/.claude/skills/skill-evaluation)

The same Python evaluator (`scripts/eval.py`) is what the web app bundles and executes in production.

## Run with Docker

The easiest way to self-host is the prebuilt Docker image. It bundles the Go backend, frontend, Redis, and the deterministic evaluator into a single container.

```bash
docker run -p 8080:8080 ghcr.io/sasa-fajkovic/agents-skill-eval-app:latest
```

Open `http://localhost:8080` in your browser after the container starts.

### Environment Variables

All environment variables are optional. The app runs deterministic-only evaluations by default.

**AI review providers** (enable optional LLM-backed review):

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key. Enables Anthropic as an AI review provider. |
| `OPENAI_API_KEY` | OpenAI API key. Enables OpenAI as an AI review provider. |
| `GEMINI_API_KEY` | Google Gemini API key. Enables Gemini as a free AI review provider. |
| `GROQ_API_KEY` | Groq API key. Enables Groq as a free AI review provider. |

**Model and provider configuration:**

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Anthropic model to use for AI review. |
| `OPENAI_MODEL` | `gpt-4.1` | OpenAI model to use for AI review. |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model to use for AI review. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model to use for AI review. |
| `LLM_PROVIDER` | `anthropic` | Default AI review provider when multiple keys are set. |
| `ANTHROPIC_MAX_TOKENS` | `1200` | Max output tokens for Anthropic requests. |
| `OPENAI_MAX_TOKENS` | `1200` | Max output tokens for OpenAI requests. |
| `GEMINI_MAX_TOKENS` | `1200` | Max output tokens for Gemini requests. |
| `GROQ_MAX_TOKENS` | `1200` | Max output tokens for Groq requests. |
| `ANTHROPIC_BASE_URL` | Anthropic default | Override the Anthropic API endpoint. |
| `OPENAI_BASE_URL` | OpenAI default | Override the OpenAI API endpoint. |
| `GEMINI_BASE_URL` | Gemini default | Override the Gemini API endpoint. |
| `GROQ_BASE_URL` | Groq default | Override the Groq API endpoint. |

**Infrastructure:**

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | HTTP listen port. |
| `REDIS_ADDR` | `127.0.0.1:6379` | Redis address. Already running inside the Docker image. |
| `GITHUB_TOKEN` | — | GitHub personal access token for higher API rate limits on GitHub URL fetches. |
| `LOG_DIR` | `logs/` | Directory for structured job log files. |

**Example with AI review enabled:**

```bash
# Paid providers
docker run -p 8080:8080 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e OPENAI_API_KEY=sk-... \
  ghcr.io/sasa-fajkovic/agents-skill-eval-app:latest

# Free providers only
docker run -p 8080:8080 \
  -e GEMINI_API_KEY=AIza... \
  -e GROQ_API_KEY=gsk_... \
  ghcr.io/sasa-fajkovic/agents-skill-eval-app:latest
```

## How It Works

1. A user uploads a local skill folder or provides a GitHub URL.
2. The backend validates files and fetch targets before queueing work.
3. The worker runs `.claude/skills/skill-evaluation/scripts/eval.py` inside the app container.
4. The evaluator returns structured deterministic output as JSON.
5. The Go backend stores progress and result state, and optionally adds provider-backed AI review if explicitly enabled for that run.

Default behavior is deterministic-only. Uploaded skill content is not sent to third-party AI providers unless optional AI review is enabled.

## Main Parts

- `frontend/`
  Static HTML, CSS, and JavaScript for the site UI.

- `backend/main.go`
  Go HTTP server, queue worker, upload validation, GitHub fetch logic, progress tracking, and final result assembly.

- `.claude/skills/skill-evaluation/`
  The reusable skill package and the shared deterministic evaluator.

- `docker/`
  Single-container runtime image and droplet deploy polling script.

## Local Development

Requirements:

- Go
- Redis
- Python 3

Start locally:

```bash
make build
make start
```

Health check:

```bash
make health
```

Stop:

```bash
make stop
```

## Tests

Run everything:

```bash
make test
```

This covers:

- Python tests for `.claude/skills/skill-evaluation/tests`
- Go tests in `backend/`

## Deployment

Production is a single app container published to GHCR and pulled by the DigitalOcean droplet.

Current workflow:

- `.github/workflows/publish-app.yml`

Droplet behavior:

- polls GHCR every 60 seconds
- pulls the latest image
- restarts only after a healthy app start

Runtime secrets live on the droplet, not in GitHub Actions.

Key runtime helper:

- `docker/deploy-check.sh`

## Notes

- The evaluator prefers portable helper scripts in skills: `.sh` and `.py`
- Positive MCP usage instructions are treated as deterministic failures in evaluated skills
- The homepage is focused on running evaluations; supporting product context lives on `/about` and `/faq`
