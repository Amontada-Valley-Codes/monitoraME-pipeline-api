# MonitoraME

## Compras públicas mais acessíveis e transparentes

O MonitoraME transforma dados públicos de compras e despesas governamentais em informação mais fácil de encontrar, acompanhar e analisar. A plataforma ajuda pequenos negócios a descobrir oportunidades de fornecimento ao setor público e facilita a compreensão dos gastos públicos por município, fornecedor e natureza da despesa.

[Acessar o projeto](https://monitorame.amontadavalley.com.br)

## Problema

Informações sobre licitações, contratos e despesas públicas estão disponíveis em diferentes sistemas e formatos. Para pequenos fornecedores, encontrar oportunidades compatíveis com seu negócio pode exigir tempo e conhecimento de portais especializados. Para cidadãos e gestores, transformar registros detalhados em uma visão clara dos gastos também é difícil.

## Solução

O MonitoraME reúne e organiza dados públicos do Portal Nacional de Contratações Públicas (PNCP) e do TCE-CE. O pipeline coleta, valida e normaliza esses registros antes de disponibilizá-los à plataforma.

Com esses dados, o produto oferece descoberta de oportunidades de compras públicas e indicadores para explorar despesas por município, fornecedor, porte empresarial e natureza. O armazenamento versionado e os registros de processamento mantêm a origem dos dados rastreável e permitem reprocessar informações quando necessário.

```text
PNCP e TCE-CE → coleta e validação → PostgreSQL → API → plataforma MonitoraME
```

Este repositório contém a API e o pipeline de dados que alimentam a plataforma. A demonstração web está disponível em [monitorame.amontadavalley.com.br](https://monitorame.amontadavalley.com.br).

## Como rodar

### Requisitos

- Docker com Docker Compose
- Git

### Iniciar a API localmente

Na raiz do repositório, copie a configuração de exemplo e inicie o PostgreSQL, as migrations e a API:

```bash
cp .env.example .env
docker compose up -d --build postgres migrate api
```

Confira os serviços:

```bash
docker compose ps
```

Com a API disponível, acesse:

- **Documentação interativa:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Verificação de saúde:** [http://localhost:8000/health](http://localhost:8000/health)
- **Demonstração do produto:** [https://monitorame.amontadavalley.com.br](https://monitorame.amontadavalley.com.br)

A configuração `.env.example` é voltada ao desenvolvimento local. A API local começa com o banco configurado no Compose; para experimentar ingestões, configure e inicie os workers correspondentes. Os workers consultam fontes externas e têm agendadores próprios, separados da API HTTP.

O scheduler PNCP consulta contratações com propostas abertas nas nove UFs do Nordeste, em jobs separados por UF e modalidade. Por padrão, calcula `dataFinal` como a data local mais 410 dias e percorre as modalidades 1–14 e 16–18; ajuste `PNCP_SCHEDULER_UFS`, `PNCP_SCHEDULER_OPEN_PROPOSALS_HORIZON_DAYS` e `PNCP_SCHEDULER_MODALIDADES` conforme a cobertura desejada. Consulte [o fluxo e os parâmetros do scheduler PNCP](docs/jobs-pncp.md).

### Testes

Com as dependências Python instaladas, execute a suíte automatizada:

```bash
python -m unittest discover -s tests
```

## Tecnologias

- Python 3.14 e FastAPI
- PostgreSQL e SQLAlchemy
- Alembic para migrations
- Docker Compose
- Integrações com PNCP, TCE-CE, IBGE e OpenCNPJ

## Documentação técnica

- [Ingestão TCE-CE](docs/ingestao-tce-ce.md)
- [Pipeline e dados brutos PNCP](docs/pncp-raw.md)
- [Documentação completa da API](http://localhost:8000/docs) — disponível depois de iniciar a aplicação
