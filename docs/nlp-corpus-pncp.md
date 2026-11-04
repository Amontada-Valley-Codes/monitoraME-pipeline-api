# Preparação do corpus textual PNCP

## Escopo e fontes

`app/nlp/corpus.py` lê exclusivamente o PostgreSQL Analytics configurado em
`DATABASE_URL`, pelas tabelas normalizadas `pncp_oportunidades` e
`pncp_itens`. Não importa o cliente HTTP PNCP nem consulta RAW ou payloads de
enriquecimento não normalizados. A ingestão existente permanece independente.

O corpus atual produz segmentos vinculados pela chave `source` +
`external_id`:

| Tipo | Origem | Identidade do segmento |
|---|---|---|
| `OBJECT` | `pncp_oportunidades.objeto` | `object` |
| `COMPLEMENTARY` | `dados_semanticos.informacao_complementar`, quando preenchido | `complementary:informacao_complementar` |
| `COMPLEMENTARY` | `dados_semanticos.orcamento_sigiloso_descricao`, quando preenchido | `complementary:orcamento_sigiloso_descricao` |
| `ITEM` | `pncp_itens.descricao` | `item:{numero_item}` |
| `LOT` | descrições dos itens associados ao mesmo `numero_lote` | `lot:{numero_lote}` |

O Pipeline não possui uma coluna autônoma de texto do lote. Por isso, o
segmento de lote é uma representação derivada das descrições dos itens e
contém `item_ids`; não se inventa descrição de lote. Metadados como modalidade,
situação, datas, valores, quantidade, unidade, número do lote e qualidade ficam
em `structured_metadata`, fora do texto usado semanticamente. Valores são
preservados como recebidos do modelo normalizado; não há conversão ou cálculo
monetário nesta preparação.

## Normalização, hash e incompletude

A normalização decodifica entidades HTML, remove marcação, aplica Unicode
NFKC e compacta espaços. Preserva acentos, caixa, pontuação, termos técnicos e
números. `text_hash` é SHA-256 UTF-8 do texto normalizado. Texto ausente,
curto/inutilizável ou sem caracteres alfanuméricos gera `NO_USABLE_TEXT`, sem
hash/texto, e não interrompe os demais segmentos. Segmentos utilizáveis ficam
com estado `READY`.

Cada linha persiste origem, identificador PNCP, campo de origem, versão/hash da
contratação e do item, vínculo de item/lote, metadados estruturados, texto,
hash e horário de preparação. A tabela `nlp_procurement_segments` é isolada
das tabelas de ingestão. Um upsert só atualiza segmento quando algum conteúdo,
versão, metadado ou status mudou; segmentos que deixaram de existir na visão
atual são removidos no mesmo escopo de contratação. O processo percorre o
corpus em páginas e é idempotente. Mudança apenas de situação/data pode
atualizar metadados sem mudar o `text_hash`, permitindo reutilizar embeddings
futuramente.

## Execução local

Após configurar o `.env` e aplicar a migration principal:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m app.nlp.cli sync-corpus --dry-run
.venv\Scripts\python.exe -m app.nlp.cli sync-corpus
```

`--dry-run` calcula contagens sem gravar. A execução normal persiste o corpus
no Analytics e informa oportunidades lidas, segmentos criados/alterados/
inalterados, segmentos sem texto e segmentos obsoletos removidos. `--page-size`
ajusta a leitura (1 a 2000, padrão 200). Não é necessário Backend, Redis,
autenticação, assinatura ou conectividade com o PNCP.

Esta etapa prepara e persiste texto; geração de embeddings, matching e leitura
de documentos/anexos continuam fora deste comando.

Para registros já normalizados sob `pncp-semantica-1`, a versão nova não
preenche o campo retroativamente sozinha. Agende reprocessamento dos RAW atuais
e drene a fila `normalize`; isso atualiza a visão, o corpus e cria os embeddings
faltantes:

```powershell
docker compose run --rm api python scripts/pncp_operacao.py reprocessar-normalizacao --limite 2000
docker compose run --rm api python scripts/pncp_operacao.py normalizar --limite 2000
docker compose run --rm api python -m app.nlp.cli sync-corpus
```
