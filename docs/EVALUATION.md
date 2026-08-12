# Evaluation

PolyAugur's evaluation is deliberately offline and reproducible.

## What the benchmark measures

The current benchmark measures **agreement with a manually defined review rubric**:
should a market scenario be escalated for manual review as potentially information-sensitive?

It does **not** establish that insider trading actually occurred. Actual insider status is
usually not observable from public market data, so the repository must not present these
labels as legal or factual determinations.

The initial `evaluation/labels.jsonl` set contains synthetic rubric cases. It is useful for
testing the evaluation pipeline and prompt/policy consistency, but it is not evidence of
real-world detection quality.

## Prediction file

Create `evaluation/predictions.jsonl` with exactly one record for every `case_id`:

```json
{"case_id":"rubric-001","anomaly_detected":true,"confidence_score":0.87,"input_tokens":1200,"output_tokens":180}
```

`input_tokens` and `output_tokens` are optional. Do not invent them.

## Run

```bash
python -m src.evaluation \
  --labels evaluation/labels.jsonl \
  --predictions evaluation/predictions.jsonl \
  --threshold 0.80 \
  --output evaluation/results/report.json
```

No external API is called by the evaluator.

## Metrics

The report includes:

- precision, recall, F1 and accuracy for the production decision rule;
- confusion-matrix counts;
- Brier score;
- expected calibration error (ECE) with fixed-width confidence bins;
- a calibration table;
- token totals and estimated cost only when token counts and explicit provider pricing are supplied.

The production decision rule is:

```text
positive = anomaly_detected AND confidence_score >= threshold
```

## Calibration limitation

A model confidence score is not a probability merely because it is between 0 and 1.
Brier score and ECE are reported to test calibration against the benchmark labels.
Until there is a sufficiently representative labelled dataset, PolyAugur must describe
LLM confidence as **model-reported confidence**, not as a calibrated probability.

## Cost reporting

The repository does not hard-code provider prices. Pricing changes over time.
If you have recorded token counts and current provider pricing, pass it explicitly:

```bash
python -m src.evaluation \
  --labels evaluation/labels.jsonl \
  --predictions evaluation/predictions.jsonl \
  --input-cost-per-million <USD> \
  --output-cost-per-million <USD>
```

If token counts or prices are missing, the report states `not_measured`.

## Current evidence status

As long as no real prediction file has been generated, the repository should report:

- Precision: **not measured**
- Recall: **not measured**
- Calibration: **not measured**
- Model API cost: **not measured**

This is preferable to publishing fabricated or irreproducible metrics.

## Next evidence level

A stronger future benchmark should use a frozen, manually reviewed set of real historical
markets with documented sources, annotation rules, inter-rater review, and a fixed model
version/prompt. Those results should be reported separately from the synthetic rubric set.
