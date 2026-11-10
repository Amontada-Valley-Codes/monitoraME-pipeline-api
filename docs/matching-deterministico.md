# Hybrid matching engine

`calcular_matching` usa `hybrid-matching-v5`. Resultados registram as versoes do schema, das regras, da taxonomia e do modelo. Para reproduzir, fixe `as_of` e as versoes dos dados. Use um novo `batch_id` ao recalcular snapshots anteriores.

## Relevance score

O score de relevancia (0-100) soma criterios com pesos fixos: categoria 20%, objeto 10%, informacao complementar 20%, itens/lotes 35% e associacao CNAE-categoria 15%. Criterios ausentes contribuem zero e reduzem `data_confidence`, sem renormalizacao. Objeto, informacao complementar e itens recebem sinais semanticos separados. O cosseno E5 e calibrado acima do piso conservador 0,88 (`(score - 0,88) / 0,12`); abaixo dele, a similaridade semantica contribui zero. Esse piso ainda requer avaliacao com corpus rotulado. O texto lexical compara cada CNAE separadamente e remove termos genericos como servicos, manutencao e reparacao. `informacaoComplementar` e normalizada como `dados_semanticos.informacao_complementar` e projetada em segmento proprio. Os valores exibidos em `retrieval_segment.score` sao similaridades cosseno brutas para diagnostico, nao score de compatibilidade. `semantic_relevance` e calibrada em 0-100. HIGH exige score de ao menos 75 e cobertura de evidencias de ao menos 50 pontos de peso.

O perfil interno da contratação aceita até 10.000 descrições de itens para não descartar editais com listas extensas (por exemplo, compras com 600 itens). A pontuação lexical percorre as descrições completas disponíveis na visão normalizada.

Niveis: LOW 0-49, MEDIUM 50-74 e HIGH 75-100. Os pesos e cortes precisam de validacao humana antes de uso comercial.

## Preferencias

`preference_score` e `preference_confidence` ficam separados da relevancia e da elegibilidade. Os pesos sao regiao 50%, modalidade 25% e preferencias operacionais 25%, renormalizados sobre criterios comparaveis. `location` e a sede e nao implica interesse geografico. Regiao so e comparada se `interested_states` ou `interested_municipalities` vierem explicitamente configurados. Preferencias ausentes nao excluem oportunidades.

## Elegibilidade verificavel

O gate avalia situacao e prazo. Porte so e comparado quando `accepted_company_sizes_source` informa a proveniencia; sem fonte, gera `COMPANY_SIZE_UNCONFIRMED` e nao reprova. Territorio e modalidade nao sao gates juridicos. O motor sempre inclui `LEGAL_ELIGIBILITY_NOT_VERIFIED`: `ELIGIBLE` quer dizer apenas que os campos verificaveis disponiveis nao apontaram impedimento; nao garante habilitacao legal.

## Execucao local e persistencia

`python -m app.nlp.cli run-matching --fixtures tests/nlp/fixtures/companies.json --batch-id <uuid> --k 20` executa um batch imediatamente. Use `python -m app.nlp.cli enqueue-matching --fixtures tests/nlp/fixtures/companies.json --batch-id <uuid>` para gravar o snapshot e agendar trabalho na fila PostgreSQL; o worker `matching-nlp-worker` consome `nlp_matching_batch` junto aos jobs de embeddings e só carrega o modelo quando há batch pronto. No Compose, o processo leve fica ativo para consumir batches enviados pela API e descarrega os pesos após ociosidade. Mesmo `batch_id` e payload são idempotentes; payload diferente com o mesmo id causa conflito. Os endpoints HTTP internos autenticados aceitam o mesmo contrato `matching.v1`. Configure o modelo/cache, o token de serviço e aplique as migrations antes da execução.

Os resultados sao snapshots versionados e nao disparam notificacoes. O NestJS, endpoints HTTP e autenticacao de servico ficam para a etapa de integracao documentada.
