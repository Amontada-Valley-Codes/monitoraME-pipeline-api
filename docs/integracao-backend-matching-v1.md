# Integração do Backend com o Matching NLP (`matching.v1`)

Este documento orienta a integração futura do Backend NestJS com o serviço NLP
do Pipeline: dados de entrada, processamento do lote, formato das respostas e
associação das recomendações às empresas. É um contrato de integração, não uma
declaração de que as rotas HTTP já estejam disponíveis.

## Estado atual

Os schemas, o motor local, a persistência versionada e a fila/worker de matching
existem no Pipeline. A execução pode ser feita localmente pela CLI. As rotas
`/internal/matching/batches` descritas abaixo ainda não têm handlers HTTP,
autenticação de serviço ou contrato OpenAPI implementado. Antes de integrar o
NestJS, as equipes devem confirmar esses pontos e congelar os códigos HTTP e a
política operacional.

O serviço recebe snapshots mínimos de empresa e consulta oportunidades já
normalizadas no Pipeline/Analytics. Ele não consulta o banco do Backend nem o
PNCP durante o matching.

## Fluxo recomendado

1. O Backend seleciona empresas que devem ter o matching atualizado e monta
   snapshots com `CompanyProfile`.
2. O Backend gera um UUID `batch_id`, fixa `schema_version` como `matching.v1`
   e envia um lote de 1 a 500 empresas.
3. O serviço valida e registra duravelmente o lote e seus jobs antes de aceitar
   a requisição. A resposta de aceitação contém o `batch_id` e a contagem aceita.
4. Um worker processa o lote de forma assíncrona, grava resultados associados a
   cada `company_id` e preserva as versões usadas.
5. O Backend consulta o estado e pagina os resultados. Persiste-os no contexto
   da empresa correspondente e os disponibiliza na área de oportunidades.
6. Se houver notificações, um processo do Backend decide separadamente quando e
   como enviá-las, usando os resultados persistidos e as preferências do usuário.

```text
NestJS Backend -> POST batch -> Pipeline Matching
                                worker -> resultados por company_id
NestJS Backend <- status/resultados paginados
NestJS Backend -> persistência/feed da empresa -> interface / notificações
```

## Requisição

### Envelope do lote

```json
{
  "batch_id": "d27bc9a2-0c7a-44d5-b4be-32a86d9d3821",
  "schema_version": "matching.v1",
  "companies": []
}
```

- `batch_id`: UUID gerado pelo Backend por lote lógico. Repetições de envio do
  mesmo conteúdo devem reutilizar o mesmo ID. Se o envio original falhar com
  resultado incerto, repetir com o mesmo ID permite idempotência. Um conteúdo
  diferente sob o mesmo ID deve resultar em conflito, nunca substituir o lote.
- `schema_version`: valor constante definido pelo contrato, atualmente
  `matching.v1`. O Backend envia esse valor; não o gera por requisição.
- `companies`: de 1 a 500 perfis, sem `company_id` duplicado no lote. Para mais
  empresas, dividir em lotes distintos, cada qual com seu UUID.

### Perfil `CompanyProfile`

Campos necessários:

| Campo | Tipo/formato | Origem/uso |
|---|---|---|
| `company_id` | UUID | ID interno estável da empresa no Backend; chave para associar os resultados. |
| `profile_version` | string não vazia, até 100 caracteres | Versão/revisão do snapshot do perfil. Deve mudar quando mudarem dados relevantes enviados ao matching. |
| `company_size` | `MEI`, `ME`, `EPP` ou `DEMAIS` | Porte conhecido pelo Backend. |
| `primary_cnae` | CNAE de 7 dígitos | CNAE principal; precisa aparecer como o CNAE primário em `cnaes`. |
| `cnaes` | 1–100 itens únicos | Cada item contém `code`, `description` opcional e `is_primary`; exatamente um deve ter `is_primary: true`. Descrições alimentam as consultas semânticas. |
| `location` | `{ municipality_code, state }` | Município IBGE com 7 dígitos e UF em maiúsculas. É a sede e não implica preferência geográfica. |

Preferências opcionais:

| Campo | Tipo | Observação |
|---|---|---|
| `categories` | lista de strings | Categorias de interesse explícitas, caso o Backend tenha uma taxonomia compatível. |
| `operational_preferences` | objeto de valores string, boolean ou inteiro | Restrições/preferências operacionais explícitas e comparáveis com o edital. |
| `participation_forms` | lista de strings ou `null` | Formas de participação aceitas pela empresa. |
| `interested_states` | lista de UFs ou `null` | Preferência geográfica explícita; não inferir da sede. |
| `interested_municipalities` | lista de códigos IBGE ou `null` | Municípios de interesse explicitamente configurados. |
| `preferred_modality_codes` | lista de strings ou `null` | Códigos de modalidade de interesse explícita. |

As preferências podem ser omitidas ou enviadas vazias/nulas. A ausência não deve
excluir oportunidades. O Backend não deve enviar CNPJ, CPF, nome empresarial,
telefone, e-mail, endereço completo, credenciais ou dados de assinatura. O
schema rejeita campos extras.

