# Fluxo de comunicação e processamento do Matching NLP

## Escopo e estado atual

Este documento descreve como o Pipeline recebe um snapshot de empresa,
recupera oportunidades PNCP normalizadas, calcula o matching e persiste
resultados.

Os schemas, o processamento local, a fila interna e a persistência existem. O
processamento é operado pela CLI e pelo worker. Não há handlers HTTP para
`POST/GET /internal/matching/batches`, autenticação serviço a serviço ou
OpenAPI implementados neste serviço; portanto, não há integração HTTP ativa
com o Backend descrita aqui.

## Visão ponta a ponta

```text
Consumidor do Pipeline
  └─ snapshot mínimo: matching.v1 + batch_id + empresas
       └─ validação Pydantic e deduplicação de batch
            └─ job persistente PostgreSQL (enqueue) ou execução CLI local
                 └─ embedding query por CNAE com E5
                      └─ Top-K no Analytics/pgvector sobre corpus PNCP pronto
                           └─ filtros de status + carga de dados normalizados
                                └─ elegibilidade + score híbrido + evidências
                                     └─ resultado versionado no banco principal
                                          └─ consulta local dos resultados persistidos
```

O Matching não consulta o banco do Backend, não chama a API PNCP, não lê RAW
durante a busca e não envia notificações. A fonte da recuperação é o conjunto
já normalizado e indexado no Analytics. O banco principal guarda batch, jobs e
resultados; Analytics fornece oportunidades, itens, segmentos e embeddings.

## 1. O que o Backend envia

O envelope é `MatchingBatchRequest`: `batch_id` UUID, `schema_version` literal
`matching.v1` e de 1 a 500 empresas. `company_id` não pode repetir dentro do
lote. O hash canônico do conteúdo é associado ao `batch_id`: reenvio com mesmo
conteúdo é idempotente; reutilizar o mesmo ID com payload diferente falha em
conflito, sem substituir o snapshot registrado.

Cada `CompanyProfile` contém `company_id`, `profile_version`, `company_size`,
`primary_cnae`, lista `cnaes` e `location`. Pode também conter categorias,
preferências operacionais, formas de participação, estados/municípios de
interesse e modalidades preferidas. O schema rejeita campos extras. CNPJ, CPF,
razão social, contatos, endereço completo, credenciais e assinatura não fazem
parte do snapshot.

Validações importantes:

- CNAE tem sete dígitos; a lista tem de 1 a 100 códigos sem duplicatas.
- Exatamente um CNAE tem `is_primary=true`, e seu código é `primary_cnae`.
- Localização usa código IBGE de sete dígitos e UF em maiúsculas.
- `profile_version` identifica a versão dos dados relevantes ao cálculo.
- Preferências não informadas são ausência de critério, não uma rejeição de
  oportunidade. A sede em `location` não é interpretada como preferência
  regional.

O perfil semântico é montado com uma descrição por CNAE, sem concatenar
atividades diferentes. Usa-se primeiro a descrição fornecida pelo Backend; se
ausente, o pipeline procura descrição no catálogo CNAE versionado disponível
no banco. CNAEs sem descrição utilizável não geram consulta vetorial e são
registrados como ausência de evidência. Se nenhum CNAE gerar consulta, aquele
perfil não produz candidatos e é contado como falha de perfil no batch.

## 2. Como o texto vira consulta NLP

O modelo padrão é `intfloat/multilingual-e5-small`, com revisão fixada por
configuração, dimensão 384, limite de 512 tokens e execução padrão em CPU. Para
cada texto de CNAE, o adaptador acrescenta o prefixo E5 `query: `, codifica e
normaliza o vetor em norma L2. A versão registrada combina identificador e
revisão do modelo. Não se gera embedding de CNPJ, nome da empresa ou do objeto
de preferência livre.

