# Exemplos JSON de retorno da API

Os exemplos abaixo mostram a estrutura de sucesso das rotas registradas. Valores são ilustrativos; listas podem retornar vários itens ou `[]`. Campos opcionais podem vir como `null`. Valores monetários são inteiros em centavos. Nas rotas de despesas, não são retornados valor bruto, anulações, valor líquido nem médias: somente `valor_final_centavos`.

## Sistema

### `GET /health`

```json
{"status":"healthy"}
```

### `GET /health/pncp`

```json
{"status":"healthy","pncp":{"jobs":{},"fluxos":[],"totais":{"raw_total":0,"raw_backlog":0,"enrichment_backlog":0,"leases_expirados":0,"eventos_gerados":0}}}
```

## TCE-CE

### Metadados comuns de carga analítica

As respostas de overview estadual/municipal, KPI mensal por porte, indicadores
analíticos e comparação compartilham estes campos:

```json
{
  "data_available": true,
  "data_as_of": "2025-01-31",
  "last_successful_load_at": "2025-02-05T12:30:00+00:00",
  "is_stale": true,
  "stale_reason": ["LATEST_LOAD_FAILED"],
  "quality": {
    "municipalities_with_data": 1,
    "municipalities_in_scope": 1,
    "unknown_origin": {
      "expenses": 2,
      "value_centavos": 125000,
      "percent": 20.0
    },
    "historical_size_classification_coverage": {
      "classified_expenses": 8,
      "expenses_in_scope": 10,
      "classification_unit": "CNPJ_BASICO",
      "reference": "EXACT_MONTHLY_COMPETENCE",
      "percent": 80.0
    },
    "partial_period": false,
    "missing_competencies": [],
    "competencies_with_incomplete_municipal_coverage": [],
    "alerts": [
      {
        "code": "LATEST_LOAD_FAILED",
        "message": "A carga mais recente falhou; os valores preservam o último snapshot publicado."
      }
    ]
  }
}
```

`data_as_of` é o último dia coberto por uma competência publicada no recorte.
`last_successful_load_at` é a publicação mais recente nesse escopo. `stale_reason`
é uma lista de códigos estáveis (`LATEST_LOAD_FAILED`, `SNAPSHOT_EXPIRED` e/ou
`PUBLICATION_TIMESTAMP_MISSING`);
ausência de publicação é informada separadamente por `data_available` e por um
alerta de qualidade. `is_stale` representa falha mais recente ou publicação
fora da janela de atualização esperada. `quality.partial_period` é verdadeiro
quando há competências ausentes ou as datas de início/fim não cobrem meses
completos, ou há competências com menos municípios publicados que o catálogo
local do estado. A cobertura histórica compara a raiz de CNPJ (`CNPJ_BASICO`)
com um retrato publicado para a competência exata; o histórico mensal é uma
amostra e não comprova vigência diária ou cobertura integral dos fornecedores.
Os alertas têm códigos e mensagens seguros para exibição, sem documentos ou
dados pessoais.

O TTL usado para o alerta `SNAPSHOT_EXPIRED` é de uma hora para a competência
do mês atual, 24 horas para os três meses anteriores e sete dias para as
competências históricas.

Quando não existe lote publicado, `data_available` é `false`, os valores
monetários retornados são zero e taxas/percentuais são `null`; isso não afirma
que o gasto foi zero. Uma competência publicada sem empenhos é dado disponível
com movimento monetário conhecido igual a zero, mas percentuais sem denominador
também são `null`.

### `GET /pipeline/tce/kpis/portes-por-mes`

```json
{"fonte":"TCE-CE","parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":"004"},"totais":{"empenhos":0,"registros_kpi":0},"limite_resposta":100,"kpi":"portes_por_mes","dados":[{"ano_mes":"2025-01","porte":"ME","total_empenhos":0,"quantidade_empenhos":0,"total_compras":0,"valor_porte":0,"percentual_empenhos":0.0,"percentual_valor":0.0}]}
```

### `GET /pipeline/tce/overview`

O overview sem município consolida o Ceará e considera somente ME no recorte principal. MEI integra os demais portes.

