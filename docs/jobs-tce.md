# Jobs de ingestão do TCE-CE

Este guia mostra como o pipeline agenda, executa e recupera os jobs do TCE-CE. A fila fica em `ingestion_jobs`, no PostgreSQL principal, e é processada pelos serviços `tce-scheduler` e `tce-worker`.

## Visão geral

O scheduler semanal começa por um job `tce_catalogo`. Esse job sincroniza os municípios do IBGE e do TCE e só expande o lote quando a cobertura do Ceará está válida, com os 184 municípios esperados. A expansão cria jobs de despesas e pagamentos por município e competência, além de jobs de fornecedores por exercício.

Competências ainda sem lote publicado são agendadas. As competências recentes também entram no período de overlap para capturar atualizações. O agendamento é idempotente por lote e chave de deduplicação.

```mermaid
flowchart TD
    A[Agendador semanal] --> B[Agenda tce_catalogo]
    B --> C[Sincroniza catalogo IBGE e TCE]
    C --> D{184 municipios validos?}
    D -- Nao --> E[Encerra sem expandir o lote]
    D -- Sim --> F[Consulta competencias ja publicadas]
    F --> G[Agenda tce_despesas por municipio e competencia]
    F --> H[Agenda tce_pagamentos por municipio, exercicio e competencia]
    F --> I[Agenda tce_fornecedores por exercicio]
    G --> J[Fila PostgreSQL: ingestion_jobs]
    H --> J
    I --> J
    J --> K[Workers reservam jobs elegiveis]
    K --> L[Coleta, valida e persiste lote]
    L --> M{Resultado}
    M -- Publicado --> N[SUCCEEDED]
    M -- Erro transitorio --> O[RETRY_WAIT com proxima execucao]
    M -- Erro permanente ou tentativas esgotadas --> P[FAILED]
    O --> J
```

## Tipos de job

| Tipo | Unidade de trabalho | O que executa |
|---|---|---|
| `tce_catalogo` | Lote semanal | Sincroniza e valida o catálogo municipal; cria os jobs do lote após validação. |
| `tce_despesas` | Município e competência (`YYYY-MM`) | Coleta despesas, empenhos e anulações e tenta publicar o snapshot da competência. |
| `tce_pagamentos` | Município, exercício orçamentário e competência | Coleta e publica o snapshot mensal de pagamentos. |
| `tce_fornecedores` | Exercício e lote semanal | Enriquece os fornecedores encontrados em dados publicados, após a conclusão dos jobs de ingestão do lote. |

Os jobs de despesas e pagamentos podem executar em paralelo. O enriquecimento de fornecedores verifica se ainda há jobs ativos de despesas ou pagamentos naquele lote; se houver, ele é adiado. Essa barreira coordena o enriquecimento, mas não ordena as competências de despesas entre si.

## Reserva, tentativas e leases

O worker reserva um job elegível em uma transação curta usando `FOR UPDATE SKIP LOCKED`. A consulta externa e o processamento ocorrem depois da reserva. O job passa a `RUNNING`, recebe `lease_token` e prazo de lease, e o worker renova o lease enquanto executa.

Jobs só são reivindicados enquanto `attempts < max_attempts`. Falhas transitórias entram em `RETRY_WAIT` com backoff. Se o lease expirar, o recuperador registra o evento de lease: jobs ainda elegíveis voltam para `PENDING`; jobs que já atingiram o limite terminam em `FAILED`. Falhas finais preservam o erro e o contexto para diagnóstico.

```mermaid
stateDiagram-v2
    [*] --> PENDING: agendado
    PENDING --> RUNNING: reserva / tentativa incrementada
    RETRY_WAIT --> RUNNING: retry devido e tentativas disponiveis
    RUNNING --> SUCCEEDED: lote publicado
    RUNNING --> RETRY_WAIT: erro transitorio / dependencia aguardada
    RUNNING --> FAILED: erro permanente ou limite atingido
    RUNNING --> PENDING: lease expirou e ainda ha tentativas
    RUNNING --> FAILED: lease expirou no limite
    FAILED --> PENDING: replay explicito
```

O lease e o token impedem que um worker atrasado conclua um job depois de outro processo tê-lo recuperado. Eventos como `STARTED`, `SUCCEEDED`, `RETRY_WAIT`, `FAILED`, `LEASE_EXPIRED`, `LEASE_EXPIRED_FAILED` e `REPLAYED` mantêm o histórico das transições.

## Dependência temporária de empenhos

Uma competência pode conter uma anulação cujo empenho correspondente ainda não foi publicado em outra competência. A validação retorna `ANULACAO_ORFA`. Como as competências podem ser processadas em paralelo, o worker trata esse diagnóstico como dependência potencialmente temporária:

1. Mantém o diagnóstico e as chaves dos empenhos ausentes da rejeição.
2. Aguarda 15 minutos antes da nova tentativa.
3. Antes de consultar o TCE outra vez, verifica no banco se cada empenho referenciado já consta em lote publicado.
4. Se ainda faltar alguma chave, registra o retry por dependência e aguarda, respeitando `max_attempts`.
5. Se as chaves estiverem publicadas, repete a ingestão da competência. Se o limite se esgotar com chaves ausentes, encerra em `FAILED`, preservando a rejeição original e as chaves faltantes.

```mermaid
flowchart TD
    A[Job tce_despesas executa] --> B{Validacao publicou o lote?}
    B -- Sim --> C[SUCCEEDED; snapshot publicado]
    B -- Nao --> D{Rejeicao inclui ANULACAO_ORFA?}
    D -- Nao --> E[Erro final ou retry conforme classificacao]
    D -- Sim --> F[Carrega chaves da rejeicao anterior]
    F --> G[Consulta empenhos em lotes publicados]
    G --> H{Todas as chaves estao publicadas?}
    H -- Sim --> I[Repete coleta e validacao]
    I --> B
    H -- Nao, ha tentativas --> J[Registra diagnostico e aguarda 15 minutos]
    J --> K[RETRY_WAIT]
    K --> A
    H -- Nao, limite atingido --> L[FAILED preservando diagnostico e lote anterior]
```

Uma falha de coleta ou validação não substitui o lote publicado anteriormente. Indicadores continuam considerando apenas lotes `PUBLICADO`.

## Replay explícito

Jobs `FAILED` não são reabertos automaticamente. Primeiro consulte a fila para identificar o ID e o diagnóstico:

```bash
docker compose -f compose.production.yaml run --rm api python scripts/tce_jobs.py status
```

Depois, reative somente o job escolhido:

```bash
docker compose -f compose.production.yaml run --rm api python scripts/tce_jobs.py replay --job-id 123
```

O replay é registrado como evento e reinicia o contador operacional de tentativas. Evite replay enquanto a dependência de empenho estiver ausente, pois a nova execução pode encontrar o mesmo diagnóstico.

## Executar e acompanhar os serviços

O Compose mantém scheduler e worker em processos separados, compartilhando o banco principal. Para iniciar ou conferir os serviços:

```bash
docker compose -f compose.production.yaml up -d tce-scheduler tce-worker
docker compose -f compose.production.yaml ps tce-scheduler tce-worker
docker compose -f compose.production.yaml logs -f tce-scheduler tce-worker
```

O scheduler avalia a agenda semanal de domingo às 02:00 no fuso `America/Fortaleza` e também recupera a última execução devida após reiniciar. O worker verifica leases expirados periodicamente, reserva jobs com concorrência configurada e processa a fila continuamente. A API e os indicadores leem os snapshots publicados; uma consulta de leitura não inicia uma coleta.
