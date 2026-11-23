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

O perfil tambem aceita `business_description`, texto opcional de ate 150
caracteres sobre a atuacao da empresa. O campo complementa somente a consulta
semantica do CNAE principal; sua alteracao deve incrementar `profile_version`.
Sua ausencia preserva o comportamento dos perfis existentes.

São aceitos somente os portes `MEI`, `ME`, `EPP` e `DEMAIS`. Cada perfil deve
ter entre 1 e 100 CNAEs únicos, exatamente um CNAE primário igual a
`primary_cnae`, código CNAE de sete dígitos, código IBGE de município com sete
dígitos e UF em duas letras maiúsculas. O lote aceita de 1 a 500 empresas sem
IDs repetidos. Erros de validação informam o caminho do campo.

## Saída

`MatchingResult` associa obrigatoriamente `company_id` ao edital normalizado e
inclui `score` entre 0 e 100, `data_confidence` entre 0 e 100, elegibilidade
(`ELIGIBLE`, `INELIGIBLE`, `NOT_CONFIRMED`), nível (`LOW`, `MEDIUM`, `HIGH`),
`reason_codes`, `warning_codes`, evidências, `profile_version`, `schema_version`,
`matching_version`, `taxonomy_version`, estado de processamento e timestamp.
A referência do edital contém
`procurement_id`, origem/ID externo, objeto, URL opcional e versão do registro
normalizado. Os envelopes `BatchAccepted`, `BatchStatusResponse` e
`MatchingResultsPage` definem os formatos previstos para aceitação, consulta
de estado e resultados paginados.

As regras locais e a definição de confiança ficam em
[`matching-deterministico.md`](matching-deterministico.md). Resultados LOW
e MEDIUM permanecem persistidos para diagnóstico. As rotas de resultados
HTTP retornam LOW, MEDIUM e HIGH quando `score > 40`; os estados de elegibilidade permanecem explícitos.

## Integração HTTP implementada

As rotas abaixo estão disponíveis no serviço Pipeline. O Backend envia um
snapshot `matching.v1`; o Pipeline persiste o lote na fila PostgreSQL, e o
worker executa o matching de forma assíncrona. Todas exigem
`Authorization: Bearer <MATCHING_INTERNAL_API_TOKEN>`. Configure o mesmo segredo
no Backend e na API do Pipeline. Se o segredo não estiver configurado, a API
responde `503`; se estiver ausente ou inválido, responde `401`.

### `POST /internal/matching/batches`

Recebe `MatchingBatchRequest` e agenda processamento assíncrono. A requisição
não executa matching durante o ciclo HTTP.

- `202 Accepted` com `BatchAccepted` quando o lote é novo ou uma repetição
  idêntica de `batch_id` e conteúdo. Retorna `batch_id`, `pending` e quantidade
  aceita.
- `409 Conflict` se `batch_id` já existir com conteúdo diferente. Nunca
  substitui o conteúdo de um lote existente.
- `422 Unprocessable Entity` para JSON inválido semanticamente, schema
  desconhecido, porte inválido, CNAE/localização inválidos, IDs repetidos ou
  campos extras. Os erros de validação não ecoam o payload recebido.
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
- `422 Unprocessable Entity` para `batch_id` fora do formato UUID.

Estados: `pending` ainda não iniciado; `running` em execução; `completed` com
todos os perfis processados sem falhas; `partial` com pelo menos um sucesso e
uma falha; `failed` sem sucesso após conclusão/falha terminal. Para não
confundir total com sucesso, o campo `processed_companies` representa perfis
concluídos com sucesso; `failed_companies` representa falhas finais. Em
`pending`/`running`, os totais podem ser parciais e `error` deve conter apenas
diagnóstico seguro e resumido, nunca payload ou dados pessoais.

### `GET /internal/matching/batches/{batch_id}/results`

Consulta resultados prontos do lote usando `MatchingResultsPage`.
Somente resultados com `score > 40` entram em `results`; o cursor avança entre esses resultados.

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

## Uso local com fixtures explícitas

```powershell
python -m app.nlp.cli validate-fixtures --fixtures tests/nlp/fixtures/companies.json
python -m unittest tests.nlp.test_contracts
```

As fixtures em `tests/nlp/fixtures/companies.json` simulam empresas para
validação e smoke tests. A CLI só as lê quando `--fixtures` é informado; não
existe arquivo de empresas padrão no fluxo de produção. O Backend envia os
perfis pela API interna, e o worker processa o snapshot persistido no
PostgreSQL. Editais consumidos pelo motor vêm da visão já normalizada do
Pipeline.

## Campos adicionados ao motor hibrido local

O perfil aceita `interested_states`, `interested_municipalities` e
`preferred_modality_codes` como preferencias opcionais explicitas. `location`
continua representando a sede e nao e usado para inferir interesse geografico.
O edital pode informar `accepted_company_sizes_source`; sem essa proveniencia,
porte ausente/incompleto resulta em `NOT_CONFIRMED`, nunca em inelegibilidade.

O resultado preserva `semantic_relevance`, `preference_score` e
`model_version`, separados do score de relevancia e do gate. Sempre inclui
`LEGAL_ELIGIBILITY_NOT_VERIFIED`. A persistencia local usa batches/resultados
Analytics versionados; as rotas HTTP descritas neste documento continuam sendo
propostas futuras.

O resultado tambem pode retornar `preference_confidence` (cobertura dos
criterios de preferencia comparaveis), sem penalizar score ou elegibilidade
quando preferencias nao foram enviadas.
