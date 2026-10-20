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

O endpoint consulta as fontes externas em cada chamada. A execucao integral pode demorar varios minutos; nao usa o snapshot de empenhos nem publica dados em bancos. A coleta bruta pode ser preservada pelo comando de auditoria. Nao foi implementado cache persistente de pagamentos ou de portes para este fluxo.

- `total_me_centavos`, `me_por_elemento_centavos` e `fornecedores_me`: valores para o recorte publico ME.
- `controle_financeiro`: totais antes do porte, por elemento e por categoria exclusiva (ME, EPP, Demais, sem classificacao e documento nao CNPJ).
- `classificacoes`: um cadastro por CNPJ, porte original e normalizado, fonte, instante da consulta, indicador MEI e situacao da classificacao.
- `pendencias`: chave de pagamento, documento original, motivo e valor afetado. Ausencia cadastral nao reduz o controle financeiro.
- `classificacao_completa`: falsa quando existem pagamentos com classificacao pendente ou documento nao CNPJ valido. Um subtotal parcial nao deve aparecer como definitivo.
- `referencia_base_cadastral: null` e `porte_historico_comprovado: false`: o adaptador atual consulta o retrato atual e nao tem referencia mensal documentada da base. Nao presume que o porte consultado era o vigente em 2025.

ME significa porte cadastral `01`, sem exclusao adicional de MEI. Nome empresarial, opcao pelo Simples e valores recebidos nao determinam porte. O CNPJ retornado pela fonte deve corresponder ao solicitado. Documentos incompletos, invalidos ou de outro tipo ficam rastreaveis.

## Regras financeiras

O cadastro `orgaos` identifica a Camara por tipo `02` ou nome contendo Camara, sem fixar um numero universal. Orgao ausente do cadastro ou impossibilidade de identificar a Camara impedem a conciliacao.

Coletam-se os doze meses de empenhos e todos os meses de pagamentos ate a referencia final. Cada mes reinicia a paginacao em zero; falhas HTTP, respostas sem uma lista valida de `elements` e itens malformados nao encerram meses como vazios. O exercicio orcamentario permanece fixo, inclusive em referencias posteriores.

O relacionamento usa municipio, exercicio, orgao, unidade orcamentaria, data de emissao do empenho e numero do empenho. Datas e espacos externos sao normalizados; zeros dos identificadores sao preservados. A referencia documental nao integra a chave. A chave de pagamento acrescenta numero do subempenho e numero da nota.

Qualquer chave repetida, mesmo aparentemente identica, interrompe o calculo com a chave na mensagem. Pagamentos sem empenho e estados de estorno diferentes de `0` e `1` tambem impedem a soma. Nao se escolhe arbitrariamente uma versao conflitante.

Somam-se centavos de `valor_nota_pagamento`, nunca o valor empenhado. Excluem-se a Camara, o estado de estorno `1` e elementos fora de `39`, `30`, `51`, `52`, `32`, `40`, `35`. O elemento corresponde as posicoes cinco e seis do codigo de oito digitos.

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

Os testes incluem esse subtotal apenas como exemplo de classificacao hipotetica, alem de meses vazios, mudanca de ano, paginas completas, normalizacao de chaves, duplicidades, orfaos, estornos desconhecidos, documentos invalidos e pendencias cadastrais. Nenhum total de referencia e usado como ajuste dos pagamentos.
