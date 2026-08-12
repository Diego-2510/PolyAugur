# PolyAugur

[![CI](https://github.com/Diego-2510/PolyAugur/actions/workflows/ci.yml/badge.svg)](https://github.com/Diego-2510/PolyAugur/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Prediction-market anomaly detection and evaluation research system**

PolyAugur is a research-oriented pipeline for screening Polymarket markets for unusual activity and information-sensitive scenarios. It combines Gamma API ingestion, rule-based anomaly scoring, optional LLM-assisted review with strict JSON-Schema validation, CLOB trade heuristics, SQLite persistence, reporting, and an offline evaluation harness.

PolyAugur does **not** prove that insider trading occurred, does not execute trades, and does not present model confidence scores as calibrated probabilities.

---

## Why this project exists

Prediction markets combine market microstructure, event-driven data, noisy public information, and external APIs. PolyAugur explores how to build a reproducible detection pipeline around those constraints while keeping the distinction between:

- observed market data,
- rule-based heuristics,
- model-produced assessments,
- and actual ground truth.

The repository is structured as an engineering and evaluation project rather than a profitability claim.

---

## Architecture

```text
                    Polymarket Gamma API
                            |
                            v
                 +----------------------+
                 | Market ingestion     |
                 | normalization        |
                 | filtering            |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | Snapshot enrichment  |
                 | derived baseline     |
                 | real elapsed-time    |
                 | change metrics       |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | Rule-based anomaly   |
                 | scoring              |
                 |                      |
                 | volume               |
                 | price/liquidity      |
                 | topic heuristics     |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | LLM-assisted review  |
                 | optional             |
                 | batched              |
                 | strict JSON Schema   |
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | CLOB trade analysis  |
                 | concentration        |
                 | directional bias     |
                 | burst heuristics     |
                 +----------+-----------+
                            |
                            v
             +--------------+---------------+
             |                              |
             v                              v
      SQLite persistence             Telegram / reports
             |
             v
      Offline evaluation
      precision / recall
      Brier score / ECE
      optional cost estimate
```

---

## Current pipeline

### 1. Market ingestion

`src/data_fetcher.py` retrieves and normalizes market data from the Polymarket Gamma API.

Current filters include:

- minimum 24-hour volume: `$30,000`;
- active/not-expired checks;
- sports/live-event heuristics;
- deduplication across pagination pages.

Pagination is fail-closed: if an intermediate page cannot be retrieved or has an unexpected response structure, PolyAugur discards the partial scan instead of silently continuing with a gap.

### 2. Snapshot construction

Each market is converted into a normalized snapshot.

The current volume baseline is a **derived proxy**:

```text
all-time volume / market age in days
```

It should not be interpreted as a historical rolling-volume model.

Cross-observation metrics use the **actual elapsed time** between observations. The pipeline records:

- price change since the previous observation;
- rolling-24h volume change since the previous observation;
- elapsed seconds;
- price change linearly normalized to one hour.

The linearly normalized hourly value is a mathematical normalization only. It is **not** a forecast and is not presented as an observed one-hour return.

### 3. Elite pre-filter

Current default gates are configured in `config.py`:

| Parameter | Default |
|---|---:|
| Minimum 24h volume | `$30,000` |
| Minimum spike ratio | `2.5x` |
| Minimum recency ratio | `15%` |
| Maximum days to close | `90` |
| Anomaly threshold | `0.45` |

These values are engineering defaults and heuristics, not statistically optimized operating points.

### 4. Rule-based anomaly scoring

`src/anomaly_detector.py` combines several heuristic layers:

- volume-spike score;
- price/liquidity pressure;
- time-horizon adjustments;
- recency adjustments;
- topic-sensitive score multipliers.

Known non-target categories can be excluded with a hard blacklist, including examples such as:

- public social-media count markets;
- broad crypto-price targets;
- weather outcomes;
- selected sports outcomes;
- entertainment awards;
- public polling/sentiment markets.

Topic matches are **score boosters**, not evidence that a market contains insider trading.

### 5. Optional LLM-assisted review

Markets above the configured review threshold can be passed to `src/mistral_analyzer.py`.

Current defaults:

| Parameter | Default |
|---|---:|
| Review queue threshold | `0.45` |
| Batch size | `4` |
| Maximum LLM calls per cycle | `8` |
| Review confidence threshold | `0.80` |

LLM output is treated as untrusted external input.

`src/llm_contract.py` validates responses against `schemas/mistral_signal.schema.json` using JSON Schema Draft 2020-12. The contract rejects:

- missing required fields;
- unsupported enum values;
- invalid types;
- unexpected fields;
- invalid numeric ranges;
- incorrect batch cardinality.

A `confidence_score` is a **model-reported score**, not a calibrated probability.

If the LLM client is unavailable, the analyzer contains a rule-based fallback path. Live Mistral execution is intentionally not required for the repository's offline tests and evaluation.

### 6. CLOB trade heuristics

For confirmed candidates, `src/trade_analyzer.py` can inspect CLOB trade activity and derive features such as:

- large-trade counts;
- wallet concentration;
- directional bias;
- timing/burst indicators.

These values are heuristic evidence. They do not establish trader intent or identity.

### 7. Persistence and reporting

Signals can be written to SQLite through `src/signal_store.py`.

Additional operational components include:

- duplicate suppression;
- outcome tracking;
- CLI/HTML reporting;
- optional Telegram notifications;
- health checks and retry helpers;
- a systemd service definition.

These components provide operational scaffolding; **production readiness is not claimed**.

---

## Evaluation

PolyAugur includes a fully offline evaluation harness in `src/evaluation.py`.

The initial benchmark in `evaluation/labels.jsonl` contains **16 synthetic, manually defined rubric cases**. The label target is:

> Should this scenario be escalated for manual review as potentially information-sensitive?

The labels do **not** mean that insider trading actually occurred.

The evaluator supports:

- confusion-matrix counts;
- precision;
- recall;
- F1;
- accuracy;
- Brier score;
- expected calibration error (ECE);
- fixed-width calibration bins;
- optional token-count and cost reporting.

The decision rule is:

```text
positive =
    anomaly_detected == true
    AND confidence_score >= threshold
```

### Current public evidence status

| Metric | Status |
|---|---|
| Precision | **not measured** |
| Recall | **not measured** |
| Real-world calibration | **not measured** |
| Model API cost | **not measured** |

No real-world accuracy or cost figure is published until it is backed by a frozen prediction file and reproducible evaluation.

See [`docs/EVALUATION.md`](docs/EVALUATION.md) for the benchmark semantics and evaluation procedure.

### Run the evaluator

Create a prediction file with one record per benchmark case:

```json
{"case_id":"rubric-001","anomaly_detected":true,"confidence_score":0.87}
```

Then run:

```bash
python -m src.evaluation \
  --labels evaluation/labels.jsonl \
  --predictions evaluation/predictions.jsonl \
  --threshold 0.80 \
  --output evaluation/results/report.json
```

No external API call is required.

Optional token cost estimation:

```bash
python -m src.evaluation \
  --labels evaluation/labels.jsonl \
  --predictions evaluation/predictions.jsonl \
  --input-cost-per-million <USD> \
  --output-cost-per-million <USD>
```

Provider pricing is deliberately supplied at evaluation time rather than hard-coded.

---

## Testing

The repository contains deterministic tests for:

- time-delta semantics;
- Gamma API fixtures;
- pagination failure behavior;
- deduplication;
- HTTP 429 retry behavior;
- JSON-Schema validation;
- malformed and invalid LLM responses;
- LLM fallback integration;
- offline runtime startup;
- evaluation metrics and validation.

Install development dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Run the test suite:

```bash
python -m pytest -q
```

Additional local checks:

```bash
python -m ruff format --check src tests config.py run.py
python -m ruff check src tests config.py run.py
python -m compileall -q src tests config.py run.py
python -m pip_audit --requirement requirements.txt
```

GitHub Actions runs the same core checks on clean environments.

---

## Quick start

### Clone and install

```bash
git clone https://github.com/Diego-2510/PolyAugur.git
cd PolyAugur

python -m venv .venv
source .venv/bin/activate

python -m pip install -r requirements.txt
```

### Offline smoke test

No credentials are required:

```bash
python run.py --help
```

### Configuration

```bash
cp .env.example .env
```

Supported environment variables:

| Variable | Required for | Description |
|---|---|---|
| `MISTRAL_API_KEY` | Live LLM-assisted review | Mistral API credential |
| `TELEGRAM_BOT_TOKEN` | Telegram notifications | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram notifications | Target chat ID |
| `SIGNAL_DB_PATH` | Optional override | SQLite path, default `data/signals.db` |

Live Mistral access is not required for unit tests, contract tests, Docker smoke tests, or offline evaluation.

### Runtime commands

```bash
python run.py --help
python run.py --stats
python run.py --check
python run.py --health
python run.py --once
python run.py --cycles 10
```

Commands that contact external services depend on current API availability and credentials.

---

## Docker

The runtime image uses pinned Python 3.12 and runs as an unprivileged user.

Build:

```bash
docker build -t polyaugur:local .
```

Offline smoke test:

```bash
docker run --rm polyaugur:local python run.py --help
```

Verify non-root execution:

```bash
docker run \
  --rm \
  --entrypoint sh \
  polyaugur:local \
  -c 'id && test "$(id -u)" -ne 0'
```

A real run can use named volumes for mutable state:

```bash
docker run \
  --rm \
  --env-file .env \
  -v polyaugur-data:/app/data \
  -v polyaugur-logs:/app/logs \
  -v polyaugur-exports:/app/exports \
  polyaugur:local \
  python run.py --once
```

Secrets and local data should never be baked into the image.

---

## Dashboard and exports

`src/dashboard.py` provides CLI exploration and export functionality for stored signals.

Examples:

```bash
python -m src.dashboard
python -m src.dashboard --hours 72
python -m src.dashboard --whales
python -m src.dashboard --performance
python -m src.dashboard --export csv
python -m src.dashboard --export html
```

The repository also contains a dashboard screenshot in `docs/dashboard.png`.

---

## Project structure

```text
PolyAugur/
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/
│   ├── dashboard.png
│   └── EVALUATION.md
├── evaluation/
│   └── labels.jsonl
├── schemas/
│   └── mistral_signal.schema.json
├── src/
│   ├── anomaly_detector.py
│   ├── dashboard.py
│   ├── data_fetcher.py
│   ├── evaluation.py
│   ├── health.py
│   ├── llm_contract.py
│   ├── mistral_analyzer.py
│   ├── orchestrator.py
│   ├── performance_tracker.py
│   ├── retry.py
│   ├── signal_store.py
│   ├── telegram_notifier.py
│   ├── trade_analyzer.py
│   └── wallet_profiler.py
├── tests/
│   ├── fixtures/
│   ├── test_data_fetcher_fixtures.py
│   ├── test_evaluation.py
│   ├── test_llm_contract.py
│   ├── test_mistral_contract_integration.py
│   ├── test_runtime_smoke.py
│   └── test_time_semantics.py
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── LICENSE
├── config.py
├── polyaugur.service
├── pyproject.toml
├── requirements-dev.txt
├── requirements.txt
└── run.py
```

---

## Key engineering decisions

### Fail closed on incomplete pagination

A partial market scan is discarded when an intermediate page fails. This avoids silently treating incomplete market coverage as complete data.

### Real time semantics

Cross-observation metrics use measured elapsed time rather than assuming that one polling cycle represents a fixed 30-minute interval.

### Strict LLM boundary

LLM output is validated before downstream use. Invalid responses do not get silently coerced into the expected structure.

### Confidence is not probability

The pipeline keeps model-reported confidence separate from statistical calibration. Brier score and ECE are available for evaluation, but no calibrated-probability claim is made without evidence.

### No fabricated cost or accuracy claims

Costs require recorded token counts and explicit pricing. Accuracy metrics require a reproducible benchmark and stored predictions.

### No automated order execution

PolyAugur produces research signals and reports only. It does not submit trades.

---

## Known limitations

1. **The benchmark is synthetic.**  
   The current 16-case benchmark validates evaluation plumbing and rubric consistency, not real-world detection quality.

2. **Real-world precision and recall are not measured.**  
   A stronger benchmark requires frozen historical markets, documented annotations, and stored model predictions.

3. **LLM confidence is uncalibrated.**  
   Scores are treated as model outputs rather than probabilities.

4. **The current Gamma full-market scanner still uses offset pagination.**  
   A real large scan has previously failed at a higher offset. Migrating to Gamma's cursor/keyset pagination is the next reliability improvement before claiming robust full-market coverage.

5. **The volume baseline is a proxy.**  
   `all-time volume / market age` is not equivalent to a historical rolling baseline.

6. **Heuristic thresholds are not statistically optimized.**  
   Detection thresholds currently encode engineering assumptions and should be evaluated on a frozen historical dataset.

7. **Operational scaffolding is not a production guarantee.**  
   Docker, systemd, retries, health checks, and persistence improve operability but do not by themselves establish production readiness.

---

## Security and privacy

- Secrets belong in `.env` or an external secret store and are excluded from version control.
- The Docker build context should exclude credentials, databases, logs, exports, tests, and local virtual environments.
- LLM output is treated as untrusted input and schema-validated.
- The repository does not contain real order-execution logic.
- Synthetic evaluation fixtures contain no private or customer data.

---

## Tech stack

- Python 3.12
- Requests
- Pandas
- NumPy
- SQLite
- JSON Schema
- Mistral Python SDK
- Plotly / Streamlit
- Docker
- pytest
- Ruff
- pip-audit

---

## Background

PolyAugur originated as a hackathon project and has since been refactored into a more reproducible research and engineering repository with:

- deterministic fixtures;
- explicit time semantics;
- strict external-output contracts;
- containerized runtime support;
- offline evaluation;
- documented methodological limitations.

The focus is on making the system's assumptions and failure modes inspectable rather than presenting heuristic outputs as verified trading edge.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

## Disclaimer

PolyAugur is a research and educational project. It monitors publicly available prediction-market data for anomalous activity. It does not establish that any person engaged in insider trading or other wrongdoing, does not execute trades, and is not financial advice.