# Validação local de embeddings para matching PNCP

## Estado e decisão

`intfloat/multilingual-e5-small` foi exercitado localmente em CPU com segmentos reais do corpus PNCP normalizado. O resultado justifica mantê-lo como candidato para a próxima rodada, mas ainda não homologa o modelo: os rótulos desta amostra foram sugeridos por leitura assistida e precisam de validação humana. Não houve acesso ou medição na VPS.

Versão executada: `intfloat/multilingual-e5-small@fd1525a9fd15316a2d503bf26ab031a61d056e98`.

Similaridade semântica serve para ordenar oportunidades para revisão. Não comprova habilitação, capacidade técnica, porte, localização elegível ou direito de participar. Essas condições exigem validação determinística separada.

## Dataset ampliado

- Arquivo reproduzível local: `.local/nlp/pncp-ampliado-revisado.json`.
- Versão: `pncp-amostra-v3-curadoria-assistida-15x40`.
- Origem: `nlp_procurement_segments` (segmentos PNCP normalizados).
- 15 perfis empresariais e 40 contratações, com 91 segmentos: 40 `OBJECT` e 51 `ITEM`.
- Os perfis têm CNAE principal e secundários, com 224 descrições CNAE no total; não restaram códigos sem descrição.
- Foram revisados os 600 julgamentos: 470 rótulos `0`, 89 `1` e 41 `2`; 25 rótulos foram alterados após aplicar a régua abaixo.
- Os rótulos são uma curadoria assistida pendente de validação humana, não ground truth. O conjunto não deve ser usado como decisão de elegibilidade legal.
- O perfil concatenado mais longo tem 1.139 tokens e três perfis excedem o limite de 512 tokens; as partes CNAE têm até 39 tokens cada e os segmentos até 173 nesta amostra.

Descrições dos códigos foram completadas a partir da tabela oficial CNAE-Subclasses 2.3 do IBGE/CONCLA. O fixture atualizado é `tests/nlp/fixtures/companies.json`.

## Protocolo e estratégias

O benchmark compara seis formas de formar o perfil:

1. `e5`: perfil concatenado, com todos os CNAEs;
2. `e5_per_cnae_max`: uma consulta por CNAE e máximo dos scores por oportunidade;
3. `e5_primary_only`: somente CNAE principal;
4. `tfidf_word_char`: referência lexical TF-IDF de palavras e caracteres sobre o perfil concatenado;
5. `tfidf_per_cnae_max`: referência lexical por CNAE com máximo por oportunidade;
6. `tfidf_primary_only`: referência lexical somente com o CNAE principal.

Para E5, o adaptador aplica `query: ` aos perfis e `passage: ` aos segmentos, normaliza vetores L2 e usa produto interno equivalente à similaridade cosseno. O modelo produz vetores de dimensão 384. A estratégia por CNAE evita truncar o conjunto de atividades secundárias como um único texto; para cada oportunidade considera o maior score entre as partes do perfil.

Precision@K e Recall@K consideram apenas o rótulo `2` positivo e estão agregados sobre os 15 perfis (micro). nDCG@10 preserva os níveis `0`, `1` e `2` e é a média entre perfis. Resultados são deduplicados por oportunidade, tomando o maior score entre seus segmentos. Nesta revisão, `2` significa relação direta entre atividade CNAE e objeto/item; `1`, relação plausível mas indireta ou insuficientemente especificada; `0`, ausência de relação semântica clara.

## Resultado desta execução local

| Estratégia | Precision@5 | Recall@5 | Precision@10 | Recall@10 | nDCG@10 médio |
|---|---:|---:|---:|---:|---:|
| E5 concatenado | 0,160 | 0,293 | 0,120 | 0,439 | 0,362 |
| E5 por CNAE, máximo | **0,160** | **0,293** | **0,133** | **0,488** | **0,371** |
| E5 somente principal | 0,133 | 0,244 | 0,100 | 0,366 | 0,268 |
| TF-IDF palavras + caracteres | 0,107 | 0,195 | 0,100 | 0,366 | 0,340 |
| TF-IDF por CNAE, máximo | 0,147 | 0,268 | 0,107 | 0,390 | 0,364 |
| TF-IDF somente principal | 0,093 | 0,171 | 0,067 | 0,244 | 0,250 |

Leitura cautelosa: E5 por CNAE teve Precision@5 empatada e os melhores Precision@10, Recall@5 e Recall@10 nesta amostra; nDCG@10 foi 0,371, contra 0,362 do E5 concatenado. A diferença para TF-IDF não é suficiente para declarar superioridade geral, sobretudo porque há apenas 15 perfis, 40 oportunidades e rótulos ainda não validados. Os 41 positivos também estão distribuídos de forma desigual entre os perfis.

### Recursos observados

Medição do processo local nesta execução, com `psutil` e amostragem de RSS a cada 50 ms:

- carga do modelo: 18,92 s de relógio e 14,86 s de CPU;
- codificação dos 15 perfis concatenados: 3,04 s de relógio;
- codificação das 224 partes de CNAE: 2,02 s de relógio;
- codificação dos segmentos: 2,24 s, aproximadamente 40,5 segmentos/s;
- RSS antes da carga: 77 MiB; pico durante carga e inferência: 1.362 MiB;
- RSS após a carga: 1.172 MiB; após inferência dos segmentos: 856 MiB.

