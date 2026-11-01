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
`tamanhoPagina` (limitado pelo cliente local a 50). Uma janela e criada por modalidade. Cada
registro valido vai para RAW com `run_id=job:<id>:pagina:<n>`; portanto, uma
falha na pagina seguinte preserva o trabalho anterior e o replay e idempotente.

Uma resposta 5xx, timeout, JSON invalido ou pagina vazia antes de
`totalPaginas` encerra o job com falha; nunca e tratada como fim da janela. O
job somente chega a `SUCCEEDED` depois que todas as paginas foram persistidas,
logo esta etapa ainda nao confirma watermark algum.

O comando `coletar-publicacao --ano AAAA` agenda um backfill persistente desde
1º de janeiro até o momento da execução, em janelas semanais por modalidade.
Cada janela pode ser acompanhada em `GET /pncp/cobertura/{ano}`; a rota informa
contagens por estado e o status individual de cada janela/modalidade, além da
fila de normalização associada às páginas coletadas. Os serviços Compose
`pncp-worker-coleta` e `pncp-worker-normalizacao` processam jobs e retries
continuamente. A API de consumo filtra por `ano_publicacao` (`dataPublicacaoPncp`)
ou `ano_compra` (`anoCompra`) em parâmetros separados.

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

### Taxonomia CNAE-categoria

`pncp_taxonomia_categorias` e `pncp_taxonomia_cnaes` formam o vocabulário
controlado. Cada revisão em `pncp_mapa_revisoes` possui snapshot completo de
relações em `pncp_mapa_cnae_categoria`; versões publicadas não são editadas.
Para revisar, crie uma revisão `RASCUNHO`, copie as relações da versão ativa,
use `copiar_relacoes`, altere/adiciona relações com
`app.pncp.taxonomy.adicionar_relacao` / `atualizar_relacao` e ative com
`ativar_revisao` na mesma transação. Relações removidas ficam no snapshot com
`ativo=false`. Cada associação exige CNAE de sete dígitos existente, categoria
existente, tipo, confiança, evidência e responsável; condições são obrigatórias
para tipo `CONDICIONAL`.

A migration `0026_pncp_cnae_taxonomy` cria `cnae-objeto-v1`, com quatro
categorias e oito CNAEs observados no fixture local. A evidência inicial é a
correspondência semântica das descrições CNAE-Subclasses 2.3 IBGE/CONCLA com os
rótulos e descrições da taxonomia. Os pesos são curadoria inicial, não validação
humana ampla nem prova de habilitação legal; ampliar ou ativar novos mapas exige
revisão responsável documentada.

`GET /pncp/taxonomia/cnae-categoria` retorna a revisão ativa; `?versao=...`
retorna snapshot histórico reproduzível. As métricas incluem cobertura de
CNAEs, categorias sem mapeamento, CNAEs sem mapeamento, relações inativas e
cardinalidades N:N. Matching novo deve obter a versão somente por
`mapa_ativo_para_matching`; resultados incluem a versão do mapa em
`rule_version`. Categoria confirmada pela empresa tem precedência e substitui
inferência por CNAE naquela avaliação. Esse catálogo e seus serviços pertencem
ao domínio PNCP e não são importados pelos indicadores do Analytics TCE.

O cadastro inicial da migration contém apenas oito códigos para prova do
modelo. Para carregar o universo oficial, execute no PowerShell:
`.venv\Scripts\python.exe scripts\sincronizar_catalogo_cnae_ibge.py`. O script
usa `GET https://servicodados.ibge.gov.br/api/v2/cnae/subclasses`, valida
subclasses de sete dígitos e faz upsert idempotente em
`pncp_taxonomia_cnaes`; ele não cria associações automaticamente. Depois da
carga, `GET /pncp/taxonomia/cnaes/busca?q=software` pesquisa código e descrição
no catálogo local e indica relações existentes na revisão ativa. Assim, códigos
IBGE sem categoria aparecem em `cnaes_sem_mapeamento` até serem avaliados e
incluídos numa nova revisão do de-para.

#### Revisão pelo CLI

No container, use `pncp`; no checkout Windows, prefixe cada comando com
`.venv\Scripts\python.exe scripts\pncp_operacao.py`:

```text
pncp taxonomia buscar --consulta software
pncp taxonomia status
pncp taxonomia revisar --versao cnae-objeto-v2 --responsavel "Nome do revisor" --evidencia "Revisão baseada na CNAE-Subclasses 2.3 do IBGE/CONCLA"
pncp taxonomia mapear --versao cnae-objeto-v2 --cnae 0000000 --categoria software --tipo DIRETA --confianca 0.85 --responsavel "Nome do revisor" --evidencia "Descreva a evidência semântica observada"
pncp taxonomia status --versao cnae-objeto-v2
pncp taxonomia ativar --versao cnae-objeto-v2 --responsavel "Nome do aprovador"
pncp taxonomia status
```

Substitua `0000000` pelo código de sete dígitos retornado pela busca e escolha
a categoria/tipo pela evidência; o exemplo não afirma que esse código pertença
a `software`. `revisar` cria um rascunho e copia o mapa ativo. Use `mapear` para
uma nova combinação, `atualizar` para ajustar uma combinação copiada, ou
`desativar` para retirar uma associação sem apagar o histórico. Ative apenas
depois de conferir o snapshot e as métricas do rascunho. Cada ação grava no
banco principal; a ativação substitui a versão ativa para cálculos futuros,
mantendo as anteriores consultáveis por `?versao=...`.
