# Persistência de despesas calculadas do TCE-CE

## Decisão de contrato

A saída calculada é publicada como um snapshot completo por município e
exercício orçamentário. O snapshot contém fatos de três naturezas:

- `EMPENHO`: uma linha por chave natural de empenho. `valor_centavos` é o
  valor líquido calculado; os valores empenhado, anulado acumulado e líquido
  também são preservados separadamente.
- `ANULACAO_EMPENHO`: uma linha por nota de anulação. `valor_centavos` é o
  valor positivo da anulação e `registro_pai_id` aponta para o empenho do mesmo
  snapshot.
- `PAGAMENTO`: uma linha por nota de pagamento não estornada considerada no
  cálculo. `valor_centavos` é o valor pago e `registro_pai_id` aponta para o
  empenho que fornece natureza e fornecedor.

Não se deve somar naturezas diferentes como se fossem uma única medida. Para
indicadores de despesas finais, filtre `EMPENHO`; para fluxo pago, filtre
`PAGAMENTO`. Anulações são fatos auditáveis e já estão incorporadas ao valor
líquido do empenho.

Todos os valores monetários são inteiros em centavos. `float` não é aceito.
Cada registro congela município, órgão, fornecedor, porte e natureza usados no
cálculo. Uma atualização posterior do cadastro não altera silenciosamente um
snapshot já publicado.

## Identidades e vínculos

- A chave do empenho continua sendo
  `municipio|exercicio|orgao|unidade|data_empenho|numero_empenho`.
- A chave da anulação continua sendo
  `chave_empenho|data_anulacao|numero_anulacao`.
- A chave do pagamento continua sendo
  `chave_empenho|numero_sub_empenho|numero_nota_pagamento`.
- A identidade dentro de um snapshot é
  `(run_id, natureza, chave_registro)`.
- Anulação ou pagamento sem empenho no mesmo resultado é rejeitado.
- Município, exercício, órgão, unidade, natureza de despesa e identidade do
  fornecedor de um filho devem ser iguais aos do empenho vinculado.

O vínculo físico usa `registro_pai_id`, uma chave estrangeira para
`tce_despesas_calculadas.id`. `chave_empenho` é mantida para rastreabilidade e
consultas entre versões.

## Períodos

Dois campos evitam misturar semânticas temporais:

- `competencia_documento`: competência do lote/documento de origem;
- `competencia_analitica`: competência na qual o cálculo atribui o fato.

`data_referencia` preserva a data do evento. Um pagamento pode, por exemplo,
ter documento em março e ser atribuído analiticamente à competência de origem
do empenho, conforme a regra vigente. Essa decisão fica visível nos dados.

## Porte do fornecedor

`porte_fornecedor` aceita `MEI`, `ME`, `EPP`, `DEMAIS` ou
`NAO_IDENTIFICADO`. O cálculo deve informar também `fonte_porte` e
`porte_observado_em` quando disponíveis.

O porte é uma dimensão observada e congelada, não uma alegação automática de
vigência histórica. Se o cálculo não possuir evidência suficiente, deve usar
`NAO_IDENTIFICADO`. Os retratos mensais de `porte_publicacoes` podem ser usados
por uma versão futura do algoritmo, desde que a versão e a política temporal
sejam registradas em `algoritmo_versao` e `fontes`.

## Formato de publicação

O arquivo de entrada possui este envelope:

