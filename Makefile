# Shortcuts for the usual commands. Everything also works without make: each target is
# a single, readable command.

UV ?= uv
tenant ?= tenant_a

.PHONY: help up down logs ps migrate seed token test lint

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
