# Comandos de ingestão do TCE-CE

Execute os comandos na raiz do repositório da VPS. Use sempre `compose.production.yaml` para gravar no mesmo banco utilizado pela API pública `pipeline.monitorame.tech`.

Substitua `2025` pelo exercício orçamentário desejado. O exercício pode ser histórico, por exemplo `2024`, e não é a mesma coisa que a competência documental dos pagamentos.

## Ceará inteiro: empenhos, anulações e pagamentos

Para coletar empenhos e anulações do exercício informado nos 184 municípios e, em seguida, pagamentos do mesmo exercício:

```bash
docker compose -f compose.production.yaml run --rm api python -m scripts.ingestir_tce_ce --ano 2025
```

O fluxo de pagamentos começa em janeiro do exercício orçamentário e vai até a competência do mês corrente em `America/Fortaleza`. Por exemplo, executado em novembro de 2026 com `--ano 2025`, consulta pagamentos de `2025-01` a `2026-11`, mantendo `exercicio_orcamento=202500`.

O endpoint de origem trabalha por competência mensal; portanto, o mês corrente é ingerido integralmente, sem corte pelo dia da execução. A concorrência de pagamentos é limitada a dois workers por padrão. Para usar apenas um:

```bash
docker compose -f compose.production.yaml run --rm api python -m scripts.ingestir_tce_ce --ano 2025 --workers 1
```

## Ceará inteiro: somente pagamentos

Quando empenhos e anulações já estão publicados, use:

```bash
docker compose -f compose.production.yaml run --rm api python -m scripts.ingestir_tce_ce --ano 2025 --somente-pagamentos
```

Esse modo não reingere empenhos nem anulações. Percorre os 184 municípios e consulta janeiro do exercício informado até o mês corrente, mantendo o exercício orçamentário original. Se o catálogo municipal do banco estiver incompleto, atualiza o catálogo antes da coleta.

## Ceará inteiro: somente empenhos e anulações

Para executar apenas a ingestão anual de empenhos e anulações:

```bash
docker compose -f compose.production.yaml run --rm api python scripts/ingestir_tce_despesas.py --ano 2025 --todos-municipios
```

## Um município: pagamentos

Para ingerir pagamentos de um município, escolha explicitamente o intervalo de competências documentais:

```bash
docker compose -f compose.production.yaml run --rm api python scripts/ingestir_tce_pagamentos.py --codigo-municipio 004 --exercicio-orcamento 2025 --competencia-inicial 2025-01 --competencia-final 2026-11
```

Troque `004` pelo código TCE desejado e atualize a competência final para o mês da execução. A opção `--exercicio-orcamento 2025` mantém as notas vinculadas ao orçamento de 2025 mesmo quando a referência documental é de 2026.

## Reexecução, snapshots e resultados

Cada lote de pagamentos é identificado por município, exercício orçamentário e competência. Reexecutar uma competência publica um novo snapshot e substitui o anterior; não duplica as notas publicadas. O snapshot substituído fica retido por sete dias. Uma falha de coleta ou validação preserva o snapshot publicado anterior.

Os comandos imprimem progresso e um resumo final. Código `0` indica conclusão sem falhas; código `1` indica erro de catálogo/configuração; código `2` indica falha ou rejeição em uma ou mais competências. Confira o resumo antes de usar a cobertura nos indicadores.

Para o cálculo, classifique os órgãos por município/exercício quando necessário:

```bash
docker compose -f compose.production.yaml run --rm api python scripts/sincronizar_classificacoes_orgaos_tce.py --municipio 004
```

Sem `--municipio`, o script sincroniza os escopos disponíveis para todos os municípios.
