# Comandos PNCP: editais com propostas abertas

Este guia cobre o Compose local (`compose.yaml`) e a operação na VPS
(`compose.production.yaml`). A coleta usa o endpoint de propostas abertas e
processa uma modalidade por vez. O comando acompanha os jobs e aguarda a
normalização dos RAW coletados.

## Próxima execução completa no ambiente local

Reconstrua e inicie API e workers PNCP necessários:

```bash
docker compose -f compose.yaml up -d --build api pncp-worker-coleta pncp-worker-normalizacao pncp-worker-enriquecimento
```

Escolha um grupo ou combine ambos. Cada modalidade termina (incluindo retries)
antes do início da próxima:

```bash
# Concorrências: 4, 5 e 16
docker compose -f compose.yaml exec api pncp coletar-propostas-abertas --concorrencia

# Pregões: 6, 7 e 17
docker compose -f compose.yaml exec api pncp coletar-propostas-abertas --pregao

# Concorrências e pregões na mesma execução sequencial
docker compose -f compose.yaml exec api pncp coletar-propostas-abertas --concorrencia --pregao
```

Também é possível escolher códigos específicos repetindo `--modalidade`, por
exemplo `--modalidade 4 --modalidade 6`. Não combine `--modalidade` com
`--concorrencia` ou `--pregao`. O acompanhamento fica no primeiro plano; o
timeout padrão é de 12 horas e pode ser alterado com `--timeout-minutos`.

## Normalizar e enriquecer dados já coletados

O comando de coleta acima aguarda a normalização dos registros RAW que ele
coletou. Se os RAW já estão no banco, suba os workers e acompanhe até não haver
jobs `normalize` pendentes, em execução ou aguardando retry:

```bash
docker compose -f compose.yaml up -d api pncp-worker-normalizacao pncp-worker-enriquecimento
docker compose -f compose.yaml exec api pncp status
docker compose -f compose.yaml logs -f pncp-worker-normalizacao
```

Depois de normalizar, agende enriquecimento para as oportunidades já
normalizadas. Ajuste o limite para ser maior que a quantidade de oportunidades
mostrada por `pncp status`:

```bash
docker compose -f compose.yaml exec api pncp agendar-enriquecimento --todos-normalizados --limite 100000
docker compose -f compose.yaml logs -f pncp-worker-enriquecimento
```

O primeiro comando agenda os jobs de enriquecimento; o worker os processa em
segundo plano. Acompanhe o avanço e os estados finais com:

```bash
docker compose -f compose.yaml exec api pncp status
docker compose -f compose.yaml logs --since=10m pncp-worker-enriquecimento
```

O enriquecimento consulta detalhes, itens e documentos conforme a política
configurada. Falhas ficam registradas nos jobs para diagnóstico ou replay.

## Enriquecimento cadastral do TCE-CE

Enriqueça fornecedores dos lotes publicados de um ano, sem repetir a coleta:

```bash
docker compose -f compose.yaml run --rm api tce-enriquecer --ano 2025
```

Para limitar a um município, acrescente `--codigo-municipio 010`. O ano deve
usar quatro dígitos. O comando consulta CNPJs distintos dos lotes publicados,
reutiliza o cache e busca dados no OpenCNPJ quando necessário.

Para enriquecer todos os lotes publicados, independentemente do ano:

```bash
docker compose -f compose.yaml run --rm api tce-enriquecer --todos-disponiveis
```

Também aceita `--codigo-municipio 010` para limitar o processamento. O comando
imprime progresso e resumo; código de saída `0` indica sucesso, `2` indica
CNPJs sem porte cadastral disponível, e `1` indica falha operacional.

Na VPS, use os comandos equivalentes pelo serviço `api`:

```bash
docker compose -f compose.production.yaml exec api tce-enriquecer --ano 2025
docker compose -f compose.production.yaml exec api tce-enriquecer --todos-disponiveis
```

## Comandos equivalentes na VPS

Na pasta do projeto na VPS, inicie os workers e execute a coleta sequencial:

```bash
docker compose -f compose.production.yaml up -d pncp-worker-coleta pncp-worker-normalizacao pncp-worker-enriquecimento
docker compose -f compose.production.yaml exec api pncp coletar-propostas-abertas --concorrencia --pregao
```

Para dados já coletados, consulte o estado, aguarde a normalização pendente e
agende o enriquecimento:

```bash
docker compose -f compose.production.yaml exec api pncp status
docker compose -f compose.production.yaml exec api pncp agendar-enriquecimento --todos-normalizados --limite 100000
docker compose -f compose.production.yaml logs -f pncp-worker-normalizacao pncp-worker-enriquecimento
```

## Reset opcional do banco local

**Apaga todos os dados do PostgreSQL local**, incluindo PNCP, TCE e jobs. Não
afeta a VPS e preserva o volume de cache do modelo NLP. Use somente quando
quiser começar o teste local com um banco vazio:

```bash
docker compose -f compose.yaml down && docker volume rm monitorame_pipeline_api_postgres_data && docker compose -f compose.yaml up -d --build api pncp-worker-coleta pncp-worker-normalizacao pncp-worker-enriquecimento
```

Se `POSTGRES_VOLUME_NAME` foi alterado no `.env`, substitua o nome do volume
acima pelo valor configurado.
