# README changes for Sprint 0 — Slice 3

## 1. Add below the title

```markdown
[![CI](https://github.com/Diego-2510/PolyAugur/actions/workflows/ci.yml/badge.svg)](https://github.com/Diego-2510/PolyAugur/actions/workflows/ci.yml)
```

## 2. Add after the existing Quick Start section

````markdown
### Docker

The container runs as an unprivileged user and does not bake credentials or local data into the image.

```bash
# Build the pinned Python 3.12 runtime image
docker build -t polyaugur:local .

# Offline smoke test — no credentials or external API calls
docker run --rm polyaugur:local python run.py --help

# Run one real detection cycle using local credentials and named persistent volumes
docker run --rm \
  --env-file .env \
  -v polyaugur-data:/app/data \
  -v polyaugur-logs:/app/logs \
  -v polyaugur-exports:/app/exports \
  polyaugur:local \
  python run.py --once
```

The Docker image contains only runtime code, the JSON schema, and pinned runtime dependencies. Tests, local `.env` files, databases, logs, and exports are excluded from the build context.
````

## 3. Add to Project Structure

```text
├── Dockerfile                    # Reproducible non-root runtime image
├── .dockerignore                 # Excludes credentials, data and dev artifacts
├── pyproject.toml                # pytest / Ruff configuration
├── .github/
│   ├── workflows/ci.yml          # Tests, dependency audit, image build, Trivy scan
│   └── dependabot.yml            # pip, Actions and Docker dependency updates
```
