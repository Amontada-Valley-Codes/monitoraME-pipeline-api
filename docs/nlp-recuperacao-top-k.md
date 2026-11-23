# Recuperação vetorial Top-K

O PostgreSQL Analytics armazena embeddings PNCP na coluna `vector(384)` e usa
pgvector com HNSW e distância cosseno. A migration `0025_nlp_pgvector_hnsw`
converte vetores existentes de JSONB sem remover histórico, cria o índice e
adiciona índices B-tree para modalidade, código IBGE e situação derivada. O
Compose usa `pgvector/pgvector:0.8.7-pg16`, mantendo PostgreSQL 16 e o volume
Analytics existente.

`app.nlp.matching.postgres_retrieval.buscar_contratacoes_top_k` recebe uma
consulta por CNAE, consulta cada vetor de perfil separadamente e consolida os
resultados por `(source, external_id)`. A pontuação da contratação é a maior
similaridade encontrada entre CNAEs e segmentos; os CNAEs e segmentos que
contribuíram ficam no candidato para explicação. O padrão inclui `OBJECT`,
`COMPLEMENTARY`, `ITEM` e `LOT`, mas o chamador pode limitar os tipos. O HNSW
usa `m=12`, `ef_construction=48`, busca iterativa em ordem estrita e
`ef_search` ajustado ao K com teto de 1000. Esses valores são um ponto inicial
conservador para a VPS e devem ser revisados com o corpus e a carga reais.

K pode ser passado em cada consulta ou configurado em `NLP_RETRIEVAL_TOP_K`
(padrão 20). A busca consulta `K * overfetch` segmentos por CNAE antes de
consolidar os editais (`overfetch=5` por padrão), reduzindo o impacto de itens
que repetem um edital entre os vizinhos. O limite final é K oportunidades
únicas. Consultas com filtros têm HNSW iterative scan habilitado para mitigar
perda de candidatos causada pela aplicação de filtros após o scan vetorial.

As buscas Top-K e as execuções locais com `k` explícito continuam usando
`NLP_RETRIEVAL_TOP_K` (o `.env.example` configura 100). Batches enfileirados
sem `k` explícito agora congelam a lista de oportunidades `ABERTA` e
`ENCERRANDO_EM_BREVE` e avaliam todos os IDs em blocos de 200. A comparação
vetorial do bloco usa os embeddings persistidos e calcula a maior similaridade
por CNAE e tipo de segmento, sem depender da posição de cada item no HNSW.
Resultados e offset do bloco são persistidos na mesma transação; uma tentativa
seguinte retoma do primeiro bloco não confirmado. O job renova o lease após
cada bloco. Resultados de batches `RUNNING` ou `FAILED` não aparecem nas rotas
de consulta, evitando publicação parcial. Editais sem vetor utilizável recebem
`SEMANTIC_DATA_MISSING` e são avaliados apenas com as evidências disponíveis.
O limite de tamanho do bloco controla memória e duração de transações, não
reduz a cobertura dos editais congelados no snapshot.

Os filtros estruturados são opcionais e não são inferidos do perfil:

- `modality_codes`: códigos PNCP armazenados nos metadados normalizados;
- `ibge_prefixes`: prefixos de código IBGE para restringir região;
- `derived_statuses`: estados locais já derivados.

Sem esses argumentos, nenhum desses filtros exclui oportunidades. Os códigos
das 14 modalidades continuam campos estruturados; eles não criam modelos,
índices ou embeddings separados. A recuperação pode usar objeto e itens, e a
deduplicação não multiplica uma contratação por CNAE ou item.

## Migração e serviço PostgreSQL

O host PostgreSQL precisa carregar pgvector 0.8.x. O Compose do Pipeline usa a
imagem oficial pgvector para PostgreSQL 16. A migration cria a extensão e
converte os vetores JSONB existentes. Em um deploy com PostgreSQL externo, a
extensão precisa estar instalada no servidor antes de `alembic upgrade head`.
O worker Matching continua conectado exclusivamente ao Analytics; não há
conexão com o banco do Backend.

## Avaliação

Depois de aplicar a migration, preencher embeddings e manter o conjunto local
normalizado correspondente ao benchmark:

```powershell
.venv\Scripts\python.exe scripts\benchmark_nlp_retrieval.py .local\nlp\pncp-ampliado-revisado.json --k 20 --output .local\nlp\retrieval-hnsw.json
```

O script restringe a comparação aos editais julgados do dataset e registra
Recall@5/10/20 contra os rótulos, Recall@K do HNSW contra a recuperação exata,
latência p50/p95, versão/extensão, tamanho do índice, total de vetores, CPU e
RSS do processo. O modo exato desativa index e bitmap scans apenas na transação
de referência. Os rótulos atuais são curadoria assistida, ainda sem validação
humana dupla; portanto, os números são diagnósticos, não medida de qualidade
final. A medição de CPU/RSS do processo não representa o consumo total do
PostgreSQL nem substitui a avaliação na VPS.

Para configurar K na aplicação, use `NLP_RETRIEVAL_TOP_K`; para uma execução
específica, informe `k` diretamente. O argumento `overfetch` controla a
amostragem por CNAE antes da consolidação e deve ser medido quando o perfil
contiver muitos CNAEs ou o edital tiver muitos itens.
