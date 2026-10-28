# monitoraME Pipeline API

API em FastAPI para o módulo analítico retrospectivo do MonitoraME. O serviço coleta despesas empenhadas do TCE-CE, trata anulações, enriquece fornecedores com dados cadastrais e calcula indicadores de participação por porte empresarial, natureza da despesa e origem geográfica.

O módulo usa TCE-CE, IBGE e OpenCNPJ. A base do módulo de oportunidades PNCP
também começou pelo histórico RAW reprocessável; a coleta e a normalização ainda
serão entregues em etapas próprias.

## Funcionalidades

- Coleta mensal de notas de empenho e suas anulações no TCE-CE.
- Paginação da API externa e validação completa da competência antes da publicação.
- Persistência versionada por município e competência, com substituição atômica do lote publicado.
- Valores monetários armazenados e agregados como inteiros em centavos.
- Identificação e enriquecimento cadastral de fornecedores por CNPJ.
- Classificação pelas sete naturezas de despesa monitoradas.
- Indicadores por porte, natureza, origem geográfica e fornecedor.
- Registro separado das execuções de ingestão.

## Fontes de dados

| Fonte | Uso |
| --- | --- |
| TCE-CE | Empenhos, anulações, contratos, contratados e municípios. |
| OpenCNPJ | Porte, município de sede e dados cadastrais dos fornecedores. |
| IBGE | Catálogo e validação de municípios. |

## Estrutura principal

```text
app/
├── api/endpoints/       # rotas HTTP e tradução de erros
├── core/                # configuração, ORM e conexões
└── pipeline/
    ├── ingestion/       # clientes TCE-CE, IBGE e OpenCNPJ
    ├── cleaners/        # normalização por fonte
    ├── persistence/     # lotes versionados de empenhos e anulações
    ├── analitico.py     # base canônica de análise
    ├── analisys.py      # orquestração dos fluxos da API
    ├── kpis.py          # agregações e indicadores
    └── tce_despesas.py  # ingestão mensal do TCE-CE
```

## Requisitos

- Python 3.14
- Docker com Docker Compose
- Acesso às APIs do TCE-CE, IBGE e OpenCNPJ
- Dependências de `requirements.txt`

## Configuração

Crie o arquivo local de ambiente:

```bash
cp .env.example .env
```

Variáveis usadas pela aplicação:

| Variável | Obrigatória | Uso |
| --- | --- | --- |
| `DATABASE_URL` | Sim | Banco principal com caches e lotes analíticos. |
| `LOG_DATABASE_URL` | Sim | Banco da tabela `logs_ingestao`. |
| `LOG_LEVEL` | Não | Nível dos logs operacionais (`DEBUG`, `INFO`, `WARNING`, `ERROR`); padrão `INFO`. |
| `DATABASE_SSLMODE` | Não | Modo SSL do banco principal; use `disable` no PostgreSQL local. |
| `LOG_DATABASE_SSLMODE` | Não | Modo SSL do banco de logs; use `disable` localmente. |
| `TCE_CE_BASE_URL` | Sim | API de dados abertos do TCE-CE. |
| `IBGE_LOCALIDADES_BASE_URL` | Sim | API de localidades do IBGE. |
| `OPENCNPJ_BASE_URL` | Sim | API cadastral OpenCNPJ. |
| `PNCP_BASE_URL` | Não | Base pública de consultas do PNCP; padrão oficial. |
| `PNCP_CONNECT_TIMEOUT_SECONDS` | Não | Timeout de conexão PNCP; padrão `10`. |
| `PNCP_READ_TIMEOUT_SECONDS` | Não | Timeout de leitura PNCP; padrão `30`. |
| `PNCP_MAX_ATTEMPTS` | Não | Tentativas totais de um job PNCP, incluindo a primeira; padrão `5`. |
| `PNCP_RETRY_INITIAL_SECONDS` | Não | Base do backoff exponencial com full jitter; padrão `2`. |
| `PNCP_RETRY_MAX_SECONDS` | Não | Teto do backoff com full jitter; padrão `60`. |
| `PNCP_REQUEST_INTERVAL_SECONDS` | Não | Espaçamento mínimo entre chamadas do cliente PNCP; padrão `2`. |
| `PNCP_RATE_LIMIT_REQUESTS_PER_SECOND` | Não | Limite global compartilhado entre workers; padrão `2`. |
| `PNCP_CONCURRENCY_INITIAL` / `PNCP_CONCURRENCY_MAX` | Não | Concorrência inicial e teto adaptativo; padrões `2` e `5`. |
| `PNCP_CIRCUIT_FAILURE_THRESHOLD` / `PNCP_CIRCUIT_COOLDOWN_SECONDS` | Não | Falhas transitórias até abrir o circuito e duração do cooldown; padrões `5` e `60`. |
| `UF_PADRAO` | Sim | UF compradora usada pela análise. |
| `CODIGO_MUNICIPIO_TCE_PADRAO` | Sim | Código interno padrão do município no TCE-CE. |

