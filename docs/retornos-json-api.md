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

### `GET /pipeline/tce/contratos`

```json
{"fonte":"TCE-CE","parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":"004"},"totais_brutos":{},"totais":{"contratos":0},"limite_resposta":100,"dados":[]}
```

### `GET /pipeline/tce/kpis/portes-por-mes`

```json
{"fonte":"TCE-CE","parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":"004"},"totais":{"empenhos":0,"registros_kpi":0},"limite_resposta":100,"kpi":"portes_por_mes","dados":[{"ano_mes":"2025-01","porte":"ME","total_empenhos":0,"quantidade_empenhos":0,"total_compras":0,"valor_porte":0,"percentual_empenhos":0.0,"percentual_valor":0.0}]}
```

### `GET /pipeline/tce/overview`

O overview sem município consolida o Ceará e considera somente ME no recorte principal. MEI integra os demais portes.

```json
{"fonte":"TCE-CE","origem_dados":"BANCO_LOCAL","consultas_externas":false,"parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":null},"escopo_territorial":"CEARA_INTEIRO","competencias_solicitadas":[],"competencias_processadas":[],"cobertura":{},"politica_temporal":"COMPETENCIAS_PUBLICADAS","totais_brutos":{"empenhos":0},"base_analitica":{"nome":"tce_empenhos_liquidos","base_calculo":"DESPESA_FINAL"},"kpi":"overview_me","escopo":{"porte":"ME","mei":"DEMAIS_PORTES"},"unidade_monetaria":"CENTAVOS","overview":{"kpis":{},"participacao_por_porte_empresarial":[],"elementos_despesa":[],"evolucao_compras_consideradas":[],"destino_recursos":[]}}
```

### `GET /pipeline/tce/overview/municipal`

