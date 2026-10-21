# PNCP RAW e reprocessamento

`raw_pncp_records` e um historico imutavel de respostas JSON validas do PNCP.
Cada versao e identificada por `source`, `entity_type`, `external_id` e o
SHA-256 do JSON canonico. O payload nao e exposto pelas rotas HTTP nem deve ser
registrado em logs.

O repositorio `app.pncp.raw.salvar_raw_e_agendar_normalizacao` grava a versao
RAW e o respectivo `ingestion_jobs` de tipo `normalize` na mesma transacao do
banco principal. Uma repeticao por retry ou overlap recupera a mesma versao e o
mesmo job. Uma falha posterior de normalizacao nao remove o RAW.

## Retry de coleta

O cliente PNCP faz uma chamada por execução de job. Timeout, `408`, `429` e
`5xx` são classificados como transitórios e o worker persiste `RETRY_WAIT` com
backoff exponencial e *full jitter*; `400`, `401` e `403` vão diretamente para
`FAILED`. Em `429`, `Retry-After` prevalece sobre o jitter. `attempts`,
`next_run_at`, `last_error` e `last_error_context` permanecem no job para
auditoria e replay. O limite padrão é cinco tentativas totais, configurável por
variáveis `PNCP_*` descritas no `.env.example`.

## Proteção da fonte

`pncp_source_controls` mantém, no PostgreSQL e por fonte, o estado do circuito,
slots de concorrência, próximo horário permitido e motivo da última degradação.
Assim todos os workers compartilham o limite. O circuito abre após falhas
transitórias configuráveis; durante `OPEN`, jobs retornam para `RETRY_WAIT` sem
consumir tentativa nem chamar o PNCP. Após o cooldown, uma sonda `HALF_OPEN`
fecha o circuito apenas se for bem-sucedida. Respostas `429` e alta latência
reduzem a concorrência, que se recupera gradualmente após sucessos.

O contrato rejeita objetos que nao possam ser serializados como JSON (inclusive
NaN), antes de qualquer escrita. O coletor deve enviar apenas metadados seguros
do erro para a auditoria/quarentena futura, nunca o payload completo em logs.

Nao ha limpeza automatica nesta etapa: RAW e jobs sao retidos para replay e
auditoria. Uma politica de expiracao futura deve ser uma migration e um job
operacional separados, depois de definida a retencao legal e de backup.

## Fila de jobs

`ingestion_jobs` e a fila persistente do PostgreSQL; RabbitMQ nao e usado. A
reserva ocorre em uma transacao curta com `FOR UPDATE SKIP LOCKED`, prioridade
descendente e `lease_token`. O handler do worker executa apos o commit da
reserva. Conclusao, falha e heartbeat conferem o token, portanto um worker que
perdeu o lease nao altera um job reassumido. O recuperador devolve leases
expirados para `PENDING`.

Handlers longos devem chamar `renovar_lease(job_id, lease_token)` antes de o
lease vencer; essa chamada abre e fecha sua propria transacao curta.

O indice parcial de `dedupe_key` bloqueia apenas jobs equivalentes nos estados
ativos (`PENDING`, `RUNNING`, `RETRY_WAIT`). Workers reservam uma tentativa de
`backfill` a cada dez reservas por padrao, evitando starvation historico sem
tirar prioridade das atualizacoes recentes.

## Recuperação e replay

Leases expirados são devolvidos para `PENDING` pelo recuperador; o token antigo
é removido, portanto um worker que terminou tardiamente não consegue concluir
o job reassumido. `FAILED` e `QUARANTINED` são terminais recuperáveis: o replay
explícito por ID, fluxo ou janela recria a execução no próprio job, zera apenas
o contador operacional de tentativas e incrementa `replay_count`.

`ingestion_job_events` é um histórico append-only de início, falha, quarentena,
lease expirado, replay e sucesso. O replay ignora jobs `SUCCEEDED` e bloqueia a
reativação se houver outro `dedupe_key` equivalente ativo. Inconsistências de
normalização vão a `QUARANTINED`; falhas HTTP transitórias seguem `RETRY_WAIT`.

## Descoberta por publicação

O coletor usa `GET /v1/contratacoes/publicacao` com `dataInicial` e
`dataFinal` em `AAAAMMDD`, `codigoModalidadeContratacao`, `pagina` e
`tamanhoPagina` (limitado a 500). Uma janela e criada por modalidade. Cada
registro valido vai para RAW com `run_id=job:<id>:pagina:<n>`; portanto, uma
falha na pagina seguinte preserva o trabalho anterior e o replay e idempotente.

Uma resposta 5xx, timeout, JSON invalido ou pagina vazia antes de
`totalPaginas` encerra o job com falha; nunca e tratada como fim da janela. O
job somente chega a `SUCCEEDED` depois que todas as paginas foram persistidas,
logo esta etapa ainda nao confirma watermark algum.

## Enriquecimento sob demanda

Jobs `enrich` têm prioridade `40`, abaixo de descoberta e incremental. Detalhe,
itens e documentos possuem jobs e `dedupe_key` independentes por versão da
contratação; uma falha de anexo não apaga nem invalida a oportunidade básica.
O módulo usa somente GET de contratação (`/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}`
e os sufixos `/itens` e `/arquivos`), nunca PCA. `pncp_enriquecimentos` guarda
os recursos seletivamente e os reutiliza até o TTL configurado expirar.

## Oportunidades e matching

O contrato local para NestJS/frontend é produzido por `contrato_consumo` a
partir de `pncp_oportunidades` e, quando disponível, `pncp_enriquecimentos`.
Ele nunca consulta o PNCP em uma requisição de usuário. Eventos internos em
`pncp_eventos` têm chave idempotente por versão; matching em `pncp_matchings`
guarda versão da regra, motivos e ausências e só usa um de-para explícito entre
CNAE e categoria do objeto. O módulo não envia propostas ao PNCP.