## Banco de dados local

O `compose.yaml` inicia um database PostgreSQL em `localhost:5432`:

- `monitorame`: tabelas principais, caches, lotes analíticos e logs.

Os dois ambientes Alembic continuam isolados pelas tabelas de versão
`alembic_version_main` e `alembic_version_logs`.

As credenciais e a porta podem ser alteradas pelas variáveis `POSTGRES_*` do
`.env`. Se forem alteradas, ajuste também `DATABASE_URL` e
`LOG_DATABASE_URL`.

Inicie a aplicação completa e confira seu estado:

```bash
docker compose up -d --build
docker compose ps
```

O Compose constrói a imagem da API, espera o PostgreSQL ficar saudável, aplica
as duas trilhas de migrations pelo serviço `migrate` e somente então inicia o
serviço `api`. A documentação OpenAPI fica em `http://127.0.0.1:8000/docs`.

O PostgreSQL usa o volume nomeado `postgres_data`. Os dados persistem ao reiniciar ou recriar o container. Preserve os backups; `docker compose down -v` remove os volumes.

### Deploy na VPS com Traefik

Use `compose.production.yaml` para publicar a API em `https://api.monitorame.tech`
atrás do Traefik já instalado na VPS. O PostgreSQL permanece apenas na rede
interna do Compose e seus dados ficam no volume nomeado
`monitorame_pipeline_api_postgres_data`.

Crie um `.env` privado a partir de `.env.example`, gere uma senha forte para
`POSTGRES_PASSWORD` (por exemplo, `openssl rand -hex 32`) e configure também
`POSTGRES_USER` e `POSTGRES_DB`. Não reutilize os valores de desenvolvimento do
arquivo de exemplo nem versione o `.env`. O DNS do domínio deve apontar para a
VPS e as portas 80 e 443 devem estar liberadas para o Traefik.

Na pasta do repositório, aplique as migrations e inicie os serviços:

```bash
docker compose -f compose.production.yaml up -d --build
docker compose -f compose.production.yaml ps
```

O serviço `api` só inicia após o serviço `migrate` concluir com sucesso. Para
atualizar o código, faça backup do banco, atualize a imagem e execute novamente
o comando `up -d --build`. O volume do PostgreSQL permanece associado ao nome
estável acima; não use `docker compose down -v` em produção.

### CI/CD e aprovação de produção

O workflow `Production readiness` roda em cada push e pull request. Ele compila
os módulos Python, aplica as duas trilhas de migrations em um PostgreSQL
temporário, executa `unittest`, valida `compose.production.yaml` e constrói a
imagem Docker. O status **Production readiness** só fica verde quando todas
essas etapas passam.

