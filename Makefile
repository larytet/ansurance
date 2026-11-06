COMPOSE = docker compose -f deploy/compose/docker-compose.yml --env-file deploy/compose/.env

.PHONY: upgrade backup backup-verify restore conformance test-fast test-slow e2e-m1 up down logs ps smoke ci test-backend test-web lint

deploy/compose/.env:
	@echo "Creating deploy/compose/.env with random secrets (edit BIND_ADDR for your VPN)"
	@sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$(openssl rand -hex 16)/" \
	     -e "s/^OBJECTSTORE_PASSWORD=.*/OBJECTSTORE_PASSWORD=$$(openssl rand -hex 16)/" \
	     -e "s/^OBJECTSTORE_APP_PASSWORD=.*/OBJECTSTORE_APP_PASSWORD=$$(openssl rand -hex 16)/" \
	     -e "s/^BACKUP_PASSPHRASE=.*/BACKUP_PASSPHRASE=$$(openssl rand -hex 24)/" \
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

# Backups (see deploy/backup/backup.sh and docs/OPERATIONS.md)
backup:
	$(COMPOSE) run --rm --no-deps backup backup

backup-verify:
	$(COMPOSE) run --rm --no-deps backup verify

# Upgrade to the code in this checkout: backup, build, health gate, automatic rollback
upgrade:
	./scripts/upgrade.sh

# Disaster recovery into an empty stack: make restore DIR=/path/to/backups/20261106T013000Z
restore:
	./scripts/restore.sh $(DIR)

# Profile parity: the same black-box checks CI runs against the AWS dev deployment.
conformance:
	./scripts/conformance_compose.sh

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app
	cd web && npm run lint && npm run typecheck

test-backend:
	cd backend && uv run pytest -q

# everything except the slow container-based tests (real OCR run, Terraform plans, ClamAV)
test-fast:
	cd backend && uv run pytest -q -m "not slow"

test-slow:
	cd backend && uv run pytest -q -m slow

test-web:
	cd web && npm test

ci: lint test-backend test-web
