# Teste de carga do motor de indicadores

O benchmark exercita consultas simultâneas de vários municípios contra
`GET /pipeline/tce/analise-territorial`. Essa rota usa exclusivamente lotes TCE
`PUBLICADO` e cadastros persistidos no PostgreSQL; o teste não consulta TCE,
IBGE, OpenCNPJ ou PNCP.

O script distribui as requisições de forma determinística entre cada município
e cinco perfis:

- distribuições gerais, incluindo porte e elementos de despesa;
- recorte MEI;
- recorte ME;
- compras de fornecedores do município comprador;
- distribuição nos sete elementos de despesa monitorados.

A base analítica possui o CNAE principal do fornecedor, mas o contrato atual da
análise territorial não agrega por CNAE. Portanto, este benchmark não chama um
cenário de “distribuição por CNAE” inexistente. Essa dimensão deve ser adicionada
ao contrato analítico antes de integrar o teste.

## Pré-condições

1. A API e o PostgreSQL devem executar a mesma versão do código que contém a
   instrumentação deste teste.
2. Os municípios e o período escolhidos devem possuir competências publicadas.
3. A carga deve partir de uma máquina com latência de rede conhecida. Não misture
   resultados locais e de produção na mesma série histórica.
4. Não execute ingestão, migrations ou outros benchmarks durante a medição.

Valide individualmente o período antes da carga:

```bash
curl --fail --get http://127.0.0.1:8000/pipeline/tce/analise-territorial \
  --data-urlencode start_date=2025-01-01 \
  --data-urlencode end_date=2025-12-31 \
  --data-urlencode municipality_tce_code=010
```

## Execução principal

O modo `bypass` envia `X-Force-Refresh: 1`, mede o motor analítico e impede que
um `HIT` do cache esconda o custo de cálculo. O exemplo simula 200 consultas,
20 simultâneas, divididas entre os municípios `010` e `057`:

```bash
python scripts/carga_indicadores.py \
  --base-url http://127.0.0.1:8000 \
  --municipios 010,057 \
  --data-inicial 2025-01-01 \
  --data-final 2025-12-31 \
  --requisicoes 200 \
  --concorrencia 20 \
  --cache bypass \
  --p95-alvo-ms 1500
```

Por padrão, os resultados são gravados em:

- `.local/carga-indicadores/resultado.json`, para comparação automatizada;
- `.local/carga-indicadores/resultado.md`, para anexar à task.

Use `--output-json` e `--output-md` para escolher outros caminhos. O comando
retorna código `0` quando o p95 fica abaixo do alvo e a taxa de erro não supera
o limite; retorna `2` quando a meta operacional falha. O relatório é preservado
nos dois casos.

Para uma linha de base do cache aquecido, repita os mesmos parâmetros mudando
apenas o modo e o nome dos arquivos:

```bash
python scripts/carga_indicadores.py \
  --base-url http://127.0.0.1:8000 \
  --municipios 010,057 \
  --data-inicial 2025-01-01 \
  --data-final 2025-12-31 \
  --requisicoes 200 \
  --concorrencia 20 \
  --cache warm \
  --output-json .local/carga-indicadores/cache-aquecido.json \
  --output-md .local/carga-indicadores/cache-aquecido.md
```

Antes de iniciar o lote `warm`, o script confirma um `HIT` para cada cenário.
Se o cache não estiver persistindo respostas — por exemplo, por migration
ausente — o comando encerra com código `2` e explica a pré-condição que falhou.

Não compare diretamente `warm` com `bypass`: o primeiro mede a camada de
serving/cache e o segundo mede consulta, montagem da base e agregações.

## CPU e memória da API e do PostgreSQL

O cliente sempre mede seu próprio processo. `--monitorar-pid NOME=PID` mede
também CPU e RSS do PID informado e de todos os seus filhos. Para serviços
locais fora de contêiner:

```bash
python scripts/carga_indicadores.py \
  --municipios 010,057 \
  --data-inicial 2025-01-01 \
  --data-final 2025-12-31 \
  --monitorar-pid api=12345 \
  --monitorar-pid postgres=23456
```

Quando os serviços usam Docker, obtenha os PIDs no host com `docker inspect`
e execute o gerador no host. CPU pode ultrapassar 100% quando a árvore usa mais
de um núcleo. RSS inclui os processos filhos e não deve ser somado novamente
com métricas do contêiner.

## Tempo do banco

O cliente envia `X-Load-Test: 1`. A API então soma o tempo das execuções SQL no
banco principal e devolve `Server-Timing: db;dur=...` e
`X-Database-Queries`. A instrumentação fica inativa em requisições normais e
não depende da extensão `pg_stat_statements`.

O tempo do banco não inclui aquisição de conexão, transformação em pandas,
serialização JSON ou rede. A diferença entre tempo HTTP e tempo SQL ajuda a
separar contenção no PostgreSQL de custo no processo Python. Em um `HIT`, o
tempo reflete apenas a leitura do cache persistido; por isso o relatório também
conta `HIT`, `MISS` e `NAO_INFORMADO`.

## Métricas e critérios

O JSON e o Markdown documentam:

- média, p50, p95, p99 e máximo da resposta HTTP;
- throughput em requisições por segundo;
- taxa e tipos de erro;
- média, p95, total, cobertura e quantidade de consultas SQL;
- CPU média/máxima e RSS média/máxima dos processos monitorados;
- recortes por município e por cenário;
- distribuição de status HTTP e estado do cache.

Os critérios da task são verificados no relatório:

1. ao menos dois códigos municipais TCE distintos;
2. tempo de resposta sob carga registrado.

A meta operacional inicial é p95 menor que `1,5 s` e taxa de erro igual a zero.
Ela é configurável com `--p95-alvo-ms` e `--erro-maximo-percentual`, sem alterar
os dois critérios funcionais acima.

## Como registrar o resultado da task

Anexe o Markdown gerado e registre junto dele:

- commit/imagem da API;
- ambiente e quantidade de CPU/RAM;
- versão e capacidade do PostgreSQL;
- município/período e volume aproximado de despesas publicadas;
- modo de cache;
- concorrência, total de requisições e timeout;
- processos paralelos que não puderam ser interrompidos.

Não preencha números estimados. Se a infraestrutura alvo não estiver
disponível, mantenha o resultado como “não executado” até haver uma medição
reproduzível.

## Resultado local de referência — 2026-11-03

Esta execução valida o procedimento e os critérios da task; não substitui um
benchmark na infraestrutura de produção.

Configuração observada:

- base de código: revisão `a01a581` mais as alterações desta task;
- API local em um processo Uvicorn, pool SQLAlchemy de 10 conexões;
- PostgreSQL 16 local com dados previamente publicados;
- municípios TCE `004` e `010`, período solicitado de 2024-01-01 a
  2025-12-31;
- cobertura existente: 12 competências do município `004` em 2025 e seis do
  município `010` no primeiro semestre de 2024;
- 200 requisições, concorrência 20, timeout de 30 segundos e cache `bypass`;
- 10 requisições de aquecimento excluídas da medição;
- todos os 10 cenários município/filtro participaram em round-robin.

Resultado consolidado:

| Métrica | Resultado |
| --- | ---: |
| Requisições com sucesso | 200/200 |
| Taxa de erro | 0,000% |
| Tempo médio | 8.112,658 ms |
| p50 | 7.400,914 ms |
| p95 | 12.268,406 ms |
| p99 | 14.043,092 ms |
| Máximo | 15.752,683 ms |
| Throughput | 2,434 req/s |
| Tempo SQL médio por requisição | 2.080,627 ms |
| p95 do tempo SQL | 3.282,813 ms |
| Consultas SQL | 1.000, cinco por requisição |
| Cobertura da medição SQL | 100% |
| Cache | 200 `MISS` esperados no modo `bypass` |

Recorte por município:

| Município TCE | Requisições | Média | p95 | p99 |
| --- | ---: | ---: | ---: | ---: |
| `004` | 100 | 9.705,860 ms | 13.333,094 ms | 14.178,328 ms |
| `010` | 100 | 6.519,457 ms | 9.555,507 ms | 12.073,362 ms |

Recursos amostrados durante o lote:

| Processo | CPU média | CPU máxima | RSS média | RSS máxima |
| --- | ---: | ---: | ---: | ---: |
| Cliente | 12,292% | 48,000% | 39,049 MB | 39,473 MB |
| API e filhos | 105,592% | 122,000% | 575,308 MB | 596,137 MB |
| PostgreSQL e filhos | 13,032% | 117,800% | 375,481 MB | 383,535 MB |

Conclusão:

- critério “consulta simultânea de múltiplos municípios”: **atendido**;
- critério “tempo de resposta sob carga documentado”: **atendido**;
- meta p95 menor que 1,5 segundo: **não atendida**; o observado foi 12,268
  segundos;
- estabilidade funcional: 100% das respostas retornaram HTTP 200.

A API permaneceu próxima de um núcleo durante o lote. O SQL consumiu em média
2,081 segundos dos 8,113 segundos HTTP, e a aquisição de conexão não integra
esse tempo. Com concorrência 20 e pool limitado a 10 conexões, há espera fora
da medição SQL; montagem em pandas, agregações e serialização também ficam na
diferença. O município `004`, com maior cobertura, teve p95 39,5% superior ao
`010`. Esses dados indicam que o gargalo não está apenas no PostgreSQL.

Foi tentada também a linha de base `warm`, mas ela não constitui medição de
cache: todas as 200 respostas continuaram `MISS`. O diagnóstico mostrou que a
tabela `api_response_cache`, criada pela migration `0018_api_response_cache`,
não existe nesse banco local; a API registrou `ProgrammingError` nas leituras e
escritas e seguiu corretamente sem cache. O script agora interrompe o modo
`warm` quando o aquecimento não produz `HIT`, evitando rotular esse caso como
cache aquecido. A comparação válida deve ser repetida após aplicar as migrations
em um ambiente compatível.

Os artefatos completos desta execução estão em
`.local/carga-indicadores/resultado-bypass-local.json` e
`.local/carga-indicadores/resultado-bypass-local.md`; o diretório é ignorado pelo
Git porque contém resultados específicos da máquina.