O deploy não é automático em pushes. Para promover uma versão, execute o
workflow em **Actions → Production readiness → Run workflow**, no branch padrão
do repositório, e marque `deploy_production`. O job de deploy depende do status
verde e usa o ambiente `production`; configure esse ambiente no GitHub com
branch permitido restrito ao branch padrão e aprovação obrigatória de um
responsável. O deploy envia apenas os arquivos versionados do commit validado,
preserva o `.env` da VPS, reconstrói a imagem, aplica migrations e verifica
`https://api.monitorame.tech/health`.

Configure estas variáveis no ambiente `production` do GitHub:

| Nome | Valor |
| --- | --- |
| `VPS_HOST` | IP ou hostname SSH da VPS. |
| `VPS_USER` | Usuário autorizado a gravar em `/docker/monitorame-pipeline-api` e executar Docker Compose. |
| `VPS_HOST_KEY_FINGERPRINT` | Impressão digital ED25519 da VPS, verificada pelo administrador. |

Adicione `VPS_SSH_PRIVATE_KEY` como segredo do ambiente. A chave pública
correspondente precisa estar autorizada na VPS. Use uma chave dedicada ao
workflow e mantenha a aprovação do ambiente habilitada. A verificação da
impressão digital interrompe o deploy se a identidade SSH da VPS mudar.

Nesta VPS, os valores verificados são `VPS_HOST=179.199.157.134`,
`VPS_USER=root` e
`VPS_HOST_KEY_FINGERPRINT=SHA256:tC/f5ZMnRdIqlEV1r4W2ifMbmO08oZs9Mxst6EH3cC8`.
Se a VPS ou sua chave de host mudar, atualize as variáveis somente depois de
verificar a nova impressão digital pelo console Hostinger.

O workflow envia o checkout do GitHub Actions para a VPS; ele não usa a deploy
key somente leitura VPS → GitHub. Para o CD, gere outro par de chaves dedicado
GitHub Actions → VPS, cadastre a pública na VPS e guarde a privada em
`VPS_SSH_PRIVATE_KEY`. Nunca versione nem envie a chave privada pelo chat.

Para impedir merge de alterações que falham no CI, crie uma ruleset para o
branch padrão em **Settings → Rules → Rulesets** e marque `Production readiness`
como status check obrigatório. O workflow não adiciona essa proteção às
configurações do GitHub automaticamente.

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
docker compose up -d postgres
alembic upgrade head
alembic -n logs upgrade head
uvicorn app.main:app --reload
```

No Windows com Git Bash, ative o ambiente com `source .venv/Scripts/activate`.

A documentação OpenAPI fica em `http://127.0.0.1:8000/docs`.

O Compose prepara uma instância nova; ele não transfere os dados existentes
do Supabase. As migrations criam apenas a estrutura. Uma eventual migração de
dados deve ser feita separadamente, depois de definir quais tabelas e
históricos precisam ser preservados.

## Imagem da aplicação

Para construir apenas a imagem:

```bash
docker build -t monitorame-pipeline-api:local .
```

A imagem executa a API por padrão e aceita o comando `migrate` para aplicar os
dois ambientes Alembic:

```bash
docker run --rm --env-file .env monitorame-pipeline-api:local migrate
docker run --rm --env-file .env -p 8000:8000 monitorame-pipeline-api:local
```

Para publicar em um registry, defina um nome versionado, construa e envie:

```bash
docker build -t registry.example.com/monitorame/pipeline-api:1.0.0 .
docker push registry.example.com/monitorame/pipeline-api:1.0.0
```

Em produção, configure `DATABASE_URL`, `LOG_DATABASE_URL` e os modos SSL para
um PostgreSQL persistente externo. O serviço `postgres` deste Compose conserva os dados em volume nomeado. Configure backups e retenção adequados ao ambiente.

## Rotas do pipeline

