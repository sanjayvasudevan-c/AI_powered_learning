.PHONY: check overview

check:
	uv run ruff check .
	uv run pytest -q

overview:
	uv run python docs/build_overview.py