RSS é memória residente do processo Python, não memória total da máquina. O valor de pico inclui o custo de inicialização e carregamento. Esta execução não mede concorrência, limites de memória ou desempenho na KVM 2; portanto, compatibilidade com a VPS permanece por verificar. Tempos dependem do hardware, número de threads, batch size e estado do cache.

### Comparação de batch size

Foram feitas duas execuções locais por lote (`1`, `4`, `8`, `16` e `32`), sempre em processos novos, com o mesmo dataset e modelo. A tabela mostra medianas das duas execuções; para RSS, mostra o maior pico observado entre elas. O tempo total de codificação soma perfil concatenado, partes CNAE e segmentos PNCP.

| Batch size | Perfil (s) | CNAEs (s) | Segmentos (s) | Codificação total (s) | Segmentos/s | CPU de codificação (s) | Pico RSS (MiB) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 2,60 | 5,29 | 2,81 | 10,69 | 32,5 | 85,54 | 1.198 |
| 4 | **1,71** | **3,28** | 2,70 | **7,70** | 34,0 | **61,36** | 1.220 |
| 8 | 4,50 | 3,34 | **2,19** | 10,03 | **41,6** | 65,69 | 1.219 |
| 16 | 3,78 | 3,66 | 3,63 | 11,07 | 26,7 | 71,33 | 1.370 |
| 32 | 3,42 | 2,40 | 3,98 | 9,80 | 23,1 | 72,33 | 1.333 |

Para este piloto de 15 perfis, 224 partes CNAE e 91 segmentos, o lote `4` é a melhor configuração provisória para o tempo total e CPU medidos; o lote `8` favorece apenas a codificação de segmentos. Os resultados de ranking foram idênticos entre lotes (diferença máxima zero nas métricas), como esperado: batch size afeta desempenho, não a ordenação neste adaptador. São apenas duas rodadas em uma máquina local; a variação entre execuções foi relevante, e a escala real do corpus pode mudar o perfil de memória. CPU em segundos é tempo acumulado das threads e pode exceder o tempo de relógio em processamento multithread.

## Reproduzir

No PowerShell, com as dependências instaladas em `.venv` e os pesos já baixados:

```powershell
$env:NLP_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"
$env:NLP_EMBEDDING_REVISION = "fd1525a9fd15316a2d503bf26ab031a61d056e98"
$env:NLP_EMBEDDING_DEVICE = "cpu"
$env:HF_HUB_OFFLINE = "1"
.venv\Scripts\python.exe scripts\benchmark_nlp_embeddings.py `
  .local\nlp\pncp-ampliado-revisado.json `
  --batch-size 16 `
  --output .local\nlp\resultado-ampliado-revisado.json
```

O relatório detalhado da execução está em `.local/nlp/resultado-ampliado-revisado.json`. A amostra assistida anterior permanece preservada em `.local/nlp/pncp-ampliado-assistido-original.json`. O dataset e os resultados são artefatos locais ignorados pelo Git, pois podem conter uma amostra de dados de origem e devem permanecer fora do repositório.

Para preparar outra amostra com os dados já sincronizados no banco local, sem carregar o modelo:

```bash
.venv/Scripts/python.exe scripts/benchmark_nlp_embeddings.py \
  --prepare-from-db \
  --companies tests/nlp/fixtures/companies.json \
  --companies-limit 15 \
  --limit 40 \
  --sample-output .local/nlp/pncp-ampliado.json
```

Os rótulos revisados foram preparados pelo assistente e continuam pendentes de validação humana; não os considere ground truth. Para comparação reproduzível, preserve o dataset, o identificador do modelo, dependências, batch size e versão do script.

## Casos que exigem tratamento complementar

- **Perfis longos:** concatenar muitos CNAEs pode exceder o limite de tokens e descartar atividades. Comparar a estratégia por CNAE e manter o principal identificado.
- **Textos curtos ou genéricos:** similaridade alta pode refletir palavras comuns sem aderência real; complementar com busca lexical, códigos, termos específicos e limiar calibrado com revisão humana.
- **Textos incompletos ou sem descrição:** sinalizar baixa confiança/abstenção e buscar dados complementares. Ausência de evidência não deve ser interpretada como incompatibilidade.
- **Siglas, códigos, marcas e expressões exatas:** busca textual ou regras explícitas podem recuperar correspondências que embeddings diluem.
- **Regras legais e CNAE autorizado:** devem ser codificadas e verificadas separadamente; ranking semântico não é comprovação legal.
- **Múltiplos itens:** preservar o vínculo entre item, oportunidade e score; uma correspondência em um item não significa aderência a todos os itens.

Próximo critério de decisão: revisar os 600 rótulos sugeridos, idealmente resolver divergências entre avaliadores, ampliar os setores e os negativos difíceis, congelar uma nova versão do dataset e repetir o benchmark. Só então comparar as estratégias novamente. Qualquer decisão operacional ainda depende de medição isolada na infraestrutura alvo, caso o usuário autorize essa etapa.
