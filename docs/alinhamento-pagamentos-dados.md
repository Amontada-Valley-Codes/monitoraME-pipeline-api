# Alinhamento de pagamentos com o projeto `dados`

## Mapeamento comparativo

O projeto `dados` do ZIP de referência não foi alterado. A comparação foi feita
principalmente contra `app/limpeza/limpar_despesas.py`,
`app/database/consultas.py`, `app/database/migracoes/versions/0003_views.py`,
`app/routes/`, `app/kpis/kpis.py` e `tests/test_regressao_acarau.py`.

| Tema | Fonte `dados` | Este serviço |
|---|---|---|
| fato financeiro | `vw_fato_despesa`, uma linha por pagamento não estornado | `tce_pagamentos` publicados, ligados a `tce_empenhos` |
| valor | `valor_nota_pagamento` em `Decimal`/`NUMERIC(15,2)` | `valor_nota_pagamento_centavos` em inteiro |
| período | `data_nota_pagamento`, início e fim inclusivos | `data_nota_pagamento`, início e fim inclusivos |
| orçamento | `exercicio_orcamento` independente do período | `exercicio_orcamentario` independente do período |
| chave do empenho | município, exercício, órgão, unidade, data de emissão e número | os mesmos seis campos, preservando texto e zeros à esquerda |
| chave do pagamento | chave do empenho + subempenho + nota | os mesmos oito campos |
| estorno | somente estado conhecido; estornados fora do fato líquido | estado `0` entra, `1` sai e estado desconhecido rejeita a análise |
| Câmara | excluída pelo cadastro de órgãos | excluída por `tce_orgaos_classificacao`; órgão sem classificação rejeita a análise |
| elementos | posições 5 e 6 da natureza de oito dígitos | posições `[4:6]`, limitadas a `39`, `30`, `51`, `52`, `32`, `40`, `35` |
| fornecedor financeiro | empenho de credor tipo `1`, com CNPJ válido | mesmo recorte; PF e documento inválido não entram no denominador |
| cadastro ausente | pagamento permanece; cobertura/pendência é exposta | pagamento permanece no controle; porte fica não identificado e reduz a cobertura |
| ME | porte cadastral `ME` | subtotal próprio `total_me_centavos` |
| MPE | ME + EPP + MEI indicado pelo cadastro | KPI separado; nunca usa o rótulo MPE para o subtotal ME |

## Contratos das rotas

- `GET /pipeline/tce/despesas` e seu detalhe continuam representando
  **empenhos líquidos**, não pagamentos. O campo legado
  `valor_final_centavos` foi preservado e o alias explícito
  `valor_empenho_liquido_centavos` foi acrescentado.
- `GET /pipeline/tce/overview`, `/overview/municipal` e as rotas de assinante
  calculam valores exclusivamente com pagamentos publicados não estornados.
  Empenhos associados são dimensões para natureza e fornecedor. O exercício
  orçamentário usado pelas rotas `/overview` é o ano de `data_inicial`, separado
  do período civil dos pagamentos.
- `GET /pipeline/tce/pagamentos/overview` é o contrato de valores pagos. Aceita
  o par exato `data_inicial`/`data_final` ou, por compatibilidade,
  `referencia_final`; neste último caso o início é janeiro do exercício e o fim
  é o último dia do mês informado.
- `GET /pipeline/tce/kpis/portes-por-mes` usa pagamentos e aceita o filtro
  opcional `exercicio_orcamentario` separado das datas.
- `GET /public/municipios/{codigo_tce}/previa` é consumidor de pagamentos e foi
  direcionado à consulta dedicada.

Todas essas leituras usam somente snapshots locais; não iniciam ingestão nem
consultam TCE/OpenCNPJ durante uma requisição.

## Completude da coleta

O projeto `dados` registrou que a paginação geral por `$start_index` omitiu
documentos durante sua validação. A ingestão incremental passou a repetir uma
competência que atinja 1.000 registros por órgão e, se necessário, por unidade
orçamentária. Uma partição de unidade que ainda atinja o teto rejeita o lote.
As subdivisões também precisam conter todos os documentos observados na
consulta mais ampla.

A Câmara segue exatamente o cadastro do SIM: `codigo_tipo_unidade=02`. Essa
regra substitui inferência pelo nome do órgão. Snapshots coletados pela
estratégia antiga precisam ser reprocessados para que uma leitura nova possa
reproduzir os totais do `dados`.

## Qualidade e falhas explícitas

A análise deduplica uma repetição byte-equivalente da mesma chave, sem somá-la
duas vezes, e não avança quando a chave reaparece com conteúdo conflitante,
quando há empenho duplicado com dimensões conflitantes, pagamento órfão, órgão sem
classificação ou estado de estorno desconhecido. A ausência cadastral do
fornecedor não é uma dessas falhas estruturais: o valor continua no total e a
cobertura cadastral é reduzida.

## Limitação da regressão de Acaraú

O ZIP contém os valores aprovados e um teste que refaz a coleta ao vivo, mas não
contém a coleta bruta nem o snapshot cadastral usados nessa execução. O teste
offline deste repositório procura
`tests/fixtures/tce_pagamentos_acarau_2025_aprovada.json` e é ignorado com motivo
explícito enquanto esse artefato não for fornecido. Nenhum conjunto sintético é
usado para simular a validação real.
