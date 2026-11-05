# Pagamentos da Prefeitura e filtro cadastral ME

Implementacao do guia fornecido "Como reproduzir os valores do Portal da Transparencia do TCE". Auditoria executada em 20/10/2026, na branch `20_10_2026`.

## Divergencias encontradas

O fluxo anterior coleta `notas_empenhos` e `notas_anulacoes_empenhos`, publica empenhos liquidos e calcula o overview de ME + MEI. Nao coleta notas de pagamento nem identifica a Camara para esse indicador. Seu parametro mensal vincula o ano documental ao exercicio, o que nao atende a pagamentos de anos posteriores do mesmo orcamento. Por isso, o resultado desse overview nao pode ser apresentado como os pagamentos da Prefeitura descritos no guia.

As rotas de empenhos mantem sua medida. A nova rota de pagamentos implementa o indicador do guia; consumidores que exibem valor pago devem usa-la. Este repositorio nao contem a interface publica que consome esses valores.

O normalizador cadastral compartilhado tambem foi corrigido: aceita `01`, `03`, `05`, preserva os equivalentes numericos e deixa o codigo desconhecido `2` sem classificacao, em vez de presumir ME. O novo fluxo usa o porte original e guarda o indicador MEI separadamente; nao passa pelo cleaner antigo que transforma optantes MEI em uma categoria de porte distinta.

## Consulta

```http
GET /pipeline/tce/pagamentos/overview?codigo_municipio=004&exercicio=2025&referencia_final=2026-09
```

Use o prefixo de montagem da API da aplicacao antes de `/pipeline`. O exercicio e o ano do orcamento; a referencia final e o ultimo mes da documentacao consultada, nao a data limite de emissao do pagamento. O inicio e janeiro do exercicio e o termino precisa cobrir pelo menos dezembro desse ano.

O endpoint consulta as fontes externas em cada chamada. A execucao integral pode demorar varios minutos; nao usa os snapshots versionados de empenhos. A coleta bruta pode ser preservada pelo comando de auditoria. A ingestao incremental mensal de notas de pagamento e documentada em [tce-notas-pagamentos.md](tce-notas-pagamentos.md), mas esta rota ainda nao consome os snapshots persistidos nem persiste portes.

- `total_me_centavos`, `me_por_elemento_centavos` e `fornecedores_me`: valores para o recorte publico ME.
- `controle_financeiro`: totais antes do porte, por elemento e por categoria exclusiva (ME, EPP, Demais, sem classificacao e documento nao CNPJ).
- `classificacoes`: um cadastro por CNPJ, porte original e normalizado, fonte, instante da consulta, indicador MEI e situacao da classificacao.
- `pendencias`: chave de pagamento, documento original, motivo e valor afetado. Ausencia cadastral nao reduz o controle financeiro.
- `classificacao_completa`: falsa quando existem pagamentos com classificacao pendente ou documento nao CNPJ valido. Um subtotal parcial nao deve aparecer como definitivo.
- `referencia_base_cadastral: null` e `porte_historico_comprovado: false`: o adaptador atual consulta o retrato atual e nao tem referencia mensal documentada da base. Nao presume que o porte consultado era o vigente em 2025.

ME significa porte cadastral `01`, sem exclusao adicional de MEI. Nome empresarial, opcao pelo Simples e valores recebidos nao determinam porte. O CNPJ retornado pela fonte deve corresponder ao solicitado. Documentos incompletos, invalidos ou de outro tipo ficam rastreaveis.

O total consolidado usa cada nota de pagamento uma unica vez. O fornecedor e identificado pelo CNPJ completo normalizado, nunca pelo nome: pagamentos de um mesmo CNPJ ficam no mesmo grupo mesmo quando empenhos antigos e novos registram razoes sociais diferentes. `fornecedores_me` preserva `nomes_observados` para auditoria e continua expondo um unico valor agregado por CNPJ e elemento. Nomes iguais com CNPJs diferentes permanecem fornecedores distintos; filiais com CNPJs completos diferentes tambem nao sao fundidas automaticamente.

## Regras financeiras

O cadastro `orgaos`, consultado no escopo do municipio, identifica a Camara por tipo `02` ou nome contendo Camara, sem fixar um numero universal. Os demais codigos cadastrados sao identificados como orgaos do Executivo municipal. A conciliacao exige ao menos um orgao de cada grupo e rejeita qualquer pagamento cujo orgao nao esteja no cadastro. A auditoria retorna os codigos identificados em `orgaos_executivo_municipal` e `orgaos_camara`.

