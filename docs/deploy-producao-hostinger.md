# Tutorial: deploy do pipeline MonitoraME na VPS Hostinger

Este tutorial prepara o pipeline analítico para ficar acessível em
`https://pipeline.monitorame.tech`, atrás do Traefik. O serviço também participa de
uma rede Docker interna para chamadas do backend.

## O que você precisa

- Uma VPS Hostinger com Docker Compose e Traefik ativos.
- Acesso de administrador à VPS e ao repositório no GitHub.
- Uma chave SSH dedicada para o GitHub Actions, com a chave pública autorizada
  na VPS.

## 1. Prepare o ambiente de produção na VPS

O projeto usa um arquivo `.env` privado na VPS para configurar o PostgreSQL e a
aplicação. Defina nele `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` e
`CORS_ALLOWED_ORIGINS=https://api.monitorame.tech,https://monitorame.amontadavalley.com.br`,
além das variáveis de execução necessárias ao pipeline. Mantenha esse arquivo
fora do GitHub; o workflow envia somente arquivos versionados e preserva o
`.env`.

O PostgreSQL não publica porta no host. Os dados ficam no volume persistente
`monitorame_pipeline_api_postgres_data`.

## 2. Configure o ambiente `production` no GitHub

No repositório, crie ou abra o GitHub Environment chamado `production` e
configure estas variáveis:

| Variável | Valor |
| --- | --- |
| `VPS_HOST` | IP ou hostname SSH da VPS. |
| `VPS_USER` | Usuário SSH autorizado a executar o deploy. |
| `VPS_HOST_KEY_FINGERPRINT` | Impressão digital ED25519 da VPS, obtida e conferida pelo administrador. |

Adicione `VPS_SSH_PRIVATE_KEY` como segredo do ambiente. A chave pública
correspondente deve estar autorizada na VPS. O workflow confere a impressão
digital do host antes de abrir a conexão SSH.

## 3. Aguarde a validação do commit

O workflow **Production readiness** roda em pushes e pull requests. Ele prepara
um PostgreSQL temporário, compila os módulos Python, aplica as migrations,
executa os testes unitários, valida o Compose de produção e constrói a imagem.
Prossiga quando o status estiver verde para o commit que será publicado.

## 4. Inicie o deploy

1. Abra **Actions** no GitHub.
2. Selecione **Production readiness**.
3. Inicie o workflow no branch padrão do repositório.
4. Marque `deploy_production` e execute o workflow.

O job envia o commit validado à VPS, constrói uma imagem identificada pelo SHA,
aguarda o PostgreSQL, aplica as migrations e inicia o pipeline. A validação de
saúde acessa `/health` dentro do próprio container; não depende de domínio
público.

## 5. Conecte o backend quando ele estiver hospedado

O serviço `api` do pipeline possui uma rota HTTPS pública em
`pipeline.monitorame.tech` e também participa da rede Docker
`monitorame_backend_internal`, com alias DNS `pipeline-api`. No Compose do
backend, declare essa rede como externa e conecte somente o serviço backend a
ela:

```yaml
services:
  backend:
    networks:
      - default
      - pipeline_internal

networks:
  pipeline_internal:
    external: true
    name: monitorame_backend_internal
```

O backend pode então chamar `http://pipeline-api:8000`; nessa chamada, envie o
cabeçalho `Origin: https://api.monitorame.tech`. O DNS do subdomínio `pipeline`
deve apontar para o IP da VPS. `api.monitorame.tech` permanece reservado ao
backend. A porta `8000` não é publicada no host; o Traefik é o único caminho
público até o container.

Configure as origens CORS permitidas como
`https://api.monitorame.tech` e `https://monitorame.amontadavalley.com.br`.
Requisições de dados sem `Origin` ou com outra origem recebem `403`. `/health`
aceita chamadas sem origem para permitir o healthcheck do container.

Essa verificação limita o uso normal por navegadores, mas não autentica
dispositivos: clientes como `curl` podem forjar o cabeçalho `Origin`. Para
impedir chamadas diretas de clientes externos, o pipeline precisará ficar
privado e o backend deverá autenticá-las.

## Atualizações futuras

Preserve o `.env` privado da VPS e o volume
`monitorame_pipeline_api_postgres_data`. Não use `docker compose down -v` em
produção.