- `GET /pipeline/tce/contratos`: consulta e enriquece contratos do TCE-CE.
- `GET /pipeline/tce/kpis/portes-por-mes`: calcula a participação mensal por porte.
- `GET /pipeline/tce/overview`: calcula o overview exclusivo de ME e MEI.
- `GET /pipeline/tce/overview/municipal`: calcula o overview de um município por período, código TCE e portes opcionais.
- `POST /pipeline/tce/comparisons`: compara dois overviews usando somente dados armazenados.
- `GET /pipeline/tce/empenhos`: lista e pagina empenhos publicados armazenados.
- `GET /pipeline/tce/empenhos/{chave_empenho}`: detalha um empenho e suas anulações.
- `GET /pipeline/tce/fornecedores`: lista fornecedores e valores no recorte.
- `GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}`: retorna cadastro, indicadores e gráficos do fornecedor.
- `GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}/empenhos`: pagina as notas consideradas do fornecedor.
- `GET /pipeline/tce/analitico/indicadores`: atualiza as competências e calcula os indicadores analíticos principais sobre empenhos líquidos.
- `GET /pipeline/tce/analise-territorial`: calcula retenção local, destino dos recursos, concentração e rankings usando somente dados publicados no banco local.

As rotas analíticas recebem datas no formato `YYYY-MM-DD`. Uma competência só entra no cálculo depois de ser coletada, validada e publicada. Falhas totais de atualização retornam `503`; meses que falharem durante uma consulta parcialmente bem-sucedida são sinalizados nos metadados.

### Ingestão manual de empenhos e anulações

O comando `tce-ingestir` coleta as notas de empenho e suas anulações para uma
competência e um município, normaliza os registros e publica o lote validado no
banco principal. Execute-o pelo serviço `api` do Compose:

```bash
docker compose run --rm api tce-ingestir --competencia 2025-01 --codigo-municipio 010
```

Para coletar o ano inteiro em todos os 184 municípios do Ceará, use `--ano`
com quatro dígitos:

```bash
docker compose run --rm api tce-ingestir --ano 2025 --todos-municipios
```

A opção de todos os municípios valida o catálogo do TCE contra o IBGE antes de
iniciar e executa no máximo duas coletas em paralelo. O comando imprime o
resultado de cada município e um resumo final; retorna `0` se todos forem
publicados em todas as competências, `2` se houver falhas ou rejeições e `1` se
o catálogo não passar na validação. As competências são processadas em ordem,
uma por vez; para o ano corrente, a coleta vai de janeiro até o mês atual.

Para coletar o ano de apenas um município, informe o código ou deixe o padrão
configurado (`010` no `.env.example`):

```bash
docker compose run --rm api tce-ingestir --ano 2025 --codigo-municipio 010
```

`--ano` usa `YYYY`; para uma única competência ainda é possível usar
`--competencia YYYY-MM`. O comando imprime resumos JSON com status, identificador
do lote e quantidades coletadas. Código de saída `0` indica publicação completa;
`1` indica falha de catálogo, coleta ou persistência; `2` indica falha ou rejeição
de um ou mais lotes.

### Enriquecimento cadastral de empenhos publicados

Para enriquecer os fornecedores de empenhos já publicados em 2025 sem repetir
a coleta no TCE, execute:

```bash
docker compose run --rm api tce-enriquecer --ano 2025
```

Por padrão, percorre todos os municípios da UF configurada em `UF_PADRAO`; use
`--codigo-municipio 010` para limitar a um município. O comando busca CNPJs
distintos dos lotes publicados, usa o cache cadastral existente e consulta o
OpenCNPJ somente em cache miss ou expiração. O intervalo padrão entre consultas
é 0,3 segundo e pode ser alterado com `--intervalo-segundos`. O resumo informa
quantos fornecedores tiveram porte identificado. `2` indica CNPJs sem porte
cadastral disponível; `1` indica falha operacional.

Para executar a ingestão seguida do enriquecimento, use:

