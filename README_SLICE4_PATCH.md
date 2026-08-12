# README edits for Sprint 0 — Slice 4

Apply these edits directly to `README.md`; do not commit this patch file.

1. Replace **"Polymarket Insider Signal Detection System"** with:
   **"Prediction-Market Anomaly Detection Research System"**

2. In the Layer 2 architecture box replace:
   `Confidence ≥ 0.80 confirmed`
   with:
   `model-reported confidence ≥ 0.80 passes the review gate (uncalibrated)`

3. Replace statements that call Mistral "the quality gate" with:
   `the LLM-assisted review gate`

4. Add an Evaluation section:

```markdown
## Evaluation

PolyAugur includes an offline evaluation harness for manually labelled benchmark cases.
It reports precision, recall, F1, Brier score, expected calibration error (ECE), and
optional token-cost estimates without requiring live API access.

The initial benchmark measures agreement with a review rubric, **not proof of actual
insider trading**. LLM confidence values are model-reported scores and are not treated
as calibrated probabilities.

See [`docs/EVALUATION.md`](docs/EVALUATION.md).

Current public evidence status:

- Precision: **not measured**
- Recall: **not measured**
- Calibration: **not measured**
- Model API cost: **not measured**
```

5. Remove the current hard-coded `$2–5/day` Cost Profile and replace it with:

```markdown
## Cost Measurement

API cost is not hard-coded because provider pricing changes over time. The offline
evaluator can estimate cost from recorded token counts when explicit current pricing
is supplied. No cost figure is published until it is reproducibly measured.
```

6. Remove or clearly label as illustrative any pipeline example that claims concrete
market counts such as `800 → 20 → 12 → 4` unless backed by a saved reproducible run.

7. Remove the `Production-ready` bullet. Replace it with:

```markdown
- **Operational scaffolding**: systemd configuration, health checks, retry logic and
  a non-root Docker runtime are provided; production readiness is not claimed.
```
