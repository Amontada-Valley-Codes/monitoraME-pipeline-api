# PNCP P?s-Ingest?o 01/05 ? homologa??o sem?ntica

Vers?o: `pncp-semantica-1`. Data: 2026-10-21. **Homologa??o t?cnica: APROVADA.**

A aprova??o ? t?cnica, baseada em revis?o assistida, contratos oficiais, testes e verifica??es em PostgreSQL real. N?o atribui assinatura humana nem certifica universalmente todos os dados futuros. O recorte homologado cobre publica??o e atualiza??o; carga hist?rica e enriquecimento continuam etapas pr?prias.

## Resultado e evid?ncias

- OpenAPI p?blico vigente consultado e identificado por SHA-256 na [matriz versionada](pncp-qualidade-matriz.json).
- Duas respostas reais de 10 registros cada: 20 observa??es e 19 RAW distintos por deduplica??o. Acrescentado um controle sint?tico inv?lido, explicitamente identificado.
- PostgreSQL 16.15 local exclusivo: cadeia completa de migrations e roundtrip downgrade/upgrade da 0017 executados.
- 63 verifica??es de integra??o aprovadas: persist?ncia, worker, quarentena confirmada ap?s exce??o, continuidade do lote saud?vel, replay autorizado, RAW imut?vel, eventos idempotentes, FK e filtros de m?tricas.
- 100% dos RAW da amostra avaliados; zero duplicatas RAW, zero registros saud?veis sem entidade can?nica e nenhuma quarentena nos dados reais. A ?nica quarentena remanescente ? o controle negativo intencional.
- Su?te versionada: 308 testes executados, 306 passaram, zero erros/falhas; dois testes de integra??o de porte empresarial s?o opt-in de outro m?dulo, sem rela??o com PNCP e desabilitados pela pr?pria su?te.
- Os dois timeouts anteriores foram resolvidos isolando grava??o de cache/logs em banco nos testes de limpeza/merge; esses 37 testes passaram.

Os relat?rios [homologa??o](pncp-qualidade-homologacao.json), [exce??es e m?tricas](pncp-qualidade-evidencias.json) e [testes](pncp-qualidade-testes.json) s?o os artefatos de entrega. As amostras est?o em [publica??o](pncp-qualidade-raw/publicacao.json) e [atualiza??o](pncp-qualidade-raw/atualizacao.json), com URL, captura e payload integral de cada p?gina. N?o constituem carga hist?rica.

## Rastreabilidade documental

O [workflow fornecido pelo solicitante](workflow-pncp-monitorame.md) fundamenta DOC-PNCP-06 (?6: RAW/vers?es), DOC-PNCP-09 (?9: jobs/replay), DOC-PNCP-10 (?10: observabilidade) e DOC-PNCP-14 (?14: aceite). Collector, RAW, fila e mecanismos de upsert/replay entregues foram reutilizados; sem RabbitMQ e sem PCA.