```json
{"fonte":"TCE-CE","origem_dados":"BANCO_LOCAL","consultas_externas":false,"parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":null},"escopo_territorial":"CEARA_INTEIRO","competencias_solicitadas":[],"competencias_processadas":[],"cobertura":{},"politica_atualizacao":{"modo":"SOMENTE_BANCO","atualizar_competencias":false,"atualizar_fornecedores":false},"totais_brutos":{"empenhos":0},"base_analitica":{"nome":"tce_empenhos_liquidos","base_calculo":"empenhos_tce"},"kpi":"overview_me","escopo":{"portes_considerados":["ME"],"regra_participacao_me":"ME / (ME + MEI + EPP + DEMAIS)"},"unidade_monetaria":"CENTAVOS","ranking_municipios_me":[{"posicao":1,"codigo_municipio_tce":"001","municipio":"Amontada","compras_me_centavos":500000,"quantidade_empenhos":12,"percentual_total_me":25.0}],"kpis":{"total_compras_ME_centavos":2000000},"participacao_por_porte_empresarial":[],"elementos_despesa":[],"evolucao_compras_consideradas":[],"destino_recursos":[]}
```

### `GET /pipeline/tce/overview/municipal`

```json
{"fonte":"TCE-CE","origem_dados":"BANCO_LOCAL","consultas_externas":false,"parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":"004","company_sizes":[]},"competencias_solicitadas":[],"competencias_processadas":[],"cobertura":{},"overview":{"kpis":{"total_compras_centavos":0,"percentual_participacao_me":0.0,"total_compras_ME_centavos":0,"percentual_compras_local":0.0,"percentual_compras_fora":0.0},"participacao_por_porte_empresarial":[],"elementos_despesa":[],"evolucao_compras_consideradas":[],"evolucao_compras_por_portes":[],"destino_recursos":[]}}
```

### `GET /public/municipios/{codigo_tce}/previa`

Recebe o código TCE-CE de três dígitos no caminho e `data_inicial` e `data_final` no formato `YYYY-MM-DD`. A rota é pública, somente leitura, e usa dados locais publicados. `resumo_compras.valor_total_centavos` mede pagamentos publicados associados a empenhos, não o valor contratado. `quantidade_compras` conta empenhos distintos com pagamento no período. A contagem e o valor local classificam a sede do fornecedor; fornecedores sem endereço cadastral suficiente não são classificados como locais.

```json
{"municipio":{"codigo_ibge":"2304400","codigo_tce":"004","nome":"Fortaleza","uf":"CE"},"periodo":{"data_inicial":"2025-01-01","data_final":"2025-01-31"},"fonte":"TCE-CE","origem_dados":"BANCO_LOCAL","consultas_externas":false,"data_available":true,"is_stale":false,"competencias_publicadas":["2025-01"],"kpis":{"total_compras_centavos":0,"percentual_participacao_me":0.0,"total_compras_ME_centavos":0,"percentual_compras_fornecedores_locais":0.0,"percentual_recursos_fora_municipio":0.0},"participacao_por_porte_empresarial":[],"resumo_compras":{"medida":"PAGAMENTOS_PUBLICADOS_ASSOCIADOS_A_EMPENHOS","regra_contagem_compras":"EMPENHOS_UNICOS_COM_PAGAMENTO_NO_PERIODO","unidade_monetaria":"CENTAVOS","valor_total_centavos":0,"quantidade_compras":0,"quantidade_fornecedores_identificados":0,"quantidade_fornecedores_sem_documento":0,"compras_na_cidade":{"quantidade_compras":0,"valor_centavos":0,"percentual_do_valor_total":0.0},"fornecedores":[],"fornecedores_exibidos":0,"limite_fornecedores":10}}
```

### `POST /pipeline/tce/comparisons`

```json
{"fonte":"TCE-CE","origem_dados":"BANCO_LOCAL","consultas_externas":false,"comparison_mode":"MUNICIPALITY","left":{"label":"Município A","filters":{},"overview":{}},"right":{"label":"Município B","filters":{},"overview":{}},"sections":{"kpis":[],"participacao_por_porte_empresarial":[],"elementos_despesa":[],"evolucao_compras_consideradas":[],"destino_recursos":[]}}
```

### `GET /pipeline/tce/despesas`

```json
{"source":"TCE-CE","data_source":"LOCAL_DATABASE","external_requests":false,"filters":{"uf":"CE","municipality_tce_code":null,"start_date":"2025-01-01","end_date":"2025-01-31","company_sizes":[],"supplier_origins":[],"expense_element_codes":[],"search":null},"pagination":{"page":1,"page_size":50,"total_items":1,"total_pages":1,"has_previous":false,"has_next":false},"sorting":{"sort_by":"final_value","sort_order":"desc"},"items":[{"chave_despesa":"chave-exemplo","display_id":"EMP-2025-0001","numero_despesa":"0001","data_despesa":"2025-01-15","municipio_comprador":{"codigo_ibge":"2304400","codigo_tce":"004","nome":"Fortaleza","uf":"CE"},"fornecedor":{"documento":"12345678000199","cnpj":"12345678000199","nome":"Empresa Exemplo Ltda","porte":"ME","origem":"LOCAL"},"elemento_despesa":{"codigo_natureza":"339030","codigo_exibicao":"339030","codigo_elemento":"30","nome":"Material de consumo"},"valores":{"valor_final_centavos":125000}}]}
```

