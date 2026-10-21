# Operação e diagnóstico do PNCP

`GET /health/pncp` consulta somente PostgreSQL. Verifique `jobs`, `fluxos` e
`totais`: `last_success_at`/`lag_seconds` revelam atraso; `raw_backlog` e
`enrichment_backlog` mostram processamento pendente; `leases_expirados` indica
interrupções de worker. A rota não consulta o PNCP.

Em falha transitória, confirme `last_error_context`, `next_run_at` e o estado
do circuito em `pncp_source_controls`; aguarde retry/reabertura gradual. Em
`FAILED` ou `QUARANTINED`, preserve RAW e use `scripts/replay_pncp_jobs.py`
com o menor seletor possível. Nunca avance cursores manualmente após janela
incompleta; reexecute a janela com overlap. Antes de operar na VPS, aplique
`alembic upgrade head`, mantenha scheduler/worker separados da API e acompanhe
logs estruturados por `job_id`, fluxo e janela.
