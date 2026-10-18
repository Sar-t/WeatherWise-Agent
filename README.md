# WeatherWise

An AI weather assistant that answers questions like "Will it rain in Pune tomorrow evening?"
The final system will have a frontend, a FastAPI backend, a LangGraph agent and tool services
(geocoding, weather, air quality, scoring, RAG guidelines).

This repository currently contains the foundation only: configuration, logging, shared data
models, errors and a date resolver.

## Layout

```
backend/
  core/       settings (.env) and logging
  services/   shared models, errors, date resolver (tool services go here later)
  agent/      LangGraph agent (later)
  api/        FastAPI routes (later)
  tests/
ingestion/    RAG document ingestion (later)
evals/        agent evaluations (later)
```

## Install

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in OPENWEATHER_API_KEY
```

## Run tests

From the repository root:

```bash
pytest
```