Exemplo de requisição completa:

```json
{
  "batch_id": "d27bc9a2-0c7a-44d5-b4be-32a86d9d3821",
  "schema_version": "matching.v1",
  "companies": [
    {
      "company_id": "11111111-1111-4111-8111-111111111111",
      "profile_version": "profile-2026-11-01T10:30:00Z",
      "company_size": "ME",
      "primary_cnae": "6201501",
      "cnaes": [
        {
          "code": "6201501",
          "description": "Desenvolvimento de software",
          "is_primary": true
        },
        {
          "code": "6202300",
          "description": "Desenvolvimento e licenciamento de programas",
          "is_primary": false
        }
      ],
      "location": {
        "municipality_code": "2304400",
        "state": "CE"
      },
      "categories": [],
      "operational_preferences": {},
      "participation_forms": null,
      "interested_states": null,
      "interested_municipalities": null,
      "preferred_modality_codes": null
    }
  ]
}
```

O serviço cria um vetor por CNAE com descrição disponível. CNAEs sem descrição
no perfil nem no catálogo versionado são omitidos das consultas e reportados
como ausência de dado; portanto, o Backend deve fornecer descrições confiáveis
ou garantir que o Pipeline tenha catálogo correspondente.

## Respostas HTTP previstas

As rotas são propostas e precisam ser implementadas antes de uso HTTP.

### Aceitação: `POST /internal/matching/batches`

Esperado: `202 Accepted` com `BatchAccepted`:

```json
{
  "batch_id": "d27bc9a2-0c7a-44d5-b4be-32a86d9d3821",
  "status": "pending",
  "accepted_companies": 1
}
```

Um reenvio idêntico com o mesmo `batch_id` deve ser tratado idempotentemente.
Mesmo ID com conteúdo diferente retorna `409 Conflict`. Outros status previstos:

| HTTP | Significado |
|---|---|
| `413` | Corpo acima do limite operacional. |
| `422` | JSON/schema/campo inválido. |
| `503` | Lote não pôde ser registrado duravelmente; pode-se repetir com o mesmo `batch_id`. |

Erro estruturado previsto (sem ecoar o payload):

```json
{
  "error": {
    "code": "INVALID_PROFILE",
    "message": "O perfil contém um campo inválido.",
    "field": "companies[0].cnaes[0].code"
  }
}
```

### Estado: `GET /internal/matching/batches/{batch_id}`

Esperado: `200 OK` com `BatchStatusResponse`:

```json
{
  "batch_id": "d27bc9a2-0c7a-44d5-b4be-32a86d9d3821",
  "schema_version": "matching.v1",
  "status": "running",
  "total_companies": 1,
  "processed_companies": 0,
  "failed_companies": 0,
  "created_at": "2026-11-01T10:30:00Z",
  "updated_at": "2026-11-01T10:31:00Z",
  "error": null
}
```

Estados: `pending`, `running`, `completed`, `partial`, `failed`. `processed_companies`
conta perfis concluídos com sucesso; `failed_companies`, falhas finais. `404`
indica lote não encontrado/visível. O campo `error` contém somente diagnóstico
seguro, sem dados pessoais nem payload.

### Resultados: `GET /internal/matching/batches/{batch_id}/results`

Esperado: `200 OK` com página `MatchingResultsPage`:

```json
{
  "batch_id": "d27bc9a2-0c7a-44d5-b4be-32a86d9d3821",
  "schema_version": "matching.v1",
  "status": "completed",
  "results": [
    {
      "company_id": "11111111-1111-4111-8111-111111111111",
      "edital": {
        "procurement_id": "pncp:exemplo",
        "source": "PNCP",
        "external_id": "exemplo",
        "object": "Contratação de serviços de desenvolvimento de software",
        "url": "https://exemplo.gov.br/edital",
        "version": "normalized-v1"
      },
      "score": 83.5,
      "semantic_relevance": 91.0,
      "preference_score": null,
      "preference_confidence": 0,
      "data_confidence": 75,
      "eligibility": "NOT_CONFIRMED",
      "compatibility_level": "HIGH",
      "reason_codes": ["SEMANTIC_MATCH"],
      "warning_codes": ["LEGAL_ELIGIBILITY_NOT_VERIFIED"],
      "evidence": [
        {
          "criterion": "semantic_relevance",
          "score": 0.91,
          "details": "Similaridade semântica calculada com CNAE e conteúdo indexado da oportunidade."
        }
      ],
      "justifications": [
        {
          "code": "SEMANTIC_MATCH",
          "description": "O conteúdo da oportunidade apresenta relevância semântica para as atividades da empresa.",
          "evidence": "CNAE 6201501: Desenvolvimento de software."
        }
      ],
      "profile_version": "profile-2026-11-01T10:30:00Z",
      "schema_version": "matching.v1",
      "matching_version": "hybrid-matching-v2",
      "taxonomy_version": "taxonomy-v1",
      "model_version": "e5@revision-sha",
      "processing_state": "completed",
      "processed_at": "2026-11-01T10:32:00Z"
    }
  ],
  "next_cursor": null
}
```

