# Matching lexical: diagnostico da amostra

Snapshot avaliado: `.local/nlp/pncp-ampliado-revisado.json`, versao
`pncp-amostra-v3-curadoria-assistida-15x40`; 15 perfis, 40 oportunidades e 600
julgamentos. O arquivo declara `curadoria_assistida_pendente_de_validacao_humana`.

## Procedimento

Para cada perfil, cada CNAE foi comparado com os segmentos textuais da
oportunidade usando o mesmo Jaccard de tokens do motor (Unicode NFKC,
casefold e palavras com mais de um caractere). A maior similaridade por
oportunidade foi usada no ranking. Para as metricas de relevancia, somente o
rotulo 2 foi positivo; nDCG preserva os rotulos 0, 1 e 2. O diagnostico isola o
criterio textual: o snapshot nao contem os metadados estruturados de gate,
preferencias ou cobertura completa da taxonomia usados pelo motor.

## Resultados

| Metrica | Resultado |
|---|---:|
| Macro Precision@5 | 0.0933 |
| Macro Precision@10 | 0.0600 |
| Macro Recall@5 | 0.1088 |
| Macro Recall@10 | 0.1532 |
| Macro nDCG@10 | 0.2022 |
| Positivos diretos no rotulo 2 | 41 |
| Positivos acima do corte lexical 0.75 | 0 |

Nao houve falso positivo ao exigir score lexical 0.75, pois nao houve predicoes
positivas; os 41 positivos rotulados ficaram abaixo do corte. O Top 5 recuperou
7 de 75 pares e nDCG baixo mostra que esta regra e uma baseline deterministica,
mas nao sustenta alegacao de bom ranking. Os rótulos sao assistidos, nao foram
validados por revisores humanos neste trabalho e nao representam elegibilidade
legal.

O snapshot TF-IDF/E5 anterior tem metricas melhores em `nlp-validacao-
embeddings.md`, mas e um estudo separado e tambem pendente de revisao humana.
Uma proxima decisao de modelo deve congelar rotulos revisados, analisar os
falsos positivos e negativos por perfil e manter a elegibilidade como gate
deterministico separado.
