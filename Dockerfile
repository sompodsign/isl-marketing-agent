FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 1000 app

COPY requirements.txt ./
# libfribidi is loaded at runtime by Pillow's bundled Raqm so Bengali headline
# text on composed social cards gets correct complex-text shaping.
RUN apt-get update && apt-get install -y --no-install-recommends libfribidi0 libharfbuzz0b && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY --chown=app:app app ./app
COPY --chown=app:app public ./public
COPY --chown=app:app knowledge ./knowledge
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app run.py README.md ./

RUN mkdir -p /app/data/uploads && chown -R app:app /app/data

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