### `GET /pipeline/tce/despesas/{chave_despesa}`

```json
{"source":"TCE-CE","data_source":"LOCAL_DATABASE","external_requests":false,"chave_despesa":"chave-exemplo","display_id":"EMP-2025-0001","numero_despesa":"0001","data_despesa":"2025-01-15","municipio_comprador":{"codigo_ibge":"2304400","codigo_tce":"004","nome":"Fortaleza","uf":"CE"},"fornecedor":{"documento":"12345678000199","cnpj":"12345678000199","nome":"Empresa Exemplo Ltda","porte":"ME","origem":"LOCAL","tipo_documento":"CNPJ","cpf":null,"municipio_sede":"Fortaleza","uf_sede":"CE","cnae_principal_codigo":null,"cnae_principal_descricao":null,"observado_em":null},"elemento_despesa":{"codigo_natureza":"339030","codigo_exibicao":"339030","codigo_elemento":"30","nome":"Material de consumo"},"valores":{"valor_final_centavos":125000},"competencia":"2025-01","classificacao_orcamentaria":{"exercicio":2025,"exercicio_orcamento_tce":202500,"codigo_orgao":"01","codigo_unidade_orcamentaria":"01","codigo_natureza":"339030","codigo_exibicao":"339030","codigo_elemento":"30","nome":"Material de consumo"},"referencias":{"estado_despesa":"NORMAL","numero_contrato":null,"numero_licitacao":null,"numero_despesa_substituta":null},"publicacao":{"run_id":"run-exemplo","status":"PUBLICADO"}}
```

### `GET /pipeline/tce/fornecedores`

```json
{"source":"TCE-CE","data_source":"LOCAL_DATABASE","external_requests":false,"filters":{"uf":"CE","municipality_tce_code":null,"start_date":"2025-01-01","end_date":"2025-01-31","company_sizes":[],"supplier_origins":[],"expense_element_codes":[],"search":null},"pagination":{"page":1,"page_size":50,"total_items":1,"total_pages":1,"has_previous":false,"has_next":false},"sorting":{"sort_by":"final_value","sort_order":"desc"},"items":[{"tipo_documento":"CNPJ","documento":"12345678000199","cnpj":"12345678000199","nome":"Empresa Exemplo Ltda","porte":"ME","cadastro_status":"DISPONIVEL","origens_no_recorte":["LOCAL"],"valores":{"valor_final_centavos":125000},"quantidade_despesas":1,"quantidade_municipios":1,"primeira_despesa":"2025-01-15","ultima_despesa":"2025-01-15"}]}
```

### `GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}`

```json
{"source":"TCE-CE","data_source":"LOCAL_DATABASE","external_requests":false,"filters":{"uf":"CE","municipality_tce_code":null,"start_date":"2025-01-01","end_date":"2025-01-31","company_sizes":[],"supplier_origins":[],"expense_element_codes":[]},"fornecedor":{"tipo_documento":"CNPJ","documento":"12345678000199","cnpj":"12345678000199","nome":"Empresa Exemplo Ltda","porte":"ME","origens_no_recorte":["LOCAL"],"cadastro":{"status":"NAO_DISPONIVEL"}},"recorte":{"start_date":"2025-01-01","end_date":"2025-01-31","municipality_tce_code":null,"valor_final_centavos":125000,"quantidade_despesas":1,"quantidade_contratos":0,"quantidade_licitacoes":0,"quantidade_municipios":1,"primeira_despesa":"2025-01-15","ultima_despesa":"2025-01-15","meses_com_movimento":1},"evolucao_mensal":[{"competencia":"2025-01","valor_final_centavos":125000,"quantidade_despesas":1}],"por_elemento_despesa":[],"participacao_no_recorte":{"valor_total_recorte_centavos":125000,"valor_fornecedor_centavos":125000,"percentual":100.0},"despesas":{"total_items":1,"path":"/pipeline/tce/fornecedores/CNPJ/12345678000199/despesas"}}
```

