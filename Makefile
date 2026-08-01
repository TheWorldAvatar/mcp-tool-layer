# mini_marie Docker shortcuts (requires Docker Compose v2)

.PHONY: build mof-mcp city-mcp bench-city-complex bench-mof-complex shell gui kgqa-gui test-mcp-surface test-contract-rejection test-package-runtime test-semantic-acceptance

build:
	docker compose build

mof-mcp:
	docker compose run --rm -i mof-twa

city-mcp:
	docker compose run --rm -i twa-city

bench-city-complex:
	docker compose --profile bench run --rm bench-city-complex

bench-mof-complex:
	docker compose --profile bench run --rm bench-mof-complex

shell:
	docker compose --profile cli run --rm workflow-cli sh

gui:
	docker compose --profile gui up competency-gui

kgqa-gui:
	docker compose --profile kgqa up kgqa-gui

test-mcp-surface:
	python -m pytest -m mcp_surface

test-contract-rejection:
	python -m pytest -m contract_rejection

test-package-runtime:
	python -m pytest -m package_runtime

test-semantic-acceptance:
	python -m pytest -m semantic_acceptance
