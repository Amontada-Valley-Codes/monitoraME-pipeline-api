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

## Editais com propostas abertas

Para teste controlado, use `pncp coletar-amostra-abertas --limite 20`.
Esse comando busca editais ainda recebendo propostas pela consulta `GET
/v1/contratacoes/proposta`, limita o total persistido e as páginas lidas, não
avança cursor e grava RAW junto ao job de normalização. O worker de
normalização processa esse job automaticamente. `--modalidade 6` restringe a
consulta quando necessário; por padrão, são consultadas todas as modalidades.

O comando sem amostra, `pncp coletar-propostas-abertas`, permanece disponível
para operação deliberada que precise percorrer todas as páginas. Ele não deve
ser usado no teste inicial limitado.

Esse fluxo não usa `--dias`: esse argumento pertence às consultas por data de
publicação/atualização e não representa o prazo de recebimento de propostas.
O operador pode executar `pncp status` no container Pipeline; o Docker CLI
fica no host da VPS, não dentro do shell `app@<container-id>`.
