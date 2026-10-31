# Validar PNCP no Docker local

Este roteiro sobe um PostgreSQL e uma API isolados, persiste no máximo três editais de uma data/modalidade e não avança cursores. O enriquecimento consulta itens, resultados e metadados de documentos; não baixa arquivos.

## 1. Preparar o ambiente isolado

Abra o Docker Desktop. No Git Bash, entre na raiz do repositório:

```bash
cd /c/Users/praze/www/monitoraME-pipeline-api
[ -f .env ] || cp .env.example .env
[ -f .env.pncp-test ] || cp .env.example .env.pncp-test
```

Configure portas e volume exclusivos para o teste:

```bash
sed -i 's/^POSTGRES_PORT=.*/POSTGRES_PORT=55432/' .env.pncp-test
sed -i 's/^API_PORT=.*/API_PORT=18000/' .env.pncp-test
sed -i 's/^POSTGRES_VOLUME_NAME=.*/POSTGRES_VOLUME_NAME=monitorame_pncp_validation_data/' .env.pncp-test
```

Esse nome de volume separa os dados deste roteiro do volume padrão. O Compose ainda lê `.env` como `env_file` da aplicação; as configurações interpoladas do teste, incluindo usuário e banco, vêm de `--env-file .env.pncp-test` e substituem as URLs do processo.

## 2. Subir e aplicar migrations

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation up -d --build \
  api pncp-worker-coleta pncp-worker-normalizacao pncp-worker-enriquecimento
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation ps -a
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation logs migrate
```

O serviço `api` espera `migrate` concluir com sucesso. Confirme que `migrate` saiu com código 0 e que os quatro serviços estão saudáveis ou em execução antes da coleta.

Se a base de teste já existia antes da correção das URLs, atualize a imagem e aplique a migration corretiva antes de consultar:

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation build api
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation run --rm migrate
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation up -d --no-deps --force-recreate api
```

## 3. Coletar no máximo três editais

Escolha uma data recente com publicações na modalidade desejada. Por exemplo, `6` costuma ser usada para pregão eletrônico; se não houver registros, tente outra data ou modalidade.

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation build api
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation run --rm api \
  python scripts/pncp_operacao.py coletar-amostra \
  --data 2026-10-30 --modalidade 6 --limite 3
```

O PNCP rejeita páginas menores que 10. O comando consulta a primeira página com tamanho de pelo menos 10, mas persiste no máximo o valor de `--limite` (três neste exemplo) e imprime os `external_id`. Não confirma watermark; se a amostra vier vazia, não houve escrita e você pode tentar outra data.

## 4. Normalizar e agendar enriquecimento

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation run --rm api \
  python scripts/pncp_operacao.py normalizar --limite 10
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation run --rm api \
  python scripts/pncp_operacao.py status
```

Copie um `external_id` da saída da coleta e agende apenas esse edital:

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation run --rm api \
  python scripts/pncp_operacao.py agendar-enriquecimento \
  --id 'COLE_O_EXTERNAL_ID'
```

O worker `pncp-worker-enriquecimento` processa os jobs de detalhe, itens e documentos. Os resultados são consultados por item durante o job de itens. Acompanhe o worker e o estado da fila:

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation logs -f pncp-worker-enriquecimento
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation run --rm api \
  python scripts/pncp_operacao.py status
```

Para processar jobs manualmente em vez de manter o worker ligado, rode:

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation run --rm api \
  python scripts/pncp_operacao.py enriquecer --limite 10
```

## 5. Conferir e encerrar

Use o mesmo `external_id` retornado na coleta. A rota de detalhe é somente leitura:

```bash
ID='COLE_O_EXTERNAL_ID'
curl "http://localhost:18000/pncp/editais/$ID"
```

Confira `itens_estruturados`, `lotes_identificados`, `resultados_por_item`, `documentos_manifesto` e `cobertura_participacao`. `lotes_identificados` pode ficar vazio porque o contrato oficial de itens não documenta chave de lote. Para as contagens gerais, consulte `http://localhost:18000/health/pncp`.

Encerre mantendo o banco isolado para a próxima execução:

```bash
docker compose --env-file .env.pncp-test -p monitorame-pncp-validation down
```

Não acrescente `-v` se quiser preservar essa base local.