```bash
docker compose run --rm api tce-ingestir-enriquecer --ano 2025
```

Esse comando processa todos os municípios por padrão e aceita
`--codigo-municipio 010` para limitar a um município. Se a ingestão terminar
com falhas parciais, o enriquecimento ainda será executado sobre os lotes
publicados, e o código final indicará a falha parcial. Reconstrua a imagem com
`docker compose build api` antes de usar os novos comandos.

### Overview municipal por porte

Por padrão, `GET /pipeline/tce/overview/municipal` considera e discrimina MEI,
ME, EPP, demais portes e fornecedores sem porte identificado. O parâmetro
repetível `company_sizes` restringe todo o cálculo; para reproduzir o recorte
anterior de microempresas, envie `ME` e `MEI`:

```http
GET /pipeline/tce/overview/municipal?data_inicial=2025-01-01&data_final=2025-12-31&codigo_municipio_tce=010&company_sizes=ME&company_sizes=MEI
```

Os valores aceitos são `ME`, `MEI`, `EPP`, `OTHER` e `UNKNOWN`. No retorno,
`OTHER` é apresentado como `DEMAIS` e `UNKNOWN` como `NAO_IDENTIFICADO`.

### Análise territorial

`GET /pipeline/tce/analise-territorial` é somente leitura e não consulta fontes
externas. A rota recebe `start_date`, `end_date`, `municipality_tce_code`, `uf`,
listas opcionais de `company_sizes`, `supplier_origins` e
`expense_element_codes`, além de `ranking_limit`. O retorno inclui valores
empenhado, anulado e líquido, retenção local, evasão municipal, retenção no
Ceará, cobertura cadastral e geográfica, destinos dos recursos, portes,
elementos, evolução mensal, concentração HHI e ranking de fornecedores.

```http
GET /pipeline/tce/analise-territorial?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057&ranking_limit=10
```

Todos os valores monetários são inteiros em centavos e os percentuais usam a
escala de `0` a `100`. Competências ausentes aparecem explicitamente na
cobertura e não são convertidas em meses com valor zero.

Competências publicadas são reutilizadas enquanto estiverem dentro da política
de atualização: uma hora para o mês atual, 24 horas para os três meses
anteriores e sete dias para os meses mais antigos. Quando uma publicação
vencida não pode ser atualizada, sua última versão continua no cálculo e a
competência aparece em `competencias_desatualizadas`. Competências sem qualquer
publicação continuam aparecendo em `competencias_ausentes`.

### Overview de ME e MEI

`GET /pipeline/tce/overview` recebe `data_inicial`, `data_final` e aceita
`codigo_municipio` opcional. Sem o código, consolida todo o Ceará usando apenas
os lotes e cadastros já publicados no banco local. Com o código, mantém o fluxo
municipal, atualizando as competências e o cadastro dos fornecedores antes do
cálculo. A resposta restringe o detalhamento geográfico a empenhos líquidos de
ME e MEI. Essa restrição não altera os demais indicadores da API.

O total de compras consideradas corresponde aos portes identificados `ME +
MEI + EPP + DEMAIS`. A participação é `(ME + MEI) / compras consideradas`;
portes não identificados ficam fora da razão. A evolução mensal compara esses
dois valores. Os indicadores geográficos e o destino dos recursos usam
exclusivamente ME e MEI. Origem não identificada permanece no denominador
geográfico, sem ser tratada como recurso local ou externo.

O KPI `total_compras_ME_centavos` representa a soma líquida de ME e MEI no
período completo.

`participacao_por_porte_empresarial` apresenta valor e percentual de `ME`,
`MEI`, `OUTROS_PORTES` (`EPP + DEMAIS`) e `NAO_IDENTIFICADO`. Nesse indicador,
o denominador inclui as quatro categorias para que seus percentuais totalizem
`100` quando houver valor no período.

