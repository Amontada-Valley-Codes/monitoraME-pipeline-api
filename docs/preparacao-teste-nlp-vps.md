# Preparação do PNCP e Matching NLP para teste na VPS

## Objetivo

Executar uma coleta pequena e manual de contratações que ainda recebem
propostas, normalizar RAW automaticamente, enriquecer em paralelo sob o limite
global do PNCP e executar matching num container NLP sob demanda. A coleta não
é iniciada por cron ou por uma chamada de usuário.

## Coleta manual, limitada e somente de oportunidades abertas

O PNCP publica a consulta `GET /v1/contratacoes/proposta` para contratações com
recebimento de propostas aberto ([Swagger oficial de consulta PNCP](https://pncp.gov.br/api/consulta/swagger-ui/index.html)). O novo comando usa esse
endpoint, grava apenas até o limite escolhido e não avança cursor/checkpoint.
Cada registro RAW e o respectivo job de normalização são gravados na mesma
transação. Limites do comando: 1–200 contratações, 10–50 itens por página e
1–20 páginas.

No shell do container Pipeline, onde o utilitário `pncp` está instalado, a
coleta pode ser iniciada sem Docker CLI:

```sh
pncp coletar-amostra-abertas --limite 20 --tamanho-pagina 20 --max-paginas 10
```

Opcionalmente, restrinja a uma modalidade com `--modalidade 6`. O comando
imprime quantidade persistida, páginas lidas e total de normalizações
agendadas. Ele precisa de `DATABASE_URL` configurada e conectividade de saída
HTTPS ao PNCP. A coleta termina em erro quando a fonte estiver indisponível; o
limite evita baixar a listagem inteira por engano.

O prompt `app@<container-id>:~$` é um shell dentro do container. A ausência do
executável `docker` ali é esperada: o daemon Docker e o Compose são operados no
host da VPS ou pelo painel que implantou o Compose. Não instale nem exponha o
socket Docker no container Pipeline para contornar isso.

## Normalização automática e ordem de retries

A persistência RAW agenda `normalize` na mesma transação. O Compose de produção
agora inclui `pncp-worker-normalizacao`, que reivindica esses jobs
automaticamente. A fila prioriza os jobs `PENDING` de normalização sobre
`RETRY_WAIT`; quando jobs bem-sucedidos pendentes forem drenados, retries
vencidos voltam a ser tentados. Tentativas futuras respeitam `next_run_at`, e
falhas estruturais continuam em quarentena conforme as regras existentes.

Para conferir o estado no container Pipeline:

```sh
pncp status
```

Jobs de normalização ficam no PostgreSQL persistente. O worker recupera leases
expirados e não precisa de Docker CLI para processar o RAW já enfileirado.

## Enriquecimento mais rápido com proteção à fonte

O worker de enriquecimento aceita de 1 a 5 slots concorrentes, configurados por
`PNCP_ENRICHMENT_WORKERS` (padrão 3). Cada slot tem uma sessão HTTP própria.
Todos continuam usando o limitador coordenado no PostgreSQL, que regula a
frequência, a concorrência e o circuit breaker em conjunto com os demais
clientes PNCP. O número de slots não aumenta o teto de requisições por segundo.
O lease foi ampliado para acomodar enriquecimentos de itens com várias páginas.

No `.env` de produção, mantenha o rate limit controlado, por exemplo:

```dotenv
PNCP_ENRICHMENT_WORKERS=3
PNCP_RATE_LIMIT_REQUESTS_PER_SECOND=2
PNCP_CONCURRENCY_INITIAL=2
PNCP_CONCURRENCY_MAX=5
```

O worker não agenda novos editais por conta própria. Ele só processa jobs
`enrich` já presentes na fila. Ajuste o número de slots após observar duração,
429, espera e circuit breaker nos logs.

## Container NLP e rede Docker

O serviço `matching-nlp-worker` permanece no mesmo Compose e conecta-se à rede
privada padrão do Pipeline para acessar o PostgreSQL Analytics. Em produção,
ele também se associa à rede externa `monitorame_backend_internal`, com alias
DNS `matching-nlp`. Isso prepara a conectividade entre stacks Docker. O processo
atual é um worker, sem servidor HTTP; o alias de rede não significa que exista
endpoint de matching ou integração Backend ativa.

O perfil Compose `matching` impede que o worker seja iniciado pelo `up` padrão.
Ao executar, ele só carrega os pesos quando existe batch de empresa pronto; se
não houver batch, não varre oportunidades, não agenda embeddings e não carrega
o modelo. `--drain` faz o processo terminar após a fila ficar ociosa, liberando
CPU e memória do container. O cache de pesos permanece no volume nomeado para
evitar novo download em cada execução.

Para executar um matching com fixtures já disponíveis no host, primeiro
enfileire o batch e depois execute o worker de drenagem. Um operador com Docker
CLI executa no diretório do Compose:

```sh
batch_id=$(python -c 'import uuid; print(uuid.uuid4())')
docker compose -f compose.production.yaml --profile matching run --rm \
  --volume "$PWD/tests/nlp/fixtures:/fixtures:ro" \
  matching-nlp-worker python -m app.nlp.cli enqueue-matching \
  --fixtures /fixtures/companies-reais-teste.json --batch-id "$batch_id"
docker compose -f compose.production.yaml --profile matching run --rm \
  matching-nlp-worker
docker compose -f compose.production.yaml --profile matching run --rm \
  matching-nlp-worker python -m app.nlp.cli matching-status --batch-id "$batch_id"
```

O primeiro comando persiste o snapshot e job no Analytics; o segundo gera os
embeddings faltantes e processa o batch; o terceiro consulta o status. Use o
fixture de oito perfis para limitar o primeiro ensaio. Não execute `docker` de
dentro do shell `app@<container-id>`.

## Implantação e verificação operacional

Depois de publicar a nova imagem e o Compose, o operador do host confere a
topologia e sobe os workers necessários:

```sh
docker compose -f compose.production.yaml up -d --build
docker compose -f compose.production.yaml ps
docker network inspect monitorame_backend_internal
```

O Compose do Pipeline cria `monitorame_backend_internal`; conecte o serviço
Backend a esse nome como rede externa no Compose dele. A porta do PostgreSQL
Analytics continua sem publicação externa. O worker de coleta não agenda
buscas automaticamente; a coleta de teste acima é iniciada explicitamente
dentro do container Pipeline.

Verifique os logs dos serviços `pncp-worker-normalizacao` e
`pncp-worker-enriquecimento`, além de `pncp status`. Acompanhe RAM/CPU no host
Docker durante o matching; não considere limites configurados como medição de
desempenho real da VPS. Esta configuração prepara o deploy, mas não comprova
execução na VPS até o operador publicar e validar a imagem no ambiente real.