### `GET /pipeline/tce/fornecedores/{tipo_documento}/{documento}/despesas`

```json
{"source":"TCE-CE","data_source":"LOCAL_DATABASE","external_requests":false,"filters":{"uf":"CE","municipality_tce_code":null,"start_date":"2025-01-01","end_date":"2025-01-31","company_sizes":[],"supplier_origins":[],"expense_element_codes":[],"search":null},"fornecedor":{"tipo_documento":"CNPJ","documento":"12345678000199","nome":"Empresa Exemplo Ltda"},"pagination":{"page":1,"page_size":50,"total_items":1,"total_pages":1,"has_previous":false,"has_next":false},"sorting":{"sort_by":"final_value","sort_order":"desc"},"items":[{"chave_despesa":"chave-exemplo","display_id":"EMP-2025-0001","numero_despesa":"0001","data_despesa":"2025-01-15","municipio_comprador":{"codigo_ibge":"2304400","codigo_tce":"004","nome":"Fortaleza","uf":"CE"},"fornecedor":{"documento":"12345678000199","cnpj":"12345678000199","nome":"Empresa Exemplo Ltda","porte":"ME","origem":"LOCAL"},"elemento_despesa":{"codigo_natureza":"339030","codigo_exibicao":"339030","codigo_elemento":"30","nome":"Material de consumo"},"valores":{"valor_final_centavos":125000}}]}
```

### `GET /pipeline/tce/analitico/indicadores`

```json
{"fonte":"TCE-CE","parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":"004"},"totais":{"empenhos":0,"indicadores":{}},"limite_resposta":100,"kpi":"indicadores_analiticos","indicadores":{"por_porte":[],"por_elemento":[],"por_fornecedor":[],"por_municipio":[],"evolucao_mensal":[],"concentracao_fornecedores":[]}}
```

## PNCP

### `GET /pncp/editais`

Retorna apenas os campos de resumo para montar listagens e cart?es.

```json
{"dados":[{"data_quality_status":"VALID","sync_status":"SUCCEEDED","id":"00000000000000000001","source":"PNCP","url_oficial":"https://pncp.gov.br/app/editais/00000000000000000001","orgao":null,"unidade":null,"objeto":"Aquisi??o de materiais","modalidade":null,"situacao":null,"prazo_proposta_final":null,"atualizado_em":null,"itens_disponiveis":false,"anexos_disponiveis":false}],"paginacao":{"pagina":1,"tamanho_pagina":20,"total":1}}
```

### `GET /pncp/editais/{external_id}`

Retorna os dados da contrata??o e o detalhe enriquecido, quando dispon?vel. O identificador pode conter barras, por exemplo `80881915000192-1-000105/2026`.

```json
{"data_quality_status":"VALID","sync_status":"SUCCEEDED","dados_semanticos":{},"id":"00000000000000000001","source":"PNCP","url_oficial":"https://pncp.gov.br/app/editais/00000000000000000001","orgao":null,"unidade":null,"objeto":"Aquisi??o de materiais","modalidade":null,"situacao":null,"prazo_proposta_final":null,"atualizado_em":null,"itens_disponiveis":false,"anexos_disponiveis":false,"detalhe":null}
```

### `GET /pncp/editais/{external_id}/itens`

Retorna itens estruturados, resultados por item, lotes identificados e cobertura da participa??o.

### `GET /pncp/editais/{external_id}/anexos`

Retorna o manifesto de documentos, URLs oficiais, disponibilidade e cobertura, sem baixar os arquivos.

### `GET /pncp/editais/{external_id}/historico`

Retorna os eventos do histórico persistido pelo enriquecimento assíncrono. A resposta contém `edital_id`, `historico` e `disponivel`; antes de o recurso ser coletado, ou quando o edital foi atualizado depois da coleta, `historico` é `null` e `disponivel` é `false`.

```json
{"edital_id":"00000000000000000001","historico":[{"categoriaLogManutencaoNome":"Contratação","tipoLogManutencaoNome":"Inclusão","logManutencaoDataInclusao":"2026-11-10T11:55:59"}],"disponivel":true}
```

## Formato de erro

Rotas com envelope de erro próprio (despesas, fornecedores, comparações e PNCP) retornam, por exemplo:

```json
{"error":{"code":"NOT_FOUND","message":"Registro não encontrado.","details":[],"request_id":"req_exemplo"}}
```

As rotas TCE analíticas que usam o handler padrão do FastAPI retornam `{"detail":"mensagem"}` para erro de validação ou indisponibilidade.