`elementos_despesa` apresenta os sete elementos monitorados no período total,
com código de dois dígitos, nome e valor líquido em centavos. O cálculo usa
somente empenhos de ME e MEI e mantém elementos sem movimento com valor zero.

Valores monetários são inteiros em centavos e percentuais variam de `0` a
`100`. Um período publicado sem compras de porte identificado retorna `200`,
KPIs zerados, evolução vazia e os quatro destinos com valor e percentual zero.

### Comparação de overviews

`POST /pipeline/tce/comparisons` recebe dois lados independentes e permite
comparar períodos (`PERIODS`), municípios (`MUNICIPALITIES`) ou ambos
(`MIXED`). Os municípios são informados pelo código IBGE e resolvidos para o
código interno do TCE usando o mapeamento armazenado.

A rota é estritamente somente leitura. Ela não atualiza competências, não
renova o cadastro de fornecedores e não consulta TCE-CE, IBGE ou OpenCNPJ.
Autenticação e assinatura são responsabilidades do proxy que publica a API.

Exemplo de comparação mista:

```json
{
  "comparison_mode": "MIXED",
  "left": {
    "label": "Acaraú 2025",
    "filters": {
      "uf": "CE",
      "municipality_ibge_code": "2300200",
      "start_date": "2025-01-01",
      "end_date": "2025-12-31",
      "company_sizes": ["ME", "EPP", "MEI", "OTHER", "UNKNOWN"],
      "supplier_origins": [],
      "expense_element_codes": []
    }
  },
  "right": {
    "label": "Sobral 2024",
    "filters": {
      "uf": "CE",
      "municipality_ibge_code": "2312908",
      "start_date": "2024-01-01",
      "end_date": "2024-12-31",
      "company_sizes": ["ME", "EPP", "MEI", "OTHER", "UNKNOWN"],
      "supplier_origins": [],
      "expense_element_codes": []
    }
  }
}
```

Listas vazias significam que a dimensão não restringe a base. Os portes
aceitos são `ME`, `MEI`, `EPP`, `OTHER` e `UNKNOWN`; as origens aceitas são
`NO_MUNICIPIO_COMPRADOR`, `EM_OUTRO_MUNICIPIO`, `FORA_DO_CEARA` e
`ORIGEM_NAO_IDENTIFICADA`. Os elementos aceitos são `30`, `32`, `35`, `39`,
`40`, `51` e `52`.

`left.overview` e `right.overview` preservam todos os blocos do overview. O
objeto `sections` acrescenta maior, menor e diferença absoluta para cada KPI,
porte, elemento, destino e posição da série mensal. Competências publicadas
sem movimento são válidas e aparecem com zero na seção mensal; competências
sem publicação aparecem em `competencias_ausentes` e não são convertidas em
zero.

Erros da rota usam um envelope próprio e propagam `X-Request-ID` quando o
cabeçalho é enviado pelo proxy:

```json
{
  "error": {
    "code": "INVALID_DATE_RANGE",
    "message": "start_date deve ser menor ou igual a end_date.",
    "details": [
      {
        "field": "left.filters.start_date",
        "reason": "after_end_date",
        "value": "2026-12-31"
      }
    ],
    "request_id": "req_01K6ME9W3H"
  }
}
```

### Consulta de empenhos armazenados

`GET /pipeline/tce/empenhos` é uma rota somente leitura: não executa ingestão
nem acessa TCE-CE, IBGE ou OpenCNPJ. Ela considera apenas empenhos dos sete
elementos monitorados pertencentes a lotes com status `PUBLICADO`.

Os parâmetros obrigatórios são `start_date` e `end_date`. A consulta aceita
também `uf`, `municipality_tce_code`, listas repetidas de `company_sizes`,
`supplier_origins` e `expense_element_codes`, além de `search`, `page`,
`page_size`, `sort_by` e `sort_order`. Filtros de dimensões diferentes usam
`AND`; valores repetidos na mesma dimensão usam `OR`. Sem filtro de elemento,
os sete elementos monitorados são incluídos.

