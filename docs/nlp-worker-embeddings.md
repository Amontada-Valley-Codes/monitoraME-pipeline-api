# Worker de embeddings e batches de matching

O serviço `matching-nlp-worker` usa PostgreSQL Analytics e o cache nomeado do
modelo E5. Ele não consulta a API PNCP, não acessa o banco Backend e não possui
endpoint HTTP. O processo só carrega os pesos quando encontra batch de empresa
pronto para processamento.

Sem batch pendente, o worker não sincroniza nem percorre o corpus e não agenda
embeddings. Quando um batch existe, ele prepara o corpus se ainda não houver
segmentos, agenda embeddings faltantes para o modelo ativo, processa esses
jobs e então executa o matching. A normalização e o enriquecimento de itens
sincronizam os segmentos textuais afetados com o corpus.

## Idempotência e segurança de versões

1. O batch armazena os perfis mínimos e um hash canônico do payload.
2. Cada embedding é identificado por origem, edital, segmento, `text_hash` e
   `model_version`; reexecução da mesma identidade não duplica vetores.
3. O worker relê o segmento depois da inferência. Se o texto foi alterado,
   descarta o vetor obsoleto e agenda o hash atual no próximo ciclo.
4. Jobs usam `FOR UPDATE SKIP LOCKED`, lease e token. Expiração permite
   recuperação sem que um worker antigo conclua o job de outro.
5. Resultados mantêm versões de perfil, edital, modelo, taxonomia e matching.

O E5 produz vetores L2-normalizados de dimensão 384, persistidos em
`vector(384)` com pgvector. O cache do modelo fica em
`matching_nlp_model_cache`, então o container pode ser removido ao terminar sem
baixar os pesos novamente na execução seguinte.

## Execução automática e memória ociosa

O serviço fica ativo junto ao Compose para consumir jobs criados pelos
endpoints HTTP ou pela CLI. Sem batches prontos, não percorre o corpus, não
agenda embeddings e não carrega os pesos do modelo. Quando o batch é processado,
o worker libera o modelo após `NLP_MODEL_IDLE_UNLOAD_SECONDS` (padrão 60
segundos); o cache de arquivos continua no volume. O processo leve permanece
rodando e verifica a fila a cada `NLP_EMBEDDING_IDLE_SECONDS` segundos
(padrão 5), permitindo que o Backend submeta trabalho sem iniciar Docker.

No Compose de produção, suba o worker com `docker compose -f
compose.production.yaml up -d matching-nlp-worker`. Para executar `docker`, use
o host da VPS ou o painel de deploy, não um shell `app@<container-id>` dentro
do container.

## Recursos e configuração

O container é CPU-only, com batch size 1, limite configurável por
`MATCHING_NLP_MEMORY_LIMIT` (padrão 2 GiB) e
`MATCHING_NLP_CPUS` (padrão 1,5 CPU). O modelo e a revisão ficam fixados em
`NLP_EMBEDDING_MODEL` e `NLP_EMBEDDING_REVISION`; a revisão compõe a identidade
dos vetores. O cache precisa de saída de rede ao Hugging Face apenas na
primeira execução ou quando a revisão ainda não estiver baixada.

Com batch de matching pendente, o worker consulta os embeddings faltantes no
máximo uma vez por minuto, inclusive quando a fila de embeddings esvazia. A
consulta percorre o corpus e não precisa ser repetida após cada vetor; o
matching pode começar até um minuto depois da conclusão do último embedding.

## Rede e limites de integração

Em produção, a API do Pipeline publica os endpoints autenticados de matching
em `http://pipeline-api:8000/internal/matching/...` pela rede
`monitorame_backend_internal`. O worker NLP também usa essa rede e a rede
padrão do Pipeline para acessar Analytics; ele não recebe chamadas HTTP. O
Backend usa um token Bearer compartilhado em `MATCHING_INTERNAL_API_TOKEN`.

Falhas transitórias usam a política de retry da fila persistente. Tentativas
esgotadas ficam `FAILED` para replay autorizado. O worker recupera leases dos
tipos `nlp_embedding` e `nlp_matching_batch`, sem alterar leases de coleta,
normalização ou enriquecimento.
