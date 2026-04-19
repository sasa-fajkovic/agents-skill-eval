# agents-skill-eval

`agents-skill-eval` is two things in one repository:

- a hosted web app for evaluating skill packages from local folders or GitHub URLs
- a reusable agent skill package under `.claude/skills/skill-evaluation/`

Live site: `https://agents-skill-eval.com`

## What It Is

### Web app

The site accepts a skill package, runs deterministic checks by default, optionally adds provider-backed AI review, and returns a structured result with progress, findings, and score.

### Agent skill

The downloadable skill package lives at:

- `.claude/skills/skill-evaluation/`

Its single public evaluator entrypoint is:

- `.claude/skills/skill-evaluation/scripts/eval.py`

That same Python evaluator is what the web app bundles and executes in production. There is no separate duplicate evaluator anymore.

## Download The Skill

If you want to use the evaluator directly in an agent runtime, download the full folder:

- `.claude/skills/skill-evaluation/`

Keep the whole package, not just `SKILL.md`, because the skill depends on:

- `scripts/`
- `references/`
- `tests/`

GitHub folder:

- `https://github.com/sasa-fajkovic/agents-skill-eval/tree/main/.claude/skills/skill-evaluation`

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
- Docker
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

Optional AI review keys:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`

Optional model overrides:

- `ANTHROPIC_MODEL`
- `OPENAI_MODEL`

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