Os valores do exemplo são ilustrativos; em produção, versões e escores devem
ser os efetivamente calculados. `limit` previsto: padrão 100, mínimo 1 e máximo
500; `cursor` é opaco. Se houver `next_cursor`, buscar a próxima página. Uma
lista `results` vazia pode significar que ainda não existem resultados ou que
o lote terminou sem matches; interpretar junto com `status`. Cursor inválido
retorna `400`; lote desconhecido, `404`.

## Como interpretar e persistir os resultados

- `score`: relevância híbrida de 0 a 100, para ordenar recomendações. Não é
  probabilidade de vitória nem parecer jurídico.
- `semantic_relevance`: sinal semântico de 0 a 100, separado dos critérios
  determinísticos; pode ser nulo se não houver sinal semântico.
- `preference_score` e `preference_confidence`: aderência/cobertura de
  preferências explícitas, separados da relevância e da elegibilidade.
- `data_confidence`: cobertura dos dados usados pelo cálculo, não confiança de
  que a empresa será habilitada.
- `eligibility`: `ELIGIBLE`, `INELIGIBLE` ou `NOT_CONFIRMED`. `ELIGIBLE` significa
  apenas que os campos verificáveis disponíveis não apontaram impedimento.
- `compatibility_level`: `LOW`, `MEDIUM` ou `HIGH`; resultados baixos continuam
  pesquisáveis e a interface pode permitir filtrá-los.
- `reason_codes`, `warning_codes`, `evidence`, `justifications`: motivos e
  evidências dos critérios aplicados. A descrição é texto controlado pelo
  código/regra; o modelo de embeddings não redige essa justificativa.
- `profile_version`, `schema_version`, `matching_version`, `taxonomy_version`,
  `model_version`, `edital.version`: versões necessárias para rastrear e
  reproduzir o cálculo. Persistir junto do resultado.

O Backend deve tratar `company_id` como associação explícita e validar que a
empresa pertence ao tenant/conta autorizado antes de expor a recomendação. A
identidade do lote não concede autorização de leitura. Persistir de forma
idempotente por uma chave equivalente a `(company_id, procurement_id,
edital.version, profile_version, matching_version, taxonomy_version,
model_version)`, ou manter histórico e marcar qual snapshot está vigente. A
chave final deve ser acordada entre Backend e Pipeline antes da integração.

## Distribuição às respectivas empresas

O serviço NLP calcula e persiste snapshots de resultados; não envia
notificações. A distribuição pertence ao Backend e deve seguir esta sequência:

1. Buscar todas as páginas do lote e validar `batch_id`, `schema_version` e
   `company_id` dos resultados.
2. Para cada resultado, localizar a empresa pelo UUID interno e confirmar o
   vínculo/tenant usando as regras de autorização já existentes no Backend.
3. Fazer upsert no armazenamento de recomendações do Backend, mantendo a
   referência do edital e as versões/evidências recebidas. Nunca encaminhar um
   resultado a todas as empresas do lote: a distribuição é por `company_id`.
4. Servir recomendações na área autenticada da empresa. Ordenar por `score`
   decrescente como padrão, oferecendo os filtros de compatibilidade,
   elegibilidade e situação conforme os campos disponíveis.
5. Deduplicar notificações pela empresa e oportunidade (e pela versão/alteração
   relevante). Notificar apenas após persistência bem-sucedida e conforme as
   preferências de comunicação; falhas de notificação não devem apagar o
   resultado persistido.
6. Apresentar evidências, avisos e a ressalva de que o resultado não garante
   elegibilidade. Não converter automaticamente `ELIGIBLE` em aprovação para
   participar.

Para reduzir tráfego e trabalho, o Backend pode enviar somente perfis criados
ou alterados desde a última versão processada. Se o matching for executado por
empresa em lotes menores, cada resposta continua vinculada pelo `company_id`;
`batch_id` é somente a identidade do lote e não substitui a chave da empresa.

## Segurança, repetição e operação

- Expor as rotas apenas em rede privada e exigir autenticação serviço a serviço
  quando forem implementadas.
- Definir timeout, retries limitados e limites do corpo. Repetir POST com mesmo
  `batch_id` e payload idêntico; não gerar IDs novos após timeout de resultado
  incerto.
- Não registrar payloads completos nem dados pessoais em logs.
- Não consultar PNCP do Backend por empresa. O Pipeline usa a visão normalizada
  compartilhada.
- Não enviar notificações, aprovar participação nem determinar conformidade
  legal automaticamente a partir deste serviço.

## Pontos a fechar antes da integração HTTP

- Implementação e publicação dos handlers, OpenAPI, autenticação e autorização
  serviço a serviço.
- Política de timeout/retry, limites do corpo e tratamento idempotente do POST.
- Paginação estável, retenção dos batches/resultados e política de reprocessamento.
- Chave de upsert/histórico acordada entre Backend e Pipeline.
- Responsável pelo agendamento de batches e critério de atualização do perfil.
- Política de notificações e preferências de comunicação no Backend.
- Validação humana dos pesos e cortes do score, que são baseline técnico e não
  calibração comercial/legal aprovada.
