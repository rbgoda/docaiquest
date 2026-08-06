# Standalone DocAIQuest module.
PROJECT=docaiquest
up:
	docker compose -p $(PROJECT) --env-file .env up -d --build
	@echo "→ Documents at http://localhost:$${DOCAIQ_HOST_PORT:-8085}"
# Slim DEV stack — ~2 GB smaller backend (no torch/sentence-transformers/rapidocr);
# API embeddings + reranker off. Needs DOCAIQ_DASHSCOPE_API_KEY in .env.
up-min:
	docker compose -p $(PROJECT) --env-file .env -f docker-compose.yml -f docker-compose.min.yml up -d --build
	@echo "→ Documents (slim) at http://localhost:$${DOCAIQ_HOST_PORT:-8085}"
down:
	docker compose -p $(PROJECT) down
logs:
	docker compose -p $(PROJECT) logs -f --tail=100
smoke:
	@curl -sf http://localhost:8085/api/health && echo " ✓ health"
