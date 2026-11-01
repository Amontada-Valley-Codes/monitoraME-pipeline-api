# Contratos locais do Matching NLP v1

Os modelos Python tipados ficam em `app/nlp/profiles/schemas.py` (entrada) e
`app/nlp/matching/schemas.py` (resultados e envelopes). `schema_version` é
`matching.v1`. Campos extras são rejeitados para impedir envio acidental de
PII. O snapshot espelha `Company.id`, `Company.companySize`,
`Company.primaryCnae`, `Company.municipalityCode`, `Company.state` e a relação
`Company.cnaes` / `CompanyCnae`; não recebe CNPJ, nomes, telefone, e-mail nem
endereço.

## Entrada

```json
{
  "batch_id": "d27bc9a2-0c7a-44d5-b4be-32a86d9d3821",
  "schema_version": "matching.v1",
  "companies": [{
    "company_id": "11111111-1111-4111-8111-111111111111",
    "profile_version": "profile-1",
    "company_size": "MEI",
    "primary_cnae": "6201501",
    "cnaes": [{"code": "6201501", "description": "Desenvolvimento de software", "is_primary": true}],
    "location": {"municipality_code": "2304400", "state": "CE"}
  }]
}
```

São aceitos somente os portes `MEI`, `ME`, `EPP` e `DEMAIS`. Cada perfil deve
ter entre 1 e 100 CNAEs únicos, exatamente um CNAE primário igual a
`primary_cnae`, código CNAE de sete dígitos, código IBGE de município com sete
dígitos e UF em duas letras maiúsculas. O lote aceita de 1 a 500 empresas sem
IDs repetidos. Erros de validação informam o caminho do campo.

## Saída

`MatchingResult` associa obrigatoriamente `company_id` ao edital normalizado e
inclui `score` entre 0 e 1, nível (`LOW`, `MEDIUM`, `HIGH`), justificativas
codificadas, `profile_version`, `schema_version`, `matching_version`, estado
de processamento e timestamp opcional. A referência do edital contém
`procurement_id`, origem/ID externo, objeto, URL opcional e versão do registro
normalizado. Os envelopes `BatchAccepted`, `BatchStatusResponse` e
`MatchingResultsPage` definem os formatos previstos para aceitação, consulta
de estado e resultados paginados.

## Planejamento das rotas futuras

As definições abaixo são uma proposta de contrato para a fase de integração.
Não há routers, handlers, autenticação ou persistência HTTP implementados.
Os códigos HTTP e regras ainda devem ser confirmados com o Backend antes da
integração.

### `POST /internal/matching/batches`

Recebe `MatchingBatchRequest` e agenda processamento assíncrono. A requisição
não executa matching durante o ciclo HTTP.

- `202 Accepted` com `BatchAccepted` quando o lote é novo ou uma repetição
  idêntica de `batch_id` e conteúdo. Retorna `batch_id`, `pending` e quantidade
  aceita.
- `409 Conflict` se `batch_id` já existir com conteúdo diferente. Nunca
  substitui o conteúdo de um lote existente.
- `413 Content Too Large` se ultrapassar o limite operacional de corpo,
  independente do máximo de 500 perfis do schema.
- `422 Unprocessable Entity` para JSON inválido semanticamente, schema
  desconhecido, porte inválido, CNAE/localização inválidos, IDs repetidos ou
  campos extras. O corpo deve apontar `code`, `message` e `field` (caminho do
  campo), sem ecoar o payload recebido.
- `503 Service Unavailable` se não for possível registrar o lote de modo
  durável; o chamador pode repetir a mesma solicitação com o mesmo `batch_id`.

O hash idempotente deve ser calculado sobre JSON canônico após validação, com
ordem de chaves estável. Definir no desenho de persistência o tratamento da
ordem de `companies` e `cnaes`; recomendação: preservar ordem recebida no
conteúdo, embora ela não altere a identidade dos resultados. A aceitação só
deve ocorrer depois de persistir o lote e todos os jobs/itens necessários.

### `GET /internal/matching/batches/{batch_id}`

Consulta estado e contagens do lote; retorna `BatchStatusResponse`.

- `200 OK` para lote conhecido, inclusive estados terminais.
- `404 Not Found` para UUID válido sem lote visível ao chamador.
- `400 Bad Request` para `batch_id` fora do formato UUID.

Estados: `pending` ainda não iniciado; `running` em execução; `completed` com
todos os perfis processados sem falhas; `partial` com pelo menos um sucesso e
uma falha; `failed` sem sucesso após conclusão/falha terminal. Para não
confundir total com sucesso, o campo `processed_companies` representa perfis
concluídos com sucesso; `failed_companies` representa falhas finais. Em
`pending`/`running`, os totais podem ser parciais e `error` deve conter apenas
diagnóstico seguro e resumido, nunca payload ou dados pessoais.

### `GET /internal/matching/batches/{batch_id}/results`

Consulta resultados prontos do lote usando `MatchingResultsPage`.

- Query proposta: `cursor` opaco opcional e `limit` opcional, padrão 100,
  mínimo 1 e máximo 500. Cursor inválido/expirado retorna `400` com erro
  estruturado.
- `200 OK` para lote conhecido; `results` pode ser lista vazia quando ainda
  não há resultados ou quando terminou sem matches. A resposta inclui `status`
  para permitir polling sem uma segunda consulta. `next_cursor` ausente indica
  fim da página atualmente disponível, não necessariamente fim do lote se o
  `status` ainda for `pending` ou `running`.
- `404 Not Found` para lote desconhecido.

Cada página retorna `batch_id`, `schema_version`, `status`, `results` e
`next_cursor`. Cada resultado deve estar associado a `company_id`, oportunidade e às versões
do perfil, schema, modelo/regra de matching e registro normalizado. Ordenação
estável recomendada: `company_id`, score decrescente e `procurement_id`, com
cursor keyset vinculado ao batch e à ordenação. Nunca retornar CNPJ, nome,
contatos, endereço, nem dados de outra empresa. Um resultado vazio não deve
ser interpretado como falha do lote; usar a rota de status para distinguir
execução pendente de lote concluído sem matches.

### Regras transversais da integração

- Prefixo `/internal` deve ficar em rede privada e exigir autenticação de
  serviço quando conectado ao NestJS. Credenciais, assinatura de payload e
  autorização são responsabilidade da fase de integração, não da CLI local.
- O serviço Matching recebe snapshots mínimos e lê editais já normalizados no
  Pipeline/Analytics; não consulta o banco do Backend nem a API PNCP.
- Aplicar timeout no cliente, limite de corpo/lote, rate limit e logs sem
  payload. Para erros usar envelope estável `{"error":{"code":"...",
  "message":"...","field":"..."}}`, sem stack trace ao chamador.
- Retenção/expiração de lote, cancelamento, política de retry e janela de
  resultados incrementais precisam ser definidos antes de produção.
- A criação HTTP deverá apenas persistir o batch e enfileirar trabalho durável;
  worker separado calcula e persiste os resultados. Repetir POST não pode
  duplicar jobs nem resultados.

## Uso local

```powershell
python -m app.nlp.cli validate-fixtures
python -m unittest tests.nlp.test_contracts
```

As fixtures em `tests/nlp/fixtures/companies.json` simulam quatro empresas de
segmentos, portes, localidades e quantidades de CNAE distintos. A CLI valida
os contratos sem NestJS, credenciais, assinatura, Redis, banco ou chamadas ao
PNCP; editais consumidos pelo motor devem vir da visão já normalizada do
Pipeline.
