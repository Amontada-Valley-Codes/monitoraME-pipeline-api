Implementação local concluída: consulte o [contrato das rotas mensais e validações](portes-mensais.md). O texto abaixo preserva os resultados e limites do piloto que fundamentou a implementação.

# PEN-003 — Resultado do piloto com dados reais

Execução concluída em 2026-10-14T19:22:49.188139+00:00.

**Recomendação: seguir com a implementação de observações mensais de porte, com cobertura e procedência explícitas. Não implementar porte vigente na data exata do empenho a partir dessas fontes.**

## Recorte efetivamente validado

A coleta TCE-CE abrangeu os 184 municípios compradores do Ceará em janeiro de 2025: 100750 empenhos, 535 anulações e 184 lotes publicados. Os fornecedores podem ter sede em qualquer UF; o recorte é do município comprador.

Foram selecionados 8744 CNPJs reais, correspondentes a 8366 raízes. As inscrições selecionadas passaram pela validação de dígitos verificadores do pipeline. A análise cadastral foi feita por raiz, conforme o campo de porte da Receita.

Foram baixados e lidos integralmente 22 ZIPs oficiais das competências 2025-01 e 2026-10, totalizando 3158053055 bytes compactados. Foram verificados tamanho, SHA-256, CRC, estrutura dos registros e duplicidades de raízes selecionadas. Nenhum dado fictício foi utilizado.

## Cobertura por fotografia

| Competência | Raízes encontradas | Porte identificado | Porte não informado | Raízes ausentes | Cobertura cadastral |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2025-01 | 8348 | 8348 | 0 | 18 | 99.78% |
| 2026-10 | 8363 | 8363 | 0 | 3 | 99.96% |

Cobertura cadastral mede presença da raiz em EMPRESAS, incluindo porte não informado; não mede existência de cada estabelecimento nem cobertura nacional. Ausência e porte 00 permanecem distintos. Não houve preenchimento retroativo de lacunas.

## Mudanças observadas

Foram comparáveis 8348 raízes com porte identificado em ambas as fotografias; 274 apresentaram códigos diferentes.

| Porte na primeira fotografia | Porte na segunda fotografia | Raízes |
| --- | --- | ---: |
| DEMAIS | EPP | 7 |
| DEMAIS | ME | 4 |
| EPP | DEMAIS | 71 |
| ME | DEMAIS | 66 |
| ME | EPP | 126 |

A diferença mostra mudança entre dois retratos cadastrais. Não demonstra o dia da mudança, sua vigência legal, sua causa ou o porte de todos os meses intermediários. Mudanças de/para porte desconhecido não foram contadas como transições de porte identificado.

## Simples e MEI, separados do porte

| Competência | Raízes no arquivo Simples | Ausentes no Simples | Opção MEI S | Opção MEI N | Indicador vazio |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2025-01 | 5651 | 2715 | 703 | 4948 | 0 |
| 2026-10 | 5714 | 2652 | 511 | 5203 | 0 |

Indicadores e datas tributárias reais foram preservados no SQLite, sem converter ausência em não optante. O indicador MEI não substituiu o código cadastral ME/EPP/demais neste piloto. A implementação futura deve definir explicitamente a composição das categorias e a referência mensal, sem usar a condição atual para reclassificar o passado.

## Evidência e limites temporais

A validação final usou a versão local preservada dos 22 arquivos oficiais, com recálculo dos hashes, pois a reconsulta dos metadados remotos terminou em RemoteDisconnected. O modo está registrado no JSON como VERSAO_PRESERVADA_SEM_REVALIDACAO_REMOTA. Durante os downloads também houve resposta 504; as novas tentativas permitiram concluir todos os arquivos. Não se declara confirmação remota posterior ao download.

As competências são as pastas oficiais de publicação. O relatório JSON registra URLs, ETags, Last-Modified, hashes e nomes/datas internas dos membros dos ZIPs. Esses metadados não comprovam o início ou fim da vigência do porte; a referência exata da extração continua marcada como não comprovada. Não foram utilizados snapshots posteriores como evidência de porte na data de um empenho.

O piloto valida duas fotografias, não uma série mensal contínua entre elas. Tampouco valida todos os estabelecimentos em ESTABELECIMENTOS, faturamento, enquadramento jurídico ou todos os ciclos de opção/exclusão tributária.

## Encaminhamento técnico

GO para uma tabela de observações separada do cache atual, por raiz/competência/versão do artefato, com conservação dos arquivos e fontes. A API deve informar competência, procedência, cobertura e desconhecidos, e adotar uma regra temporal documentada que impeça usar observações futuras. O armazenamento precisa ser persistente.

NO-GO para vigência diária do porte ou reconstrução de eventos de reenquadramento. Isso depende de uma fonte adicional com eventos e datas de vigência verificáveis. A implementação produtiva também precisa validar as competências adicionais exigidas pelo produto; a disponibilidade de duas fotografias não garante retenção permanente nem SLA de download.

As observações deste piloto estão no SQLite separado do cache cadastral da aplicação. Este resultado não declara integração histórica em produção.

## Artefatos e reprodução

- Resultado detalhado: `artifacts/pen-003/20261014T191044138270Z/resultado.json`.
- Observações reais e inscrições: `artifacts/pen-003/20261014T191044138270Z/observacoes.sqlite3` (16732 observações, incluindo ausências explícitas).
- Coleta municipal e backup: `artifacts/pen-003/coleta-ce-2025-01/`.
- ZIPs preservados: `artifacts/pen-003/rfb-cache/`.
- Comandos de reprodução: [coleta CE](PEN-003-coleta-ce.md).

Fontes: [layout oficial](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf), [compartilhamento oficial CNPJ](https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9) e [investigação inicial no código](PEN-003-historico-porte-cnpj.md).


