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
  agent/      query understanding: LLM extraction, resolution, follow-ups, CLI
  api/        FastAPI routes (later)
  tests/
ingestion/    RAG document ingestion (later)
scripts/      manual smoke scripts (real APIs)
evals/        real-LLM evals for understanding and answers
```

## Install

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in the API keys (see comments in the file)
```

## Run tests

From the repository root:

```bash
pytest
```

Tests mock all HTTP calls, so they need no API keys or network.

## Smoke test against the real APIs

Needs both keys in `.env`; the OpenWeather key must have the "One Call API 3.0" subscription.

```bash
python scripts/smoke_weather.py "Jaipur" tomorrow evening
```

## Chat with the agent

Needs `GOOGLE_API_KEY` and `LLM_MODEL` in `.env` (plus the weather and geocoding keys). Leave
`LLM_MODEL` empty once to list the Flash models your key can use. On the free tier each model
allows about 20 requests per day; `EXTRACTION_MODEL` / `ANSWER_MODEL` can split the two LLM calls
per message across models with separate quotas.

```bash
python -m backend.agent.cli --lat 26.91 --lon 75.79 --debug   # --lat/--lon simulate the device
```

## Evals (real LLM, run manually)

```bash
python evals/run_understanding.py --pause 5            # extraction accuracy per slot
python evals/run_answers.py --model gemini-2.5-flash   # answers on recorded weather; cached in evals/.cache/
```
