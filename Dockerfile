FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install --requirement requirements.txt

ARG INSTALL_NLP=0
COPY requirements-nlp.txt ./
RUN if [ "$INSTALL_NLP" = "1" ]; then python -m pip install --requirement requirements-nlp.txt; fi

COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app docker/entrypoint.sh ./docker/entrypoint.sh

RUN sed -i 's/\r$//' ./docker/entrypoint.sh \
    && find ./scripts -type f -exec sed -i 's/\r$//' {} + \
    && chmod +x ./docker/entrypoint.sh
RUN chmod +x ./scripts/pncp \
    && ln -s /app/scripts/pncp /usr/local/bin/pncp \
    && chmod +x ./scripts/ingestir_tce_despesas.py \
    && ln -s /app/scripts/ingestir_tce_despesas.py /usr/local/bin/tce-ingestir \
    && chmod +x ./scripts/enriquecer_tce_fornecedores.py \
    && ln -s /app/scripts/enriquecer_tce_fornecedores.py /usr/local/bin/tce-enriquecer \
    && chmod +x ./scripts/ingestir_enriquecer_tce.py \
    && ln -s /app/scripts/ingestir_enriquecer_tce.py /usr/local/bin/tce-ingestir-enriquecer

RUN mkdir -p /opt/huggingface && chown app:app /opt/huggingface

USER app 

EXPOSE 8000

ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