Exemplo:

```http
GET /pipeline/tce/empenhos?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057&company_sizes=ME&company_sizes=MEI&expense_element_codes=39&page=1&page_size=50&sort_by=net_value&sort_order=desc
```

Cada item retorna a `chave_empenho` canônica, um `display_id` no formato
`EMP-AAAA-NUMERO`, data, município comprador, fornecedor, porte, origem,
elemento de despesa e valores bruto, anulado e líquido em centavos. A busca
aceita chave, número, `display_id`, documento ou nome do fornecedor.

`GET /pipeline/tce/empenhos/{chave_empenho}` recebe a chave com caracteres
reservados codificados na URL (por exemplo, `|` como `%7C`). O detalhe inclui
classificação orçamentária, dados cadastrais armazenados do fornecedor,
referências de contrato e licitação, lista das anulações, valores consolidados
e metadados do lote publicado. O payload bruto das fontes não é exposto.

As duas rotas usam o mesmo envelope padronizado de erro e propagam
`X-Request-ID`. Autenticação e assinatura permanecem sob responsabilidade do
proxy que publica a API.

### Consulta de fornecedores armazenados

As três rotas de fornecedores são somente leitura e consideram exclusivamente
empenhos dos lotes `PUBLICADO`. Elas recebem o mesmo recorte por período,
município TCE, porte empresarial, origem e elemento de despesa usado pela
consulta de empenhos.

`GET /pipeline/tce/fornecedores` agrupa as notas por tipo e documento do
fornecedor. A resposta apresenta nome, porte, disponibilidade cadastral,
origens no recorte, valores bruto, anulado e considerado, quantidade de notas,
municípios e datas da primeira e última nota. Fornecedores sem cadastro no
OpenCNPJ continuam na listagem com `cadastro_status=NAO_DISPONIVEL`.

```http
GET /pipeline/tce/fornecedores?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057&page=1&page_size=50&sort_by=net_value&sort_order=desc
```

`GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}` aceita
`CNPJ` ou `CPF` no tipo e retorna o cadastro armazenado, totais do recorte,
evolução mensal, distribuição pelos sete elementos e participação no valor
total filtrado. O cadastro é a observação mais recente disponível e não uma
fotografia histórica do exercício.

```http
GET /pipeline/tce/fornecedores/CNPJ/05537536000164?start_date=2025-01-01&end_date=2025-12-31&municipality_tce_code=057
```

`GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}/empenhos`
retorna as notas em uma consulta separada e paginada, preservando o mesmo
formato dos itens de `/pipeline/tce/empenhos`. Assim, navegar ou ordenar a
tabela não recalcula os gráficos do detalhe.

Nenhuma dessas rotas atualiza o cache cadastral ou consulta TCE-CE, IBGE ou
OpenCNPJ. O payload bruto do cadastro não é exposto.

## Persistência

O banco principal mantém:

- `ibge_municipios`, `tce_municipios`, `fornecedores_me` e
  `fornecedores_cache`, usados como caches;
- `tce_despesa_ingestion_runs`, com o estado de cada carga mensal;
- `tce_empenhos`, com as notas de empenho versionadas por lote;
- `tce_anulacoes_empenhos`, com as anulações vinculadas aos empenhos.
- `raw_pncp_records`, com versões originais JSONB do PNCP, e `ingestion_jobs`,
  cuja criação do job `normalize` é atômica com a gravação RAW.

A resolução do código interno de município do TCE consulta primeiro
`tce_municipios`, associada a `ibge_municipios`. Quando o código ainda não
existe no cache, a aplicação consulta a lista do TCE uma vez e persiste os
mapeamentos em lote para as consultas seguintes.

