.PHONY: start stop restart build test image health

PORT ?= 18080
REDIS_ADDR ?= 127.0.0.1:6379
DISABLE_ABUSE_PROTECTION ?= true
EVAL_DOCKER_IMAGE ?= agents-skill-eval-test
EVAL_DOCKER_NETWORK ?= bridge
ANTHROPIC_MODEL ?= claude-sonnet-4-20250514
ANTHROPIC_MAX_TOKENS ?= 2500
SERVER_LOG ?= /tmp/agents-skill-eval-server.log
SERVER_PID ?= /tmp/agents-skill-eval-server.pid

start:
	@if [ -z "$$ANTHROPIC_API_KEY" ]; then echo "ANTHROPIC_API_KEY is not set"; exit 1; fi
	@mkdir -p backend/bin
	@PORT=$(PORT) REDIS_ADDR=$(REDIS_ADDR) DISABLE_ABUSE_PROTECTION=$(DISABLE_ABUSE_PROTECTION) EVAL_DOCKER_IMAGE=$(EVAL_DOCKER_IMAGE) EVAL_DOCKER_NETWORK=$(EVAL_DOCKER_NETWORK) ANTHROPIC_MODEL=$(ANTHROPIC_MODEL) ANTHROPIC_MAX_TOKENS=$(ANTHROPIC_MAX_TOKENS) ANTHROPIC_API_KEY="$$ANTHROPIC_API_KEY" go run ./backend > $(SERVER_LOG) 2>&1 & echo $$! > $(SERVER_PID)
	@sleep 3
	@$(MAKE) health
	@echo "app started on http://127.0.0.1:$(PORT)"

stop:
	@if [ -f $(SERVER_PID) ]; then kill "$$(cat $(SERVER_PID))" 2>/dev/null || true; rm -f $(SERVER_PID); fi
	@pkill -f "go run ./backend" 2>/dev/null || true
	@echo "app stopped"

restart: stop start

build:
	@mkdir -p backend/bin
	@cd backend && go build -o bin/app .
	@docker build -f docker/Dockerfile -t $(EVAL_DOCKER_IMAGE) .

test:
	@python3 -m py_compile eval/run_eval.py
	@cd backend && go test ./...

image:
	@docker build -f docker/Dockerfile -t $(EVAL_DOCKER_IMAGE) .

health:
	@curl -fsS http://127.0.0.1:$(PORT)/health
