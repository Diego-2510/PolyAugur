# syntax=docker/dockerfile:1

FROM python:3.14.0-slim-bookworm@sha256:d13fa0424035d290decef3d575cea23d1b7d5952cdf429df8f5542c71e961576

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 polyaugur \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --home-dir /home/polyaugur \
        --shell /usr/sbin/nologin \
        polyaugur

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY --chown=polyaugur:polyaugur config.py run.py ./
COPY --chown=polyaugur:polyaugur src ./src
COPY --chown=polyaugur:polyaugur schemas ./schemas

RUN mkdir -p /app/data /app/logs /app/exports \
    && chown -R polyaugur:polyaugur /app/data /app/logs /app/exports

USER 10001:10001

VOLUME ["/app/data", "/app/logs", "/app/exports"]

CMD ["python", "run.py"]