Fontes prim?rias verificadas: [OpenAPI de consultas](https://pncp.gov.br/api/consulta/v3/api-docs), [manual PNCP, v2.6, ?11.5](https://pncp.gov.br/manual/pt-br/latest/singlehtml/) e [c?lculo do DV pela Receita Federal](https://www.gov.br/receitafederal/pt-br/centrais-de-conteudo/publicacoes/documentos-tecnicos/cnpj). O contrato fixa quatro casas monet?rias, sigilo 1/2/3 e hor?rio de Bras?lia para abertura/encerramento de propostas. Os demais timestamps sem offset conservam a incerteza.

## Matriz por campo e endpoint

Todas as linhas s?o instanciadas separadamente para `/v1/contratacoes/publicacao` e `/v1/contratacoes/atualizacao` no JSON da matriz, com origem, tipos, obrigatoriedade, destino, estados e refer?ncias aos RAW reais.

| Origem RAW | Tipo | Obrigat?rio | Regra de normaliza??o | Destino |
|---|---|---|---|---|
| `numeroControlePNCP` | identidade | sim | String n?o vazia igual ao external_id do RAW; diverg?ncia isola. | `external_id` |
| `orgaoEntidade.cnpj` | cnpj | n?o | String de 14 caracteres; aceita CNPJ num?rico/alfanum?rico e valida DV por ASCII?48/m?dulo 11; mai?sculas e zeros preservados. | `orgao.cnpj` |
| `unidadeOrgao.codigoIbge` | ibge | n?o | String ASCII de sete d?gitos, com zeros preservados. Validade sint?tica; n?o afirma exist?ncia no cat?logo territorial. | `unidade.codigoIbge` |
| `objetoCompra` | texto | n?o | Texto com espa?os externos removidos; vazio opcional permanece ausente. | `objeto` |
| `valorTotalEstimado` | dinheiro | n?o | Decimal(str(valor)), sem arredondar; at? quatro casas conforme PNCP. Float ou precis?o maior gera warning; negativo/n?o finito/tipo inv?lido isola. | `dados_semanticos.valor_total_estimado` |
| `valorTotalHomologado` | dinheiro | n?o | Decimal(str(valor)), sem arredondar; at? quatro casas conforme PNCP. Float ou precis?o maior gera warning; negativo/n?o finito/tipo inv?lido isola. | `dados_semanticos.valor_total_homologado` |
| `orcamentoSigiloso` | booleano | n?o | Somente bool legado; 0/string n?o s?o convertidos em booleano. | `dados_semanticos.orcamento_sigiloso` |
| `orcamentoSigilosoCodigo` | sigilo | n?o | C?digos 1/2/3 preservados; 2/3 indicam sigilo parcial/total. C?digo novo preservado com warning. | `dados_semanticos.orcamento_sigiloso_codigo` |
| `orcamentoSigilosoDescricao` | texto | n?o | Texto com espa?os externos removidos; vazio opcional permanece ausente. | `dados_semanticos.orcamento_sigiloso_descricao` |
| `dataAberturaProposta` | data | condicional (instrumento 1/2) | Civil permanece data. Com offset: UTC + origem. Prazos sem offset: fuso Bras?lia documentado; outros hor?rios sem fuso s?o preservados com warning. Hor?rio hist?rico amb?guo n?o vira instante. | `dados_semanticos.data_abertura_proposta` |
| `dataPublicacaoPncp` | data | n?o | Civil permanece data. Com offset: UTC + origem. Prazos sem offset: fuso Bras?lia documentado; outros hor?rios sem fuso s?o preservados com warning. Hor?rio hist?rico amb?guo n?o vira instante. | `dados_semanticos.data_publicacao` |
| `dataAtualizacaoGlobal` | data | n?o | Preserva a sem?ntica civil/local/instante e fornece preced?ncia global quando compar?vel. | `dados_semanticos.data_atualizacao_global` |
| `tipoInstrumentoConvocatorioCodigo` | instrumento | n?o | C?digos 1/2 exigem abertura/encerramento; 3 admite aus?ncia. C?digo novo preservado com warning. | `dados_semanticos.instrumento_codigo` |
| `dataAtualizacao` | data | n?o | Civil permanece data. Com offset: UTC + origem. Prazos sem offset: fuso Bras?lia documentado; outros hor?rios sem fuso s?o preservados com warning. Hor?rio hist?rico amb?guo n?o vira instante. | `dados_semanticos.data_atualizacao` |
| `dataEncerramentoProposta` | data | condicional (instrumento 1/2) | Civil permanece data. Com offset: UTC + origem. Prazos sem offset: fuso Bras?lia documentado; outros hor?rios sem fuso s?o preservados com warning. Hor?rio hist?rico amb?guo n?o vira instante. | `prazo_proposta_final` |
| `modalidadeId` | modalidade | n?o | C?digo externo inteiro/string; dom?nio local 1..13. Desconhecido ? preservado com warning. | `dados_semanticos.modalidade_codigo` |
| `situacaoCompraId` | situacao | n?o | C?digo externo inteiro/string; dom?nio 1..4 do OpenAPI. Desconhecido ? preservado com warning. | `dados_semanticos.situacao_codigo` |

## Severidade, qualidade e sincroniza??o

Origem por campo: `absent`, `null`, `empty`, `present`; zero e false s?o presentes. Aus?ncia opcional ? `missing`, sem fabricar valores; viola??o de contrato de campo presente ? `error` e isola somente aquele RAW. Falhas apontam RAW ID, campo e motivo espec?fico. `valid`, `partial`, `invalid` s?o estados de qualidade; registros anteriores ? migration ficam `not_evaluated` at? replay.

`valorTotalEstimado=0` vira valor sem?ntico nulo com warning, tanto em sigilo quanto sem evid?ncia de gratuidade. Sigilo bool legado e c?digos atuais 2/3 s?o reconhecidos. O zero original permanece no RAW e n?o entra como contrata??o gratuita. A origem real omitiu atributos de sigilo em parte da amostra: isso ? exposto como aus?ncia, sem presumir publicidade do or?amento.

A amostra ? semanticamente parcial, n?o corrompida: h? valores monet?rios originalmente desserializados como float, aus?ncia de campos opcionais e timestamps sem offset. Decimal preserva a representa??o decimal dispon?vel; n?o recupera d?gitos j? perdidos antes do RAW. Essas limita??es s?o warnings publicados por campo, sem descarte silencioso nem falsa declara??o de precis?o plena.

Prazos sem offset usam `America/Sao_Paulo` conforme o manual, com `tzdata` declarado como depend?ncia direta; preservam valor original, fuso contratual e offset aplicado, sem afirmar que o offset veio no payload. Datas civis nunca viram instantes. Outros timestamps sem fuso ficam locais. A resposta ao consumidor reconverte instantes para UTC, mesmo que a sess?o PostgreSQL os apresente em outro offset. A atualiza??o global tem preced?ncia quando presente nos dois lados com a mesma sem?ntica. Vers?es com datas locais compar?veis s?o ordenadas sem lhes inventar UTC; rel?gios/sem?nticas incompar?veis ficam `AMBIGUO`.

Sincroniza??o da entidade: `pending`, `syncing`, `synchronized`, `partially_synchronized`, `stale`, `source_unavailable`, `quarantined`. A fun??o de estado recebe sinais expl?citos; o DTO b?sico deriva apenas da qualidade, sem alegar enriquecimento conclu?do. Jobs mant?m estados pr?prios em mai?sculas (`PENDING`, `RUNNING`, `SUCCEEDED`, etc.).

Upsert preserva vers?es antigas, empates sem preced?ncia e snapshots que apaguem dados b?sicos conhecidos, inclusive CNPJ/IBGE aninhados. Decis?es `ANTIGO`, `AMBIGUO`, `INCOMPLETO` s?o publicadas no relat?rio. Reprocessamento da mesma regra/hash n?o duplica entidade/eventos; outra regra pode reavaliar o mesmo RAW.

## M?tricas e relat?rio de exce??es

`PncpAvaliacaoQualidade` registra por `(raw_record_id, rule_version)` endpoint, run/p?gina, estado, decis?o, campos, tentativas e hor?rio. Completude = presentes / avaliados; validade estrita = valid / avaliados. Warning n?o ? validade plena. A cobertura compara todos os RAW de contrata??es com avalia??es da regra atual, incluindo o backlog ainda n?o avaliado.

Unicidade ? medida por identidade/vers?o, n?o por c?digos naturalmente repetidos como modalidade. A consulta retorna entidades e vers?es distintas, duplicatas RAW, quarentena ativa, vers?es can?nicas e registros saud?veis sem entidade normalizada, por run/endpoint. Uma vers?o hist?rica substitu?da n?o ? um ?rf?o. Constraints/FKs reais foram exercitadas na homologa??o.

Exce??es incluem RAW ID, campo, motivo, impacto, decis?o, regra e evid?ncia de reprocessamento. O hist?rico do job de recupera??o cont?m `STARTED ? QUARANTINED ? REPLAYED ? STARTED ? SUCCEEDED`; a falha foi injetada na regra de um RAW real para provar recupera??o ap?s corre??o, sem editar nem recoletar o payload. O controle de CNPJ inv?lido permanece isolado intencionalmente.

O endpoint vem da proveni?ncia do job coletor. Sem evid?ncia, fica `unknown`; nunca ? inventado. A deduplica??o RAW conserva a primeira proveni?ncia: a mesma vers?o observada nos dois fluxos aparece uma vez no banco, e ambas as observa??es constam no manifesto das capturas.

## Reprodu??o e passagem ?s etapas 02/05

1. Preparar PostgreSQL local descart?vel, nome `pncp_quality_*`, com `DATABASE_URL`/`LOG_DATABASE_URL` apontando explicitamente para esse banco e SSL conforme o ambiente. O script rejeita banco fora de localhost ou sem esse prefixo.
2. Executar `python -m alembic upgrade head` somente nesse banco novo. O roundtrip de migration foi testado nele antes de inserir amostras.
3. Executar `python scripts/homologar_pncp_qualidade.py --diretorio-raw docs/pncp-qualidade-raw --saida relatorio-homologacao.json`. O banco deve estar vazio de RAW; uma nova execu??o usa outro banco dedicado, preservando o anterior.
4. Consultar/exportar: `python scripts/relatorio_pncp_qualidade.py --saida relatorio-qualidade.json`. Filtros opcionais `--run-id "job:1:pagina:1" --endpoint /v1/contratacoes/publicacao`.

O pr?ximo respons?vel recebe matriz aprovada tecnicamente, capturas, hashes, testes e decis?es de exce??o neste diret?rio. Crit?rios do p?s-ingest?o 01/05 foram comprovados no recorte. Etapas 02/05 devem consumir `data_quality_status` e tratar os warnings documentados; esta entrega n?o executa essas etapas. Arquivos locais legados de backfill n?o versionados foram preservados, sem integr?-los ao m?dulo PNCP entregue. Nenhum banco configurado no `.env` foi migrado; apenas o cluster exclusivo de homologa??o.
