# ===========================================
# ID Card Generator — Common Tasks
# ===========================================

.PHONY: help install dev test lint format run docker-build docker-up migrate

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	pip install -r requirements.lock

install-dev: ## Install development dependencies
	pip install -r requirements.lock
	pip install -r requirements-dev.txt

dev: ## Run development server
	FLASK_ENV=development python run.py

run: ## Run production server with gunicorn
	gunicorn -c gunicorn.conf.py "app:create_app()"

test: ## Run all tests
	python -m pytest -v --tb=short

test-coverage: ## Run tests with coverage
	python -m pytest -v --cov=app --cov-report=html --cov-report=term-missing

lint: ## Run linters
	flake8 app/ --max-line-length=120 --exclude=venv,migrations --extend-ignore=E203,W503
	isort --check-only app/
	black --check app/

format: ## Format code
	isort app/
	black app/

migrate: ## Run database migrations
	flask db upgrade

migrate-create: ## Create new migration (usage: make migrate-create MSG="description")
	flask db migrate -m "$(MSG)"

docker-build: ## Build Docker image
	docker build -t idcard-generator:latest .

docker-up: ## Start all services with docker compose
	docker compose up -d

docker-down: ## Stop all services
	docker compose down

docker-logs: ## View logs
	docker compose logs -f web

clean: ## Clean temporary files
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache htmlcov .coverage