Coletam-se os doze meses de empenhos e todos os meses de pagamentos ate a referencia final. A API documenta filtros opcionais por `codigo_orgao` e `codigo_unidade_orcamentaria`, mas nao oferece ordenacao para estabilizar offsets. Por isso, cada competencia documental é consultada por órgão e reinicia `$start_index` em zero. Se a partição de um órgão retornar pelo menos 1.000 notas, ela é refeita por unidade orçamentária; partições de unidade acima de 1.000 impedem publicar o resultado como completo. A resposta registra as dimensões e chamadas realizadas. Falhas HTTP, respostas sem uma lista valida de `elements` e itens malformados nao encerram meses como vazios. O exercicio orcamentario permanece fixo, inclusive em referencias posteriores.

O relacionamento usa municipio, exercicio, orgao, unidade orcamentaria, data de emissao do empenho e numero do empenho. Datas e espacos externos sao normalizados; zeros dos identificadores sao preservados. A referencia documental nao integra a chave. A chave de pagamento acrescenta numero do subempenho e numero da nota.

Uma chave de pagamento repetida com payload identico e contada uma unica vez, com anomalia registrada na auditoria. Duplicidades conflitantes interrompem o calculo com a chave na mensagem; nenhuma versao e escolhida arbitrariamente. Pagamentos sem empenho impedem a soma. Estados de estorno diferentes de `0` e `1` permanecem auditaveis, mas sao excluidos do total.

Somam-se centavos de `valor_nota_pagamento`, nunca o valor empenhado. Consideram-se os orgaos do Executivo municipal e excluem-se pagamentos da Camara, do estado de estorno `1` e de elementos fora de `39`, `30`, `51`, `52`, `32`, `40`, `35`. O elemento corresponde as posicoes cinco e seis do codigo de oito digitos.

## Auditoria real de Acarau/2025

Uma primeira consulta foi rejeitada por chave de pagamento repetida (`004`, `202500`, `09`, `01`, `2025-05-05`, `05050112`, `001`, `16060111`). Essa resposta nao foi usada para publicar valores; a causa da repeticao nao foi estabelecida. Uma nova coleta integral, preservada localmente, apresentou 6.712 empenhos e 11.956 pagamentos, sem chaves repetidas e sem pagamentos orfaos.

Apos os filtros financeiros, restaram 8.156 pagamentos e 300 CNPJs distintos. As exclusoes sao mutuamente exclusivas, na ordem Camara, estorno e natureza: 661 pagamentos da Camara, 36 estornados e 3.103 fora das naturezas.

| Elemento | API reconstruida em 20/10/2026 | Referencia do guia | Diferenca |
| --- | ---: | ---: | ---: |
| 39 | R$ 66.318.441,19 | R$ 66.318.441,19 | R$ 0,00 |
| 30 | R$ 29.531.293,65 | R$ 29.531.293,65 | R$ 0,00 |
| 51 | R$ 31.324.092,95 | R$ 31.324.092,95 | R$ 0,00 |
| 52 | R$ 5.166.925,30 | R$ 5.166.925,30 | R$ 0,00 |
| 32 | R$ 1.877.012,00 | R$ 1.877.012,00 | R$ 0,00 |
| 40 | R$ 10.483,64 | R$ 10.483,64 | R$ 0,00 |
| 35 | R$ 1.083.280,71 | R$ 1.083.280,71 | R$ 0,00 |
| Total | R$ 135.311.529,44 | R$ 135.311.529,44 | R$ 0,00 |

A referencia comparada e a do documento fornecido (portal consultado em 19/10/2026, atualizado em 30/09/2026). A coleta atual da API coincidiu com essa referencia; isso nao representa uma nova auditoria independente do HTML do portal em 20/10/2026.

## Conferencia cadastral executada

A configuracao local ainda apontava para `kitana.opencnpj.com`, incompat?vel com a rota usada pelo adaptador atual. As 300 primeiras consultas retornaram "nao encontrado"; esses resultados ficaram pendentes e nao foram tratados como Demais ou ME. A variavel `OPENCNPJ_BASE_URL` do `.env` local foi corrigida para `https://api.opencnpj.org`, conforme `.env.example`, e somente o enriquecimento foi repetido sobre a coleta financeira salva. Esse ajuste local precisa estar presente tambem na configuracao do ambiente que executar a nova rota.

