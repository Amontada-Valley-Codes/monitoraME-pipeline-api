# Contrato dos jobs PNCP

Este documento consolida o contrato persistido da fila existente em
`ingestion_jobs`. A fila é PostgreSQL no banco principal e é compartilhada com
outros consumidores internos; os tipos PNCP usam `source = "PNCP"`. Não criar
uma fila ou worker paralelo.

## Tipos e identidade

| `job_type` | Responsabilidade | Referência de escopo |
|---|---|---|
| `incremental` | Coletar uma janela de publicações, atualizações ou propostas abertas | fluxo em `parameters.flow` e no `scope_key` |
| `backfill` | Coletar janela histórica com capacidade limitada | janela e fluxo em `scope_key` |
| `normalize` | Normalizar versão RAW identificada por `raw_record_id` | ID do RAW em `scope_key` |
| `enrich` | Buscar detalhe, itens ou documentos seletivamente | identidade da contratação e alvo em `scope_key` |

Os nomes existentes são preservados. A dimensão de fluxo (publicação ou
atualização), filtros que alteram resultados e janela pertencem à identidade
da consulta e devem compor `scope_key`/`dedupe_key`. `dedupe_key` é única entre
jobs `PENDING`, `RUNNING` e `RETRY_WAIT`; jobs terminais podem manter o mesmo
valor histórico.

## Campos persistidos

| Campo | Contrato |
|---|---|
| `id` | Identificador interno gerado pelo banco. |
| `job_type`, `entity_type`, `source` | Tipo de operação, entidade processada e fonte (`PNCP` para coleta PNCP). |
| `scope_key`, `dedupe_key` | Escopo legível e identidade idempotente da unidade de trabalho. |
| `parameters` | Objeto JSON com parâmetros efetivos necessários para reproduzir a consulta; não guardar credenciais. |
| `config_version` | Versão opcional da configuração/consulta que gerou o job. |
| `priority`, `status` | Ordenação e estado (`PENDING`, `RUNNING`, `RETRY_WAIT`, `SUCCEEDED`, `FAILED`, `QUARANTINED`). |
| `window_start`, `window_end`, `page` | Janela temporal e página/cursor quando aplicável. A retomada de página depende de paginação estável. |
| `raw_record_id` | RAW relacionado, principalmente para normalização/enriquecimento. |
| `next_run_at`, `attempts`, `max_attempts` | Agendamento e limite de tentativas. |
| `locked_by`, `lease_token`, `lease_until`, `heartbeat_at` | Posse temporária do worker; conclusão, heartbeat e falha exigem o token atual. |
| `last_error`, `last_error_context` | Diagnóstico sanitizado; nunca incluir payload integral, credenciais ou cabeçalhos privados. |
| `created_at`, `updated_at` | Auditoria temporal. Eventos detalhados ficam em `ingestion_job_events`. |

`parameters` tem default `{}` para compatibilidade com registros e produtores
anteriores. Os agendadores de publicação, atualização, backfill e propostas
abertas registram fluxo, modalidade, UF quando aplicável e tamanho de página.
Filtros efetivos adicionais devem ser registrados nesse objeto. Esses jobs
recebem `config_version = "v1"`; uma alteração incompatível na composição da
consulta deve incrementar essa versão. Outros tipos de job podem deixar esse
campo nulo quando não representam uma consulta PNCP. Todos os instantes são
timezone-aware.

## Ciclo de vida e concorrência

Workers reservam em transação curta usando `FOR UPDATE SKIP LOCKED`; executam
HTTP fora da transação. O lease é identificado por `lease_token`, renovado pelo
heartbeat e validado em qualquer mudança terminal. Leases vencidos são
recuperados para execução futura. Retries transitórios usam `RETRY_WAIT` e
`next_run_at`; falhas permanentes/esgotadas ficam em `FAILED`; erro estrutural
de conteúdo fica em `QUARANTINED`. Replay é explícito e auditado.

Jobs recentes e de atualização devem manter precedência sobre backfill, com
reserva periódica de capacidade para backfill. A fila assume entrega pelo menos
uma vez; RAW, normalização e efeitos derivados precisam ser idempotentes.
Cursor de fluxo só é confirmado após todas as páginas válidas da janela estarem
duravelmente persistidas.

## Schema

O modelo ORM está em `app/core/models.py`; fila e operações estão em
`app/pncp/jobs.py`. A migration `0032_pncp_job_query_contract` adiciona
`parameters JSONB NOT NULL DEFAULT {}` e `config_version TEXT` à tabela
existente. Identidade, estados, índice parcial de deduplicação, índices de
agendamento/lease e auditoria são preservados pelas migrations anteriores.
