# Shortcuts for the usual commands. Everything also works without make: each target is
# a single, readable command.

UV ?= uv
tenant ?= tenant_a

.PHONY: help up down logs ps migrate seed token test lint eval eval-real

help:
	@echo "up       build and start the whole stack in the background"
	@echo "down     stop the stack (data volumes are kept)"
	@echo "logs     follow the logs of every service"
	@echo "ps       list the services and their health"
	@echo "migrate  run the one-shot migrate service again (key, migrations, seed)"
	@echo "seed     upsert the demo tenants"
	@echo "token    print a test JWT: make token tenant=tenant_b"
	@echo "test     backend tests (needs the stack's Postgres on localhost:5433)"
	@echo "lint     ruff check + format check"
	@echo "eval     behaviour evaluation against the running stack: make eval ARGS='--only id1,id2'"
	@echo "eval-real  the held-out real-contract set: make eval-real PDFS=/path/to/pdfs"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps -a

migrate:
	docker compose run --rm migrate

# The migrate service is the one with the owner credentials.
seed:
	docker compose run --rm migrate python scripts/seed.py

token:
	docker compose exec api python scripts/issue_token.py --tenant $(tenant)

test:
	cd backend && $(UV) run pytest -q

lint:
	cd backend && $(UV) run ruff check . && $(UV) run ruff format --check .

# Evaluation harness (ADR-014), against the RUNNING stack (`make up` first). It runs in a
# one-off container of the backend image as the `migrate` service: on the Compose network,
# with the JWT key volume and the owner URL (for --create-tenants). The current scripts
# and sample data are mounted, so no rebuild is needed after editing them.
EVAL_RUN = docker compose run --rm --no-deps \
	-v "$(CURDIR)/backend/scripts:/app/scripts:ro" -v "$(CURDIR)/sample_data:/sample_data:ro"
EVAL_CMD = migrate python scripts/eval.py --api http://api:8000

eval:
	$(EVAL_RUN) $(EVAL_CMD) --questions /sample_data/questions.yaml --pdfs /sample_data/pdfs $(ARGS)

# PDFS: a folder with one subfolder per tenant of the questions file (e.g. tenant_eval/).
EVAL_REAL_QUESTIONS ?= sample_data/calibration/questions_eval_real.yaml

eval-real:
	@test -n "$(PDFS)" || { echo "usage: make eval-real PDFS=/path/to/pdfs"; exit 2; }
	$(EVAL_RUN) -v "$(abspath $(PDFS)):/eval_pdfs:ro" $(EVAL_CMD) --create-tenants \
		--questions /$(EVAL_REAL_QUESTIONS) --pdfs /eval_pdfs $(ARGS)