```json
{"fonte":"TCE-CE","origem_dados":"BANCO_LOCAL","consultas_externas":false,"parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":"004","company_sizes":[]},"competencias_solicitadas":[],"competencias_processadas":[],"cobertura":{},"overview":{"kpis":{"total_compras_centavos":0,"percentual_participacao_me":0.0,"total_compras_ME_centavos":0,"percentual_compras_local":0.0,"percentual_compras_fora":0.0},"participacao_por_porte_empresarial":[],"elementos_despesa":[],"evolucao_compras_consideradas":[],"evolucao_compras_por_portes":[],"destino_recursos":[]}}
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

### `GET /pipeline/tce/analise-territorial`

```json
{"fonte":"TCE-CE","origem_dados":"BANCO_LOCAL","consultas_externas":false,"unidade_monetaria":"CENTAVOS","parametros":{"start_date":"2025-01-01","end_date":"2025-01-31","uf":"CE","municipality_tce_code":"004"},"municipio":{"codigo_tce":"004","nome":"Fortaleza","uf":"CE"},"cobertura":{"despesas":0,"despesas_com_cache_cadastral":0},"resumo_financeiro":{"valor_final_centavos":0,"quantidade_despesas":0},"indicadores_territoriais":{},"destino_recursos":[],"participacao_por_porte":[],"elementos_despesa":[],"evolucao_mensal":[],"concentracao_fornecedores":{},"ranking_fornecedores":[],"metodologia":{}}
```

### `GET /pipeline/tce/analitico/indicadores`

```json
{"fonte":"TCE-CE","parametros":{"data_inicial":"2025-01-01","data_final":"2025-01-31","codigo_municipio":"004"},"totais":{"empenhos":0,"indicadores":{}},"limite_resposta":100,"kpi":"indicadores_analiticos","indicadores":{"por_porte":[],"por_elemento":[],"por_fornecedor":[],"por_municipio":[],"evolucao_mensal":[],"concentracao_fornecedores":[]}}
```

### `GET /pipeline/tce/pagamentos/overview`

```json
{"fonte":"TCE-CE/SIM","medida":"VALOR_NOTA_PAGAMENTO_NAO_ESTORNADA","unidade_monetaria":"CENTAVOS","administracao":"PREFEITURA","codigo_municipio":"004","exercicio_orcamento":202500,"referencias_empenhos":[],"referencias_pagamentos":[],"coleta_iniciada_em":"2025-02-01T00:00:00","coleta_concluida_em":"2025-02-01T00:01:00","criterio_me":{"porte_codigo":"01","excluir_mei":false},"referencia_base_cadastral":null,"porte_historico_comprovado":false,"consulta_cadastral_executada":true,"auditoria":{},"total_me_centavos":0,"me_por_elemento_centavos":{},"fornecedores_me":[],"controle_financeiro":{},"classificacao_completa":true,"pendencias":[],"classificacoes":[]}
```

## Porte empresarial histórico

### `GET /pipeline/tce/portes/competencias`

```json
{"data_source":"LOCAL_DATABASE","external_requests":false,"politica_temporal":"COMPETENCIA_EXATA_SEM_PREENCHIMENTO","competencia_final":"2025-01","items":[{"competencia":"2025-01","versao":"versao-exemplo","ativa":true,"fonte":"RECEITA_FEDERAL_DADOS_ABERTOS","procedencia":"RETRATO_MENSAL_PUBLICADO","observado_em":"2025-02-01T00:00:00","importado_em":"2025-02-02T00:00:00","referencia_extracao_comprovada":false,"vigencia_diaria_comprovada":false,"cobertura":{},"universo":"RAIZES_SELECIONADAS_NO_PIPELINE","artefatos":[]}]}
```

### `GET /pipeline/tce/portes/historico/{cnpj}`

```json
{"cnpj_consultado":"12345678000199","raiz":"12345678","unidade_cadastral":"CNPJ_BASICO","data_source":"LOCAL_DATABASE","external_requests":false,"politica_temporal":"COMPETENCIA_EXATA_SEM_PREENCHIMENTO","vigencia_diaria_comprovada":false,"competencias_sem_publicacao":[],"items":[{"competencia":"2025-01","versao":"versao-exemplo","status":"IDENTIFICADO","porte_codigo":"01","porte_observado":"ME","optante_mei":false,"simples_disponivel":true,"publicacao":{}}]}
```

### `GET /pipeline/tce/portes/versoes/{versao}/{cnpj}`

```json
{"cnpj_consultado":"12345678000199","raiz":"12345678","unidade_cadastral":"CNPJ_BASICO","data_source":"LOCAL_DATABASE","external_requests":false,"politica_temporal":"COMPETENCIA_EXATA_SEM_PREENCHIMENTO","vigencia_diaria_comprovada":false,"competencias_sem_publicacao":[],"items":[{"competencia":"2025-01","versao":"versao-exemplo","status":"IDENTIFICADO","porte_codigo":"01","porte_observado":"ME","optante_mei":false,"simples_disponivel":true,"publicacao":{}}]}
```

## PNCP

### `GET /pncp/editais`

```json
{"dados":[{"data_quality_status":"VALID","sync_status":"SUCCEEDED","dados_semanticos":{},"id":"00000000000000000001","source":"PNCP","url_oficial":"https://pncp.gov.br/app/editais/00000000000000000001","orgao":null,"unidade":null,"objeto":"Aquisição de materiais","modalidade":null,"situacao":null,"prazo_proposta_final":null,"atualizado_em":null,"itens_disponiveis":false,"anexos_disponiveis":false}],"paginacao":{"pagina":1,"tamanho_pagina":20,"total":1}}
```

### `GET /pncp/editais/{external_id}`

```json
{"data_quality_status":"VALID","sync_status":"SUCCEEDED","dados_semanticos":{},"id":"00000000000000000001","source":"PNCP","url_oficial":"https://pncp.gov.br/app/editais/00000000000000000001","orgao":null,"unidade":null,"objeto":"Aquisição de materiais","modalidade":null,"situacao":null,"prazo_proposta_final":null,"atualizado_em":null,"itens_disponiveis":false,"anexos_disponiveis":false,"detalhe":null,"itens":null,"anexos":null}
```

## Formato de erro

Rotas com envelope de erro próprio (despesas, fornecedores, análise territorial, comparações, porte histórico e PNCP) retornam, por exemplo:

```json
{"error":{"code":"NOT_FOUND","message":"Registro não encontrado.","details":[],"request_id":"req_exemplo"}}
```

As rotas TCE analíticas que usam o handler padrão do FastAPI retornam `{"detail":"mensagem"}` para erro de validação ou indisponibilidade.