O corpus de oportunidades é preparado separadamente por `sync-corpus`, usando
somente as tabelas normalizadas de oportunidades e itens do Analytics. Os tipos
de segmento são objeto, informação complementar/orçamento sigiloso, descrição
de item e lote derivado das descrições de seus itens. A normalização textual
remove HTML, aplica Unicode NFKC e compacta espaços; o hash SHA-256 identifica
o texto. Segmentos utilizáveis recebem embeddings `passage: ` da mesma família
de modelo. Texto complementar genérico de edital não está coberto além dos
campos normalizados listados; anexos/documentos não participam deste fluxo.

Esta preparação assíncrona é independente do matching. Uma mudança de edital
precisa passar pela normalização, sincronização do corpus e geração dos
embeddings antes de poder ser recuperada semanticamente.

## 3. Recuperação de oportunidades

Para cada CNAE com descrição, o PostgreSQL Analytics compara o vetor de query
com embeddings de passagens pelo cosseno via pgvector. O modelo da consulta
precisa coincidir com `model_version` dos embeddings persistidos. A busca
considera `OBJECT`, `COMPLEMENTARY`, `ITEM` e `LOT`; busca até cinco vezes K
segmentos por CNAE (overfetch padrão 5) e consolida pelo par fonte/ID externo,
mantendo a maior similaridade entre os CNAEs e segmentos. O limite final é K
oportunidades distintas, padrão 20.

No fluxo atual, a chamada restringe o status derivado a `ABERTA` e
`ENCERRANDO_EM_BREVE`. Não passa filtro de modalidade nem região a partir da
sede ou das preferências. Embora a camada de recuperação aceite filtros
estruturados opcionais, só restringe por eles quando explicitamente chamada
com esses filtros. O Top-K é etapa de recuperação de candidatos, não lista
final ordenada pelo score híbrido.

Após recuperar IDs, o pipeline carrega a oportunidade normalizada, itens e
segmentos de evidência no Analytics. Usa hash do payload da oportunidade como
versão do edital. Busca sem candidatos resulta em zero resultados, não em uma
linha artificial de score zero.

## 4. Score, elegibilidade e explicações

Para cada candidato, o motor executa primeiro a avaliação determinística de
elegibilidade com os campos verificáveis disponíveis. Um impedimento conhecido
gera `INELIGIBLE`, score 0 e nível `LOW`; isso não elimina o registro de
auditoria. Ausência de prova suficiente resulta em `NOT_CONFIRMED`, nunca em
aprovação jurídica implícita.

Quando não há impedimento conhecido, o score híbrido soma critérios observados
com pesos fixos, sem redistribuir peso de dados ausentes:

| Critério | Peso |
|---|---:|
| Categorias explícitas coincidentes | 20% |
| Objeto | 10% |
| Texto complementar | 20% |
| Itens/lotes | 35% |
| Associação CNAE à taxonomia | 15% |

As comparações textuais usam similaridade lexical de tokens específicos e/ou
semântica calibrada. Para o sinal semântico, a similaridade cosseno bruta abaixo
de 0,88 não contribui; acima disso é escalada linearmente até 1. Objeto,
complementar e itens usam o maior valor entre lexical e semântico calibrado.
Categorizações PNCP vazias não são inferidas por IA: a dimensão CNAE usa o
mapa explícito e versionado de CNAE para categoria. A taxonomia ativa e a
versão do matching são gravadas no resultado.

Preferências explícitas são pontuadas separadamente: região 50%, modalidade
25% e operação 25%. `preference_score` é normalizado sobre os critérios
comparáveis; `preference_confidence` informa a cobertura desses critérios.
Uma preferência ausente não reduz o score de compatibilidade. `data_confidence`
é a cobertura dos pesos principais que tiveram evidência. Para evitar HIGH
com evidência insuficiente, score acima/igual a 75 com cobertura abaixo de 50%
é limitado logo abaixo de 75. Os níveis são `LOW` abaixo de 50, `MEDIUM` de 50
a menos de 75 e `HIGH` a partir de 75.

