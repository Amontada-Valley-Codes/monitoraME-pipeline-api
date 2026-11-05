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

Para percorrer todas as páginas, use `pncp coletar-propostas-abertas`.
O comando agenda jobs persistentes e percorre o resultado completo do endpoint
`GET /v1/contratacoes/proposta`, gravando RAW e jobs de normalização. Repita
`--modalidade` para cada modalidade desejada; sem essa opção, consulta todas.
O worker persistente de coleta processa esse fluxo e seus retries. A CLI agenda
uma modalidade por vez e só agenda a próxima depois que a atual conclui (ou
esgota as tentativas). Ela fica em primeiro plano, imprime o estado e o erro
mais recente e termina com resumo. O timeout padrão é de 12 horas; use
`--timeout-minutos` para ajustar.

Use `--concorrencia` para as modalidades `4`, `5` e `16`; use `--pregao` para
`6`, `7` e `17`. Os dois seletores podem ser combinados, e a fila processa os
seis códigos em sequência. Para códigos específicos, repita `--modalidade`;
essa opção não pode ser combinada com os seletores de categoria.

No Compose local, para reconstruir a API e os workers necessários, iniciá-los e
acompanhar coleta e normalização em uma única execução:

```bash
docker compose -f compose.yaml up -d --build api pncp-worker-coleta pncp-worker-normalizacao && docker compose -f compose.yaml exec api pncp coletar-propostas-abertas --concorrencia --pregao
```

Para teste controlado, use `pncp coletar-amostra-abertas --limite 20`. Esse
comando limita o total persistido e as páginas lidas, não avança cursor e grava
RAW junto ao job de normalização. `--modalidade 6` restringe a consulta quando
necessário.

Esse fluxo não usa `--dias`: esse argumento pertence às consultas por data de
publicação/atualização e não representa o prazo de recebimento de propostas.
O operador pode executar `pncp status` no container Pipeline; o Docker CLI
fica no host da VPS, não dentro do shell `app@<container-id>`.