```json
{
  "codigo_municipio_tce": "010",
  "exercicio_orcamento": 202500,
  "algoritmo_versao": "despesas-tce-v1",
  "fontes": {
    "tce_despesa_run_ids": [100, 101],
    "tce_pagamento_run_ids": [200]
  },
  "registros": [
    {
      "natureza": "EMPENHO",
      "chave_registro": "010|202500|17|01|2025-01-31|31010002",
      "chave_empenho": "010|202500|17|01|2025-01-31|31010002",
      "fonte": "TCE-CE",
      "source_run_id": 100,
      "source_record_id": 500,
      "competencia_documento": "2025-01",
      "competencia_analitica": "2025-01",
      "data_referencia": "2025-01-31",
      "exercicio_orcamento": 202500,
      "codigo_municipio_tce": "010",
      "codigo_municipio_ibge": "2300754",
      "municipio": "Amontada",
      "uf": "CE",
      "codigo_orgao": "17",
      "nome_orgao": "Prefeitura Municipal",
      "codigo_unidade_orcamentaria": "01",
      "tipo_documento_fornecedor": "CNPJ",
      "documento_fornecedor": "10496308000123",
      "cnpj_fornecedor": "10496308000123",
      "cpf_fornecedor": null,
      "nome_fornecedor": "Fornecedor LTDA",
      "municipio_fornecedor": "Amontada",
      "uf_fornecedor": "CE",
      "porte_fornecedor": "ME",
      "fonte_porte": "RECEITA_FEDERAL_VIA_OPENCNPJ",
      "porte_observado_em": "2025-02-01T12:00:00+00:00",
      "natureza_despesa_codigo": "52",
      "natureza_despesa_descricao": "Equipamentos e material permanente",
      "valor_centavos": 90000,
      "valor_empenhado_centavos": 100000,
      "valor_anulado_centavos": 10000,
      "valor_liquido_centavos": 90000,
      "atributos": {}
    }
  ]
}
```

Anulações e pagamentos usam os mesmos campos dimensionais. Eles não preenchem
`valor_empenhado_centavos`, `valor_anulado_centavos` nem
`valor_liquido_centavos`; esses campos pertencem somente ao empenho.

Depois de aplicar a migration, publique o resultado:

```bash
alembic upgrade head
python scripts/persistir_tce_despesas_calculadas.py --arquivo resultado.json
```

Saída `reutilizado=true` significa que o mesmo conteúdo, fontes e versão do
algoritmo já estavam vigentes e nenhuma linha foi criada.

## Idempotência, correções e histórico

Os registros são ordenados e serializados canonicamente. O SHA-256 de
`fontes + registros` forma `input_hash`; listas dentro de `fontes` são tratadas
como conjuntos e ordenadas para que a ordem de IDs não quebre a idempotência.
Cada fato também recebe
`conteudo_hash`.

Sob bloqueio transacional por município/exercício:

1. mesmo `input_hash` e mesma `algoritmo_versao`: reutiliza o run vigente;
2. conteúdo ou algoritmo diferente: marca o vigente como `SUBSTITUIDO` e
   publica um novo run na mesma transação;
3. falha de validação ou persistência: rollback integral;
4. versões substituídas não expiram e não são apagadas pelo fluxo normal.

Há somente um run `PUBLICADO` por `escopo_chave`, protegido por índice parcial
único. Consultas correntes leem apenas esse run. A função
`listar_historico_registro` lê versões publicadas e substituídas.

## Consultas

`listar_resultados_calculados` em
`app.pipeline.persistence.tce_despesas_calculadas` consulta somente resultados
vigentes e filtra município, exercício, competência analítica, natureza,
órgão, natureza de despesa, porte e identidade do fornecedor por documento ou
CNPJ. A função não consulta fontes externas e não altera estado.

`listar_historico_registro` recebe natureza e chave e devolve todas as versões,
com status do run, versão do algoritmo, hashes e data de publicação.

Não foi adicionada rota HTTP nesta entrega. Backend e Análise de Dados podem
consumir as tabelas ou a camada de persistência depois de aprovarem o contrato;
uma rota pública deve ser criada somente quando paginação, autorização e
campos de resposta forem definidos.

## Validação com Análise de Dados

Antes da promoção para produção, a equipe deve registrar a aprovação destes
pontos:

- granularidade de cada natureza;
- regra de `competencia_analitica`, especialmente para pagamentos;
- semântica positiva do valor de anulação;
- política e procedência do porte;
- versão inicial do algoritmo;
- conjunto definitivo de atributos adicionais.

Alterações nesses itens exigem nova `algoritmo_versao`; não reescrevem runs já
publicados.