Na consulta de 20/10/2026, os 300 CNPJs tiveram retorno cadastral correspondente: 155 ME, 37 EPP e 108 Demais. Nao restaram pendencias de porte nesta coleta. A concorrencia e limitada a quatro consultas e cada CNPJ e consultado uma unica vez.

| Categoria cadastral atual | Pagamentos |
| --- | ---: |
| ME | R$ 24.265.871,08 |
| EPP | R$ 24.513.550,75 |
| Demais | R$ 86.532.107,61 |
| Sem classificacao / nao CNPJ | R$ 0,00 |
| Total reconciliado | R$ 135.311.529,44 |

Em Consultoria, a fonte retornou Microempresa para ISYSERVICE e SIST e Demais para WESLEY. Os valores ME foram R$ 761.880,06 (61 notas) e R$ 120.000,00 (12 notas), totalizando R$ 881.880,06. A classificacao foi consultada, sem ser deduzida do nome empresarial ou do total esperado. A coincidencia desse subtotal com o relatorio historico nao comprova que o retrato cadastral atual corresponda a referencia usada pelo consultor em todas as naturezas.

O arquivo `.local/tce-pagamentos/acarau.json` guarda os 300 registros de classificacao, seus instantes de consulta, fonte e referencia ausente, alem dos totais financeiros, fornecedores ME e auditoria. O total geral, os sete elementos e o subtotal ME de Consultoria foram reconferidos offline a partir da coleta bruta e dessas classificacoes. Os arquivos locais nao entram no controle de versao.

## Verificacao automatizada

A suite completa foi executada com bancos locais de teste e timeout de conexao de dois segundos: 236 testes, sendo 233 aprovados, dois ignorados por exigirem o piloto real de portes historicos e um erro de importacao no backfill PNCP local, que depende das constantes `PNCP_CONSULTA_BASE_URL` e `PNCP_GESTAO_BASE_URL` removidas da configuracao na branch atual. Os 14 testes novos de pagamentos passaram.

A rota HTTP foi verificada na aplicacao usando a coleta e as classificacoes salvas como substitutos das fontes externas: resposta 200 com os totais financeiros e ME conferidos, respostas 422 para parametros ausentes ou invalidos e resposta 503 para indisponibilidade simulada da fonte. Essa verificacao nao iniciou o servidor nem validou conexoes de banco em uma implantacao. `git diff --check` tambem passou.

## Reproducao

```powershell
.venv/Scripts/python.exe scripts/auditar_pagamentos_tce.py --municipio 004 --exercicio 2025 --referencia-final 2026-09 --salvar-coleta .local/tce-pagamentos/coleta-acarau.json --saida .local/tce-pagamentos/acarau.json
```

A coleta bruta e salva antes da conciliacao, permitindo investigar rejeicoes. Valores decimais sao preservados como texto no arquivo. Para repetir somente a conferencia financeira, sem TCE nem consultas cadastrais:

```powershell
.venv/Scripts/python.exe scripts/auditar_pagamentos_tce.py --municipio 004 --exercicio 2025 --referencia-final 2026-09 --entrada .local/tce-pagamentos/coleta-acarau.json --somente-financeiro --saida .local/tce-pagamentos/financeiro.json
```

O modo financeiro retorna subtotal ME zero com classificacao pendente, pois nao executa consultas de porte; esse zero nao comprova ausencia de ME. Sem `--somente-financeiro`, uma entrada local evita consultas ao TCE, mas os portes sao consultados novamente.

O JSON de saida guarda os portes efetivamente consultados. A funcao pura `resumir_pagamentos` permite reconciliar uma lista de pagamentos ja normalizados com uma classificacao por CNPJ, sem consultar fontes externas. Nao se exige que o porte atual produza o subtotal ME historico de Consultoria de R$ 881.880,06.

## Revalidação e paginação adaptativa (04/11/2026)

O OpenAPI atual do TCE documenta `codigo_orgao` e `codigo_unidade_orcamentaria` como filtros opcionais em `/notas_pagamentos`, junto com `$count` e `$start_index`, sem parâmetro de ordenação. A coleta passou a consultar por órgão e só subdividir por unidade quando um órgão atinge 1.000 registros. A execução de Acaraú/2025 consultou 15 órgãos, registrou 25 unidades disponíveis, planejou e executou 315 consultas, sem subdivisão adicional. Vieram 12.067 notas, sem chaves duplicadas nem pagamentos órfãos; referências documentais seguiram de 2025-01 a 2026-09 com exercício orçamentário 202500.

