# Worker isolado de embeddings

O serviço `matching-nlp-worker` executa no Compose junto ao PostgreSQL do
Pipeline, na rede privada padrão. Ele não tem rota HTTP, não consulta a API
PNCP e não usa o banco do Backend. Instale os pesos do modelo no primeiro
startup; portanto, o worker precisa de saída de rede para o Hugging Face quando
o cache local ainda não contém a revisão configurada.

## Fluxo

1. Normalização do edital e enriquecimento dos itens sincronizam os segmentos
   textuais afetados em `nlp_procurement_segments` na mesma transação.
   Se a tabela ainda estiver vazia, o primeiro startup prepara o corpus existente
   em páginas de 200 oportunidades antes de começar a gerar vetores.
2. O worker procura segmentos `READY` sem vetor para o `text_hash` e
   `model_version` ativos e cria jobs `nlp_embedding` em `ingestion_jobs`.
3. Jobs são reivindicados com `FOR UPDATE SKIP LOCKED`, lease e token. Uma
   inferência por processo limita memória e concorrência.
4. Antes de gravar, o worker relê o segmento. Se o texto mudou enquanto o
   modelo processava, descarta o vetor antigo e conclui o job; o próximo ciclo
   agenda o hash atual.
5. O vetor é inserido por chave idempotente `(origem, edital, segmento,
   text_hash, model_version)`. Reexecução do mesmo job não duplica registros.

Os vetores ficam em `vector(384)` do pgvector como números normalizados de dimensão 384. A chave
primária preserva vetores de hashes e versões anteriores, enquanto a FK ao
segmento mantém a identidade de origem e remove vetores quando o segmento é
removido. Consumidores devem sempre selecionar o vetor cujo `text_hash`
corresponde ao segmento atual e cuja `model_version` está configurada.

## Operação

```sh
docker compose -f compose.production.yaml up -d --build matching-nlp-worker
docker compose -f compose.production.yaml logs -f matching-nlp-worker
```

`matching-nlp-worker` usa `DATABASE_URL` e `LOG_DATABASE_URL` do ambiente do
Pipeline. O padrão é 2 GiB, 1,5 CPU, CPU-only, batch 1 e um processo. Ajuste os
limites no `.env` da VPS conforme a memória disponível. Fixe
`NLP_EMBEDDING_REVISION` em um SHA do repositório do modelo. O padrão é
`fd1525a9fd15316a2d503bf26ab031a61d056e98`, a revisão usada na validação
local. A revisão participa de `model_version`, de modo que uma atualização do
modelo agenda vetores novos sem sobrescrever os anteriores.

Falhas transitórias usam o retry exponencial/jitter da fila compartilhada.
Tentativas esgotadas ficam `FAILED`; use o procedimento existente de replay
operacional (`scripts/replay_pncp_jobs.py`) para reativar jobs selecionados.
Jobs ativos são deduplicados por hash da identidade completa. O worker só
recupera leases do tipo `nlp_embedding`, sem alterar leases de coleta,
normalização ou enriquecimento.
