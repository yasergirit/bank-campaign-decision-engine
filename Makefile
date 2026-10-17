.PHONY: install model check test db-test image serve serving-verify serving-logs serving-down

PYTHON := .venv/bin/python
COMPOSE_SERVING := POSTGRES_PASSWORD=unused-by-serving-profile docker compose

install:
	uv sync --locked --inexact
	uv pip install --python $(PYTHON) --require-hashes -r requirements-ui.lock $(if $(filter Linux,$(shell uname -s)),-r requirements-container.lock,)
	uv pip check --python $(PYTHON)

model:
	$(PYTHON) scripts/fetch_model.py

check:
	uv run --locked --inexact ruff check src app tests scripts
	uv run --locked --inexact ruff format --check src app tests scripts
	MYPYPATH=src uv run --locked --inexact mypy src app scripts

test:
	uv run --locked --inexact pytest -q -m "not integration"

db-test:
	BANK_CAMPAIGN_DB_TESTS=1 uv run --locked --inexact pytest -q -m integration

image:
	docker build --tag bank-campaign-decision-engine:local .

serve:
	$(PYTHON) scripts/verify_serving.py --preflight-only
	$(MAKE) image
	$(COMPOSE_SERVING) up --detach --no-build --wait api ui

serving-verify:
	$(PYTHON) scripts/verify_serving.py

serving-logs:
	$(COMPOSE_SERVING) logs --follow api ui

serving-down:
	$(COMPOSE_SERVING) stop api ui
	$(COMPOSE_SERVING) rm --force api ui