Essa coleta atual, processada uma vez por nota e sem Câmara/estorno, ainda divergiu da fotografia do portal consultada em 19/10/2026. O total foi R$ 136.213.306,64, diferença de +R$ 901.777,20. Elementos 30, 32, 39 e 52 divergiram; 35, 40 e 51 coincidiram. A composição por elemento e a diferença constam em `conferencia_portal` da resposta e no arquivo local de auditoria. Portanto, o subtotal cadastral ME não é marcado como validado quando o controle financeiro de todos os portes não bate com a referência.

Na classificação cadastral consultada em 04/11/2026, os valores ME por elemento foram: 30 R$ 2.689.620,61; 32 R$ 284.931,90; 35 R$ 881.880,06; 40 R$ 0,00; 51 R$ 6.920.121,87; 52 R$ 490.062,45. Cinco dos seis alvos informados coincidiram; Material de Consumo ficou R$ 23.789,84 acima do alvo de R$ 2.665.830,77. Como a conferência financeira geral não bate e a referência cadastral histórica não está disponível (`referencia_base_cadastral=null`), não ajustamos fornecedor ou pagamento para forçar esse subtotal. A resposta informa `indicador_me_validado=false` nesse cenário.

A evidência bruta está em `.local/tce-pagamentos/coleta-acarau-particionada.json`, e a reconciliação em `.local/tce-pagamentos/resultado-me-particionado.json` (arquivos locais não versionados). A referência oficial da paginação e dos filtros é a [documentação OpenAPI do SIM/TCE-CE](https://api-dados-abertos.tce.ce.gov.br/sim/).

Os testes incluem esse subtotal apenas como exemplo de classificacao hipotetica, alem de meses vazios, mudanca de ano, paginas completas, normalizacao de chaves, duplicidades, orfaos, estornos desconhecidos, documentos invalidos e pendencias cadastrais. Nenhum total de referencia e usado como ajuste dos pagamentos.

## Overviews por pagamentos publicados

A rota `GET /pipeline/tce/overview` (estadual ou municipal por `codigo_municipio`) e `GET /pipeline/tce/overview/municipal` calculam valor financeiro a partir de `valor_nota_pagamento_centavos`, uma vez por chave de nota, nos snapshots mensais `PUBLICADO`. As datas da rota definem a competência documental inicial e o limite mínimo do período; o exercício orçamentário permanece na chave de cada snapshot, de modo que uma nota documentada em 2026 vinculada ao orçamento de 2025 continua incluída.

Para cada exercício orçamentário com snapshots publicados, a rota mantém a competência inicial solicitada e estende automaticamente a competência final até o último snapshot publicado daquele exercício. Portanto, uma chamada com datas de 2025 inclui pagamentos documentados em 2026 que pertençam ao orçamento de 2025, sem mudar os parâmetros usados pelo backend ou frontend. A cobertura precisa ser contínua da competência inicial até a última publicada; lacunas intermediárias continuam causando erro. `competencias_solicitadas` preserva o período da chamada; `competencias_pagamentos_incluidas` e `pagamentos_estendidos_automaticamente` mostram a janela efetivamente usada.

Cada nota de pagamento é ligada ao empenho publicado pela chave natural, que inclui `numero_empenho`, município, exercício, órgão, unidade orçamentária e data do empenho. Quando o mesmo empenho reaparece em snapshots mensais, ele é atribuído à primeira competência documental publicada, que representa sua referência de origem; o pagamento integral fica nessa competência, sem rateio entre snapshots. `data_referencia_doc_pagamento` preserva a referência original da nota para auditoria. Notas sem empenho publicado correspondente permanecem persistidas, mas são ignoradas no cálculo; as notas com empenho publicado continuam sendo consideradas. Só entram notas com `estado_de_estornado=0` e órgão classificado com confiança alta como `EXECUTIVO_MUNICIPAL`; órgãos classificados como Câmara e órgãos incertos ficam fora.

A leitura exige snapshots publicados em todos os meses do intervalo para cada exercício orçamentário que tenha pagamentos publicados, começando no mês mais recente entre o início solicitado e janeiro do próprio exercício. Assim, uma consulta iniciada em 2025 não exige lotes de 2025 para o orçamento de 2026. Se faltar competência dentro da janela aplicável, a rota retorna erro de dados indisponíveis, nunca trata ausência de lote como mês vazio. Para o overview estadual, essa cobertura é exigida em todos os municípios do Ceará cadastrados. Isso preserva o comportamento somente leitura: a rota não inicia ingestão nem consulta TCE ou cadastro externo.

O overview simples considera ME no numerador e agrupa MEI, EPP e DEMAIS em `OUTROS_PORTES`; não identificados permanecem em categoria própria. O overview municipal mostra a distribuição de todos os portes e aceita `company_sizes`. A classificação usa o cache cadastral disponível (retrato atual, não comprovação histórica). Pagamento não classificado continua no total financeiro, mas não entra em ME; portanto, os indicadores de porte dependem da cobertura cadastral.

Nos overviews municipal e estadual, as evoluções contêm 12 pontos para o exercício orçamentário selecionado pelo ano de `data_inicial`. A análise preserva e filtra explicitamente `exercicio_orcamento` em cada pagamento; pagamentos desse orçamento documentados em outros anos continuam no mesmo exercício e são agregados pelo mês documental (`01` a `12`), sem usar o ano documental no eixo. `evolucao_compras_consideradas` soma compras identificadas e ME em cada mês. No overview municipal, `evolucao_compras_por_portes` mostra o percentual ME acumulado sobre o total identificado do exercício, e dezembro coincide com `kpis.percentual_participacao_me`.

O histórico de despesas líquidas permanece disponível nas rotas de despesas que não foram migradas. Overview simples (estadual e municipal), overview municipal por portes, indicadores analíticos principais e KPI mensal de portes já usam pagamentos publicados. Rotas de listagem de empenhos e indicadores explicitamente de empenhado/anulado continuam com suas medidas originais. A validação do Postgres local abaixo é evidência de execução anterior; nesta revisão o Docker não estava acessível para repetir a chamada HTTP.

## Validacao Acaraú no Postgres local (04/11/2026)

A verificacao com snapshots locais confirmou 21 competencias publicadas para 202500, de 2025-01 a 2026-09; havia 11.951 notas, sendo 11.915 com `estado_de_estornado=0` e 36 estornadas. Nao houve estado desconhecido nem chave de empenho orfa. O filtro Prefeitura excluiu o orgao 01 (Camara) e manteve notas somente de orgaos do Executivo classificados com confianca alta.

A regra nominal inicial deixava os orgaos 03 (Controladoria/Ouvidoria, grafado `Controlodaria`) e 10 (`Sec.` de Agronomia) como `REVISAR`. A regra passou a reconhecer o prefixo `CONTROL` e a abreviacao `SEC`; o cadastro Acaraú/2025 foi reclassificado, ficando 15 orgaos com confianca alta, incluindo uma Camara. Com a correcao, a rota passou de 7.920 para 8.187 notas monitoradas e o controle passou de R$ 133.358.028,00 para R$ 134.991.056,93.

Na mesma execucao, os totais por elemento de todos os portes foram: 30 R$ 29.863.810,49; 32 R$ 1.937.198,26; 35 R$ 1.083.280,71; 39 R$ 65.481.674,75; 40 R$ 10.483,64; 51 R$ 31.285.160,58; 52 R$ 5.329.448,50. Contra a referencia do portal, a divergencia total permaneceu em -R$ 320.472,51 (30 +R$ 332.516,84; 32 +R$ 60.186,26; 35 R$ 0,00; 39 -R$ 836.766,44; 40 R$ 0,00; 51 +R$ 38.932,37; 52 -R$ 162.523,20). Portanto, a classificacao dos dois orgaos explicou parte da diferenca, mas a conciliacao financeira ainda falha.

O subtotal ME corrente apos a reclassificacao foi: 30 R$ 2.650.825,02; 32 R$ 284.931,90; 35 R$ 881.880,06; 40 R$ 0,00; 51 R$ 6.881.189,50; 52 R$ 425.012,45. O cadastro historico continua nao comprovado, e as diferencas de Material de Consumo, Obras e Instalacoes e Material Permanente impedem validar todos os seis alvos. A rota retorna `indicador_me_validado=false` quando comparada com a referencia conhecida.