O cache cadastral completo de fornecedores mantém os dados normalizados e o
payload bruto do OpenCNPJ. Registros encontrados valem por 30 dias, respostas
`nao_encontrado` por sete dias e indisponibilidades por uma hora. A consulta ao
OpenCNPJ ocorre apenas para CNPJs ausentes ou vencidos. Se a renovação falhar,
o último dado conhecido é preservado e devolvido com
`cache_desatualizado=true` até uma nova tentativa.

O banco de logs mantém `logs_ingestao`, para auditoria das coletas, e
`logs_aplicacao`, para alertas operacionais. Os dois bancos usam ambientes
Alembic independentes.

## Observabilidade

A aplicação escreve logs operacionais estruturados em JSON na saída padrão. Cada
registro contém `level`, `logger`, `message` e apenas o contexto
pertinente, como duração, contagens, fonte e etapa. Segredos conhecidos são
removidos dos campos estruturados e payloads das fontes externas não são registrados.

As execuções de ingestão do TCE-CE continuam sendo auditadas no banco separado em
`logs_ingestao`; uma falha nesse banco gera um alerta operacional, mas não interrompe
o pipeline principal. Logs `WARNING`, `ERROR` e `CRITICAL` são persistidos em
`logs_aplicacao`; registros `INFO` permanecem apenas na saída padrão para evitar
volume desnecessário no PostgreSQL.

A migration `0003_remove_pncp` exclui as antigas tabelas do módulo de oportunidades. A aplicação dessa migration remove definitivamente os dados existentes nessas tabelas.

O contrato RAW, a política inicial de retenção e as garantias de idempotência
estão em [docs/pncp-raw.md](docs/pncp-raw.md).

Para executar uma coleta de fumaça do PNCP no ambiente Compose isolado, use
`docker compose -p monitorame-pncp-test`. O nome passado em `-p` é uma opção
do Compose e não deve ser colocado no `.env`:

```bash
docker compose -p monitorame-pncp-test up -d --build api
docker compose -p monitorame-pncp-test exec api \
  python scripts/smoke_pncp_publicacao.py --dias 7 --modalidade 6 --tamanho-pagina 10
```

Para reexecutar conscientemente jobs PNCP em `FAILED` ou `QUARANTINED`, sem
alterar jobs bem-sucedidos, use o seletor mínimo necessário:

```bash
docker compose -p monitorame-pncp-test exec -T api \
  python scripts/replay_pncp_jobs.py --job-id 123
```

## Testes

```bash
python -m unittest discover -s tests
```

## Observações mensais de porte (PEN-003)

Publicações versionadas, importação dos dados reais e contrato das rotas: [histórico mensal](docs/portes-mensais.md). O porte observado na fotografia não comprova vigência na data do empenho.



### Pagamentos da Prefeitura e Microempresas

Para reproduzir os valores pagos do portal do TCE, use `GET /pipeline/tce/pagamentos/overview?codigo_municipio=004&exercicio=2025&referencia_final=2026-09` (com o prefixo da API). Esse indicador usa notas de pagamento, exclui Camara e estornos e aplica porte cadastral ME. O overview anterior continua medindo empenhos liquidos. Consulte [regras, auditoria e reproducao](docs/pagamentos-tce.md).

### Qualidade semantica PNCP

A normalizacao avalia qualidade por campo e RAW, preserva valores monetarios com Decimal, datas de origem e codigos desconhecidos, e isola erros de contrato. A migration `0017_pncp_qualidade` acrescenta evidencias versionadas. Homologacao tecnica aprovada em PostgreSQL isolado com amostras reais dos dois endpoints, 63 verificacoes e relatorios publicados em `docs/`. Consulte a [matriz, metricas e procedimento de homologacao](docs/pncp-qualidade-semantica.md). Para exportar metricas e excecoes locais: `python scripts/relatorio_pncp_qualidade.py --saida relatorio-qualidade.json` (filtros opcionais `--run-id` e `--endpoint`).
