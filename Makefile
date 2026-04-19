.PHONY: start stop restart build test image image-app health

PORT ?= 18080
REDIS_ADDR ?= 127.0.0.1:6379
DISABLE_ABUSE_PROTECTION ?= true
EVAL_DOCKER_IMAGE ?= agents-skill-eval-test
APP_DOCKER_IMAGE ?= agents-skill-eval-app-test
ANTHROPIC_MODEL ?= claude-sonnet-4-6
ANTHROPIC_MAX_TOKENS ?= 2500
SERVER_LOG ?= /tmp/agents-skill-eval-server.log
SERVER_PID ?= /tmp/agents-skill-eval-server.pid

start:
	@mkdir -p backend/bin
	@$(MAKE) stop >/dev/null 2>&1 || true
	@cd backend && go build -o bin/app .
	@PORT=$(PORT) REDIS_ADDR=$(REDIS_ADDR) DISABLE_ABUSE_PROTECTION=$(DISABLE_ABUSE_PROTECTION) ANTHROPIC_MODEL=$(ANTHROPIC_MODEL) ANTHROPIC_MAX_TOKENS=$(ANTHROPIC_MAX_TOKENS) ANTHROPIC_API_KEY="$$ANTHROPIC_API_KEY" OPENAI_API_KEY="$$OPENAI_API_KEY" nohup ./backend/bin/app > $(SERVER_LOG) 2>&1 < /dev/null & echo $$! > $(SERVER_PID)
	@sleep 3
	@$(MAKE) health
	@echo "app started on http://127.0.0.1:$(PORT)"

stop:
	@if [ -f $(SERVER_PID) ]; then kill "$$(cat $(SERVER_PID))" 2>/dev/null || true; rm -f $(SERVER_PID); fi
	@lsof -tiTCP:$(PORT) -sTCP:LISTEN | xargs kill 2>/dev/null || true
	@pkill -f "go run ." 2>/dev/null || true
	@pkill -f "/go-build/.*/agents-skill-eval" 2>/dev/null || true
	@pkill -f "/backend/bin/app" 2>/dev/null || true
	@pkill -f "/bin/app" 2>/dev/null || true
	@echo "app stopped"

restart: stop start

build:
	@mkdir -p backend/bin
	@cd backend && go build -o bin/app .
	@docker build -f docker/Dockerfile -t $(EVAL_DOCKER_IMAGE) .
	@docker build -f docker/Dockerfile.app -t $(APP_DOCKER_IMAGE) .

test:
	@python3 -m py_compile eval/run_eval.py
	@python3 -m unittest discover -s .claude/skills/skill-evaluation/tests -p 'test_*.py'
	@cd backend && go test ./...

image:
	@docker build -f docker/Dockerfile -t $(EVAL_DOCKER_IMAGE) .

image-app:
	@docker build -f docker/Dockerfile.app -t $(APP_DOCKER_IMAGE) .

health:
	@curl -fsS http://127.0.0.1:$(PORT)/health
