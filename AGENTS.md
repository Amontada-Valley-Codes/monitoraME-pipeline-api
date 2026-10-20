# Guia para agentes e contribuidores

## Missão do repositório

Este repositório hospeda o pipeline analítico do MonitoraME. Além do módulo
retrospectivo de despesas do TCE-CE já existente, sua direção principal é
construir o **módulo de oportunidades do PNCP**: descobrir contratações,
recuperar atualizações, guardar o dado bruto, normalizar, enriquecer, classificar
e disponibilizar oportunidades confiáveis para os consumidores da plataforma.

O workflow PNCP fornecido é uma proposta técnica de referência. Ele orienta a
arquitetura abaixo, mas não substitui contratos vigentes: antes de implementar
uma rota do PNCP, confirme endpoint, paginação, limites de data e parâmetros no
Swagger oficial atual. Não use APIs autenticadas de publicação para consultar
oportunidades públicas.

O módulo não publica nem altera editais/propostas no PNCP. Ele sincroniza dados
públicos e produz dados internos para backend, matching, buscas e alertas.

## Arquitetura atual e arquitetura-alvo

```text
app/
  main.py                    # FastAPI, lifecycle, middleware e handlers globais
  api/                       # contratos HTTP do serviço Python quando aplicáveis
  core/                      # configuração, ORM, modelos e observabilidade
  pipeline/                  # módulo retrospectivo TCE e utilitários de pipeline
    ingestion/               # clientes externos e paginação
    cleaners/                # normalização de payloads por fonte
    persistence/             # acesso transacional e consultas persistidas
    enrichment/              # enriquecimentos de domínio
  pncp/                      # novo módulo de oportunidades (criar gradualmente)
    ingestion/               # cliente PNCP, paginação, rate limit e circuit breaker
    jobs.py                  # agendamento, reserva, lease, heartbeat e replay
    raw.py                   # armazenamento RAW, hashes e jobs derivados
    normalization.py         # contrato canônico e validação
    persistence.py           # upserts versionados, cursores e quarentena
    enrichment.py            # detalhes, itens, documentos e histórico sob demanda
    matching.py              # classificação e motivos de matching
tests/
alembic/main/                # migrations do banco principal
alembic/logs/                # migrations do banco de logs
docs/                        # regras, decisões e procedimentos operacionais
```

Adote responsabilidades unidirecionais:

`scheduler -> ingestion_jobs -> collector -> RAW -> normalize -> visão normalizada -> enrichment/matching -> consumidores`.

Coleta e processamento são independentes. Se o PNCP ficar indisponível, workers
devem continuar normalizando RAW já persistido; se a normalização falhar, o RAW
continua disponível para reprocessamento. Não coloque SQL, chamadas HTTP ou
regra analítica pesada em `api/endpoints/`.

O backend NestJS, frontend e alertas são consumidores de dados normalizados.
Não faça o frontend ou cada requisição de usuário consultar o PNCP diretamente.

## Convenções de código e nomenclatura

- Python 3.14; type hints modernos (`str | None`, `list[...]`) e
  `from __future__ import annotations` em módulos novos.
- `snake_case` para arquivos, funções, campos e colunas; `PascalCase` para
  classes; `UPPER_SNAKE_CASE` para constantes.
- O domínio usa português nos nomes internos: `oportunidade`, `contratacao`,
  `janela`, `tentativas`, `proxima_execucao_em`. Preserve termos já públicos
  ou persistidos; use `pncp_control_number` apenas quando o contrato cruzado
  justificar um nome em inglês.
- Prefira verbos explícitos: `buscar_*`, `normalizar_*`, `agendar_*`,
  `reivindicar_*`, `salvar_*`, `listar_*`, `atualizar_*`.
- Funções que alteram estado devem expor transação, idempotência e resultado.
- Use `logging.getLogger(__name__)` e `extra={...}` estruturado. Nunca registre
  segredos, credenciais, payloads completos ou dados pessoais desnecessários.

## Princípios obrigatórios do módulo PNCP

### Fila persistente e jobs

PostgreSQL é a fila persistente; **não introduza RabbitMQ**. Use uma tabela
`ingestion_jobs` no banco principal, com ao menos:

- identidade: `id`, `job_type`, `entity_type`, `source`, `scope_key`,
  `dedupe_key`;
- agendamento: `priority`, `status`, `window_start`, `window_end`, `page`,
  `next_run_at`, `attempts`, `max_attempts`;
- lease: `locked_by`, `lease_token`, `lease_until`, `heartbeat_at`;
- auditoria: `last_error`, `created_at`, `updated_at`.

Estados mínimos: `PENDING`, `RUNNING`, `RETRY_WAIT`, `SUCCEEDED`,
`FAILED` e `QUARANTINED`. Garanta unicidade do `dedupe_key` enquanto um job
equivalente estiver ativo. Jobs recentes e atualizações têm prioridade sobre
backfill, mas reserve capacidade limitada para impedir starvation histórico.

Workers reivindicam um job com transação curta e `FOR UPDATE SKIP LOCKED`; não
mantêm a transação aberta durante HTTP. Conclusão, heartbeat e falha conferem
`lease_token`, para que worker com lease expirado não conclua job retomado por
outro. Um recuperador devolve leases expirados à fila.

### Janelas, paginação e cursores

Mantenha cursor independente para cada fluxo e dimensão que muda o conjunto de
resultados. Os primeiros fluxos são `publicacao` e `atualizacao`, separados:

- descoberta: `GET /v1/contratacoes/publicacao`;
- atualizações: `GET /v1/contratacoes/atualizacao`;
- detalhe: `/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}` sob demanda ou por
  política de atualização;
- propostas, atas e contratos são complementares/fases posteriores e não devem
  atrasar a descoberta de oportunidades.