O resultado contém códigos de motivo/aviso, evidências por critério e
justificativas montadas a partir de códigos controlados. O embedding contribui
similaridade e trechos recuperados; ele não redige uma conclusão jurídica nem
uma justificativa livre. Scores são sinais de ordenação/revisão, não
probabilidade de vitória ou habilitação legal.

## 5. Persistência, idempotência e execução

O batch guarda hash e snapshots dos perfis. Execução direta (`run-matching`)
registra/reexecuta o batch localmente e persiste resultados. Enfileiramento
(`enqueue-matching`) registra estado `PENDING` e um job `nlp_matching_batch`
com chave de deduplicação ligada ao batch; o worker reivindica e processa o job
com lease/token. A execução renova o lease entre etapas e verifica o token ao
concluir, para impedir que um worker com lease expirado finalize o trabalho de
outro.

Os resultados ficam associados a `batch_id` e `company_id`. A unicidade
versionada inclui batch, empresa, origem/ID do edital, versão do perfil,
versão do edital, modelo, matching e taxonomia. Assim, uma repetição idêntica
é upsert idempotente e snapshots com versões diferentes permanecem
distinguíveis. Estados persistidos do batch são `PENDING`, `RUNNING`,
`COMPLETED`, `PARTIAL` e `FAILED`; internamente, o resultado individual marca
`completed`.

Interfaces operacionais implementadas hoje são CLI e funções locais. Exemplos
(o arquivo de entrada contém a lista de perfis, sem envelope):

```powershell
.venv\Scripts\python.exe -m app.nlp.cli run-matching --fixtures .local\nlp\profiles.json --batch-id 11111111-1111-4111-8111-111111111111
.venv\Scripts\python.exe -m app.nlp.cli enqueue-matching --fixtures .local\nlp\profiles.json --batch-id 11111111-1111-4111-8111-111111111111
.venv\Scripts\python.exe -m app.nlp.cli matching-status --batch-id 11111111-1111-4111-8111-111111111111
.venv\Scripts\python.exe -m app.nlp.cli matching-batch-results --batch-id 11111111-1111-4111-8111-111111111111 --limit 100
```

Esses comandos não equivalem a endpoints HTTP e não integram o Backend.

## 6. Estrutura dos resultados persistidos

O schema do resultado inclui `company_id`, referência do edital, `score`,
`semantic_relevance`, indicadores de preferências/cobertura, elegibilidade,
nível, motivos, avisos, evidências, versões de perfil/schema/matching/taxonomia/
modelo e horário de processamento. `semantic_relevance` é o sinal calibrado
exibido de 0 a 100; evidências de recuperação registram a similaridade bruta e
trechos usados.

Os resultados podem ser consultados pelas funções locais e comandos CLI. A
camada de repositório oferece paginação por cursor; o status apresentado vem do
estado persistido do batch. Esses mecanismos não constituem endpoints HTTP.

O consumidor deve associar cada resultado ao `company_id`, validar o vínculo
da empresa antes de disponibilizá-lo e preservar versões e evidências.
`batch_id` identifica o conjunto processado. A autorização, a distribuição e
eventuais notificações pertencem ao consumidor; Pipeline não distribui
resultados entre tenants.

## Limites e decisões ainda necessárias

- Implementar handlers HTTP, autenticação serviço a serviço, autorização,
  limites do corpo e OpenAPI para permitir integração direta via HTTP.
- Definir política de retries/timeouts, retenção, reprocessamento, distribuição
  e sincronização de perfil para consumidores integrados.
- Validar operacionalmente migrations no banco principal e Analytics,
  disponibilidade do pgvector/model cache e execução do worker no ambiente
  alvo. Este documento descreve o código/configuração, não atesta ambiente
  remoto ativo.
- Medir qualidade e latência com corpus representativo e rótulos humanos antes
  de transformar limiares ou níveis em decisão de produto/legal.
- Confirmar cobertura real das descrições complementares e a qualidade do
  mapeamento CNAE-categoria. Resultados sem texto ou sem mapeamento devem
  continuar explícitos como ausência de evidência.
