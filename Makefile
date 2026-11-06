COMPOSE = docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/.env

.PHONY: e2e-m1 up down logs ps smoke ci test-backend test-web lint

deploy/compose/.env:
	@echo "Creating deploy/compose/.env with random secrets (edit BIND_ADDR for your VPN)"
	@sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$(openssl rand -hex 16)/" \
	     -e "s/^OBJECTSTORE_PASSWORD=.*/OBJECTSTORE_PASSWORD=$$(openssl rand -hex 16)/" \
	     deploy/compose/.env.example > $@

up: deploy/compose/.env
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

smoke:
	./scripts/smoke.sh

e2e-m1:
	uv run --project backend python scripts/e2e_m1.py

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app
	cd web && npm run lint && npm run typecheck

test-backend:
	cd backend && uv run pytest -q

test-web:
	cd web && npm test

ci: lint test-backend test-web