Use `last_confirmed_end` somente após persistir todas as páginas válidas da
janela. Faça overlap para capturar alterações tardias. Se a paginação não for
estável, reexecute a janela completa após falha e deduplique; checkpoint de
página só é seguro quando a API garante estabilidade. Página vazia válida não é
erro HTTP; indisponibilidade não autoriza avançar watermark.

### RAW, normalização e idempotência

O collector persiste RAW antes de qualquer interpretação. Cada registro RAW
contém `source="PNCP"`, `entity_type`, `external_id`, `collected_at`,
`source_updated_at`, `payload_hash` SHA-256 de payload canônico e `payload`
JSONB. Use identificadores próprios por recurso; para compras,
`numeroControlePNCP` é preferencial somente quando presente e válido.

Persistir RAW e agendar `normalize` ocorre na mesma transação. O histórico RAW
impede repetição por `(source, entity_type, external_id, payload_hash)`; a visão
normalizada atual tem unicidade por `(source, entity_type, external_id)`.

O upsert normalizado compara hash, versão e data de atualização antes de
substituir dados. Respostas antigas, incompletas ou empatadas sem critério de
precedência não podem sobrescrever versão mais recente. Eventos e alertas só
ocorrem depois de alteração relevante persistida e devem ser idempotentes.

Falha estrutural de normalização vai para quarentena com diagnóstico; não baixe
o registro novamente para tentar repará-lo. Preserve versões anteriores quando a
fonte falhar.

### Resiliência de fonte

Centralize no cliente PNCP: timeout, rate limiting configurável, concorrência
limitada, retry com backoff exponencial e jitter e circuit breaker coordenado.

- timeout, 408, 500, 502, 503 e 504: retry limitado;
- 429: respeitar `Retry-After` e reduzir pressão;
- 400, 401 e 403: falha de diagnóstico, sem retry cego;
- JSON inválido/truncado: retry limitado e amostra segura para diagnóstico;
- 404 de detalhe: caso classificável, sem repetição infinita.

O retry devolve o job para execução futura usando `next_run_at`, preserva erro
e prioridade. Tentativas esgotadas ficam em `FAILED` para replay autorizado.
Com circuit breaker aberto, reagende sem consultar a fonte.

### Enriquecimento, classificação e matching

Enriqueça detalhes, itens, documentos e histórico sob demanda, priorizando
oportunidades abertas, novas, alteradas ou acompanhadas. Uma contratação básica
válida já pode ser servida; enriquecimento não bloqueia sua publicação.

Classificação usa mapeamento explícito entre CNAE e categoria do objeto, sem
presumir CNAE no edital. Matching armazena resultado, versão da regra e
motivos/ausências de dados. IA de leitura ou resumo é opcional e não pode ser
dependência da ingestão básica.

## Módulo TCE existente: preservar invariantes

O módulo TCE-CE continua ativo e não deve ser quebrado pela introdução do PNCP.

- Valores monetários persistidos e agregados usam inteiros em centavos
  (`*_centavos`); não use `float` para moeda.
- Datas de API usam `YYYY-MM-DD` e competências usam `YYYY-MM`.
- Somente lotes TCE `PUBLICADO` entram em indicadores; falhas preservam o lote
  publicado anterior.
- Rotas declaradas somente leitura não podem disparar ingestão, cache ou fontes
  externas.

## Banco, ORM e migrations

Há dois contextos Alembic isolados: principal (`alembic/main`) e logs
(`alembic/logs`). O PNCP e seus jobs pertencem ao banco principal.

- Modele entidades e constraints primeiro e crie migration incremental no
  diretório correto. Nunca edite migration aplicada.
- Proteja no banco unicidades de RAW, visão normalizada e dedupe de jobs; crie
  índices para prioridade, `next_run_at`, leases e buscas de oportunidade.
- Use `orm.main_session()` e `orm.log_session()`; não crie engines ou sessões
  ad hoc em endpoints ou workers.

```bash
alembic upgrade head
alembic -n logs upgrade head
```

## Configuração, testes e documentação

Copie `.env.example` para `.env`; não versione ou imprima segredos. Variáveis
PNCP novas começam com `PNCP_`, têm valores seguros e são documentadas em
`.env.example` e `README.md`.

Ao adicionar scheduler e worker, separe-os em serviços/processos próprios no
Compose e use o mesmo PostgreSQL. Não use `docker compose down -v` sem querer
remover o volume local.

O projeto usa `unittest` e mocks; não dependa de PNCP, TCE ou banco real nos
testes unitários.

```bash
python -m unittest discover -s tests
python -m unittest tests.test_tce_despesas_pipeline
```

Para PNCP, cubra reserva concorrente, lease expirado, deduplicação por
overlap/retry, paginação completa, cursor que não avança em falha, RAW + job de
normalização na mesma transação, proteção contra regressão de versão,
quarentena, `Retry-After`, circuit breaker e idempotência de eventos. Atualize
`README.md` e `docs/` quando alterar contrato, esquema, operação ou regra.

## Checklist antes de concluir uma alteração

- A responsabilidade está na camada correta e não mistura HTTP, SQL e domínio?
- O código suporta entrega *at least once* sem duplicar dados ou alertas?
- Cursor só avança após a janela integral estar persistida?
- RAW é persistido antes da normalização e preservado em falhas?
- Jobs usam `SKIP LOCKED`, lease/token e recuperação segura?
- Rate limit, retry e circuit breaker protegem o PNCP sem mascarar erro final?
- Upserts não deixam resposta antiga sobrescrever versão atual?
- A mudança preserva invariantes e testes do módulo TCE?
- Há migration, índices, testes e documentação proporcionais ao impacto?
