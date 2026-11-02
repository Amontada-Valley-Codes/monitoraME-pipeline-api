# Hybrid matching engine

`calcular_matching` usa `hybrid-matching-v2`. Resultados registram as versoes do schema, das regras, da taxonomia e do modelo. Para reproduzir, fixe `as_of` e as versoes dos dados.

## Relevance score

O score de relevancia (0-100) usa a media ponderada dos criterios disponiveis: categoria 40%, texto de objeto/itens 35% e associacao CNAE-categoria 25%. Criterios ausentes reduzem `data_confidence`, sem serem tratados como incompatibilidade. O texto combina Jaccard lexical (NFKC e casefold) com o score cosseno Top-K: usa o maior dos dois, e o sinal semantico recebido e limitado ao intervalo 0-1. `semantic_relevance` preserva esse sinal em 0-100. A formula e um baseline, nao uma calibracao empirica aprovada.

Niveis: LOW 0-49, MEDIUM 50-74 e HIGH 75-100. HIGH exige evidencia de atividade (categoria, texto ou CNAE). Os pesos e cortes precisam de validacao humana antes de uso comercial.

## Preferencias

`preference_score` e `preference_confidence` ficam separados da relevancia e da elegibilidade. Os pesos sao regiao 50%, modalidade 25% e preferencias operacionais 25%, renormalizados sobre criterios comparaveis. `location` e a sede e nao implica interesse geografico. Regiao so e comparada se `interested_states` ou `interested_municipalities` vierem explicitamente configurados. Preferencias ausentes nao excluem oportunidades.

## Elegibilidade verificavel

O gate avalia situacao e prazo. Porte so e comparado quando `accepted_company_sizes_source` informa a proveniencia; sem fonte, gera `COMPANY_SIZE_UNCONFIRMED` e nao reprova. Territorio e modalidade nao sao gates juridicos. O motor sempre inclui `LEGAL_ELIGIBILITY_NOT_VERIFIED`: `ELIGIBLE` quer dizer apenas que os campos verificaveis disponiveis nao apontaram impedimento; nao garante habilitacao legal.

## Execucao local e persistencia

`python -m app.nlp.cli run-matching --fixtures tests/nlp/fixtures/companies.json --batch-id <uuid> --k 20` executa um batch imediatamente. Use `python -m app.nlp.cli enqueue-matching --fixtures tests/nlp/fixtures/companies.json --batch-id <uuid>` para gravar o snapshot e agendar trabalho na fila PostgreSQL; o worker `matching-nlp-worker` consome `nlp_matching_batch` junto aos jobs de embeddings e carrega o modelo uma vez. Mesmo `batch_id` e payload sao idempotentes; payload diferente com o mesmo id causa conflito. `python -m app.nlp.cli matching-status --batch-id <uuid>` consulta estado/contagens e `python -m app.nlp.cli matching-results --company-id <uuid>` lista resultados persistidos. Configure o modelo/cache e aplique as migrations antes da execucao.

Os resultados sao snapshots versionados e nao disparam notificacoes. O NestJS, endpoints HTTP e autenticacao de servico ficam para a etapa de integracao documentada.
