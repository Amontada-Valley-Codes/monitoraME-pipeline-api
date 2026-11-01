# Motor determinístico de matching

`app.nlp.matching.engine.calcular_matching` recebe um snapshot `CompanyProfile`,
uma `ProcurementProfile` normalizada, a revisão explícita da taxonomia CNAE-
categoria e um instante opcional (`as_of`). Fixar `as_of` e as versões dos
dados reproduz o mesmo resultado. A versão da regra é
`deterministic-matching-v1`; cada resultado registra também `taxonomy_version`.

## Compatibilidade e confiança

O score público é 0–100. Os pesos são categoria 30, objeto/descrição/itens 25,
CNAE 20, região 15 e preferências operacionais 10. Cada critério disponível
retorna 0–1. Critérios sem entrada não viram zero: o score é a média ponderada
dos critérios conhecidos e `data_confidence` é a soma, em percentual, dos pesos
com evidência disponível. Assim, ausência reduz a confiança, sem afirmar
incompatibilidade.

Texto usa Jaccard de tokens após normalização Unicode NFKC e casefold; não usa
embedding nem fonte externa. Categorias e CNAEs consultam o mapa versionado
passado ao motor. Cada critério avaliado aparece em `evidence`; códigos de
ausência ficam em `warning_codes`. Uma pontuação de 75 ou mais exige ao menos
um critério de atividade disponível (categoria, objeto/itens ou CNAE). Sem essa
evidência, o nível fica limitado a MEDIUM. Níveis: LOW 0–49, MEDIUM 50–74 e
HIGH 75–100.

Este score é uma ordenação para revisão. Não comprova habilitação legal. A
classificação lexical é deliberadamente simples e exige validação contra rótulos
antes de ser usada como qualidade de ranking em produção.

## Gate de elegibilidade

O gate roda antes da pontuação. Reprovação conhecida retorna `INELIGIBLE`, score
zero e motivos explícitos. Ausência/ambiguidade retorna `NOT_CONFIRMED`; quando
não houver reprovação e os campos do gate estiverem completos, retorna
`ELIGIBLE`. Campos opcionais cobrem status aberto, prazo, porte aceito,
território, forma de participação e invalidação. `PUBLICADA` não confirma por si
só que a oportunidade está aberta. `INELIGIBLE` e `NOT_CONFIRMED` continuam
sendo resultados pesquisáveis; consumidores podem aplicar filtros próprios.

## Recálculo por alteração de perfil

`agendar_recalculo_perfil_session` agenda na fila PostgreSQL um job
`nlp_matching`, deduplicado por empresa, `profile_version` e versão da regra.
Deve ser chamado na transação que aceita uma nova versão do snapshot. A fila é
idempotente para repetição da mesma versão. O worker consumidor e a persistência
dos resultados continuam sendo responsabilidade da integração de batches, que
ainda não possui handlers neste serviço.

## Validação

`tests/nlp/test_matching.py` cobre soma de pesos, limites 49/50/74/75/89/90,
reprodutibilidade, evidências, redução de confiança, gates e chave idempotente.
O diagnóstico realizado está em [`matching-validacao-amostra.md`](matching-validacao-amostra.md).
O dataset PNCP assistido documentado em `nlp-validacao-embeddings.md` tem
rótulos ainda pendentes de validação humana e não é ground truth para aprovar
este motor lexical. Rever falsos positivos/negativos requer congelar e validar
humanamente esses rótulos antes de interpretar métricas como avaliação do
matching determinístico.
