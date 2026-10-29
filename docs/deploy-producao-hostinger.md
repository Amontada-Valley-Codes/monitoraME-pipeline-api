# Tutorial: deploy do pipeline MonitoraME na VPS Hostinger

Este tutorial prepara o pipeline analítico para funcionar como serviço interno.
O domínio `api.monitorame.tech` fica reservado ao backend público, que será
hospedado separadamente. O pipeline não recebe tráfego do navegador nem possui
rota pública no Traefik.

## O que você precisa

- Uma VPS Hostinger com Docker Compose e Traefik ativos.
- Acesso de administrador à VPS e ao repositório no GitHub.
- Uma chave SSH dedicada para o GitHub Actions, com a chave pública autorizada
  na VPS.

## 1. Prepare o ambiente de produção na VPS

O projeto usa um arquivo `.env` privado na VPS para configurar o PostgreSQL e a
aplicação. Defina nele `POSTGRES_USER`, `POSTGRES_PASSWORD` e `POSTGRES_DB`, além
das variáveis de execução necessárias ao pipeline. Mantenha esse arquivo fora
do GitHub; o workflow envia somente arquivos versionados e preserva o `.env`.

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

O serviço `api` do pipeline participa da rede Docker `monitorame_backend_internal`
e possui o alias DNS `pipeline-api`. No Compose do backend, declare essa rede
como externa e conecte somente o serviço backend a ela:

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

O backend pode então chamar `http://pipeline-api:8000`. Não publique a porta
`8000` no host e não configure uma rota Traefik para o pipeline. Configure o
Traefik para encaminhar `api.monitorame.tech` ao backend público.

Configure no backend a lista de origens CORS autorizadas para as chamadas do
navegador. CORS restringe navegadores, mas não impede clientes como `curl` de
chamar uma API pública; o pipeline fica protegido por não ter rota ou porta
pública e por aceitar conexões somente de serviços conectados à rede Docker.

## Atualizações futuras

Preserve o `.env` privado da VPS e o volume
`monitorame_pipeline_api_postgres_data`. Não use `docker compose down -v` em
produção.
