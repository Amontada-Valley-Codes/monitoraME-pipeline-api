Atualização: a coleta dos 184 municípios e a validação dos arquivos reais foram concluídas. Consulte o [resultado final do piloto](PEN-003-resultado-piloto.md). As limitações de vigência exata permanecem válidas; os estados anteriores descritos neste documento registram etapas da investigação.

# PEN-003 — Disponibilidade de histórico de porte por CNPJ

Data da investigação: 14/10/2026. Status: investigação concluída; recomendação técnica registrada. Escopo: código local do monitoraME e fontes públicas oficiais/documentação do fornecedor. Não foram criados registros de empresas, dados sintéticos ou alterações no pipeline.

## Decisão recomendada

**Não seguir, neste momento, com a implementação de porte vigente na data exata do empenho. Seguir somente com um piloto de histórico mensal de porte observado, condicionado à validação dos arquivos reais e à aceitação dessa semântica pelo produto.**

A investigação não identificou no layout aberto da Receita nem na API CNPJ examinada uma série de eventos de alteração de porte ou uma consulta desse campo por data. Entretanto, o repositório oficial mantém pastas mensais: há possibilidade concreta de construir uma série por fotografia cadastral. Não se deve concluir que só existe o arquivo mais recente, nem equiparar fotografia mensal à vigência cadastral exata.

## Respostas às tasks

| Task | Resultado | Encaminhamento |
| --- | --- | --- |
| Investigar série histórica de porte por CNPJ | Encontradas pastas mensais no repositório oficial, de 2023-05 a 2026-10 na listagem consultada. Verificada presença de arquivos de empresas em três competências. Conteúdo longitudinal por empresa ainda não validado. | Piloto com arquivos reais; viabilidade documental positiva, validação empírica pendente. |
| Verificar porte por data na Receita | O layout disponibiliza porte sem início/fim de vigência. A API documentada não oferece filtro temporal de porte. | Não prometer porte vigente em uma data arbitrária. |
| Documentar recomendação de implementação | NO-GO para vigência exata; GO condicionado para piloto de retratos mensais. | Critérios de avanço abaixo. |

## Evidências externas

### E1 — Layout oficial dos dados abertos

Fonte: [Receita Federal — metadados CNPJ](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf), páginas 1 e 3, consultado em 14/10/2026.

A tabela EMPRESAS associa o porte ao CNPJ básico, de oito posições. Os códigos documentados são 00 (não informado), 01 (ME), 03 (EPP) e 05 (demais). Não há data de alteração ou intervalo de validade do porte. ESTABELECIMENTOS permite relacionar a raiz às inscrições completas. MEI não é uma categoria desse campo; sua identificação utiliza os dados do Simples, que incluem indicadores e datas de opção/exclusão de Simples e MEI. Essas datas não datam mudanças de porte ME/EPP/demais nem comprovam, por si, todos os ciclos anteriores.

Conclusão de E1: cada arquivo informa o estado cadastral da extração, sem trajetória completa de vigência. A data de situação cadastral, abertura ou situação especial não substitui uma data de mudança de porte.

### E2 — API governamental de consulta CNPJ

Fontes: [catálogo Conecta](https://www.gov.br/conecta/catalogo/apis/consulta-cnpj) e [documentação técnica](https://www.gov.br/conecta/catalogo/apis/consulta-cnpj/swagger_api_cnpj.md/swagger_view), consultados em 14/10/2026.

O catálogo descreve consultas Básica, QSA e Empresa por CNPJ. A documentação apresenta um campo de porte e períodos do Simples em estrutura separada. Não foi identificado parâmetro para selecionar uma data de referência do porte ou lista de alterações desse atributo. Períodos tributários não equivalem a histórico de porte. A consulta exige condições de acesso do Conecta; não foi executada chamada autenticada nem avaliada contratação de serviço.

### E3 — Arquivo oficial de competências anteriores

Fonte: [compartilhamento público CNPJ da Receita](https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9), consultado diretamente em 14/10/2026.

O navegador de pesquisa não carregou a listagem do compartilhamento. A verificação direta por HTTP funcionou: foi usada a operação de leitura PROPFIND no WebDAV público do mesmo compartilhamento, com Depth: 1. A autenticação utiliza como usuário o identificador público presente no link e senha vazia; não utiliza credencial privada.

Resultado da raiz: 42 pastas mensais de 2023-05 a 2026-10, além de cnpj.tar.gz. A existência dessas pastas não comprova integridade, conteúdo distinto ou cobertura de cada CNPJ.

| Competência inspecionada | Arquivos listados | Tamanho de Empresas0.zip | Tamanho de Simples.zip |
| --- | --- | ---: | ---: |
| 2023-05 | Empresas0.zip a Empresas9.zip e Simples.zip | 263675156 bytes | 199677339 bytes |
| 2025-01 | Empresas0.zip a Empresas9.zip e Simples.zip | 395456217 bytes | 247123756 bytes |
| 2026-10 | Empresas0.zip a Empresas9.zip e Simples.zip | 570972127 bytes | 310608217 bytes |

São metadados reais obtidos da listagem, não amostras fictícias. Os ZIPs não foram baixados, descompactados ou comparados nesta investigação. Não foi comprovada mudança de porte de nenhum CNPJ específico.

Observação relevante: Empresas0.zip da pasta 2023-05 apresenta Last-Modified de 20/05/2025; na pasta 2026-10, de 13/10/2026. Logo, Last-Modified não comprova data de corte cadastral. O nome da pasta deve ser tratado como competência de publicação, até validação da referência da extração. Não foi localizada garantia de retenção permanente ou imutabilidade desses arquivos.

A URL alternativa /dados/cnpj/dados_abertos_cnpj/ respondeu 404. Isso não foi interpretado como ausência de histórico, pois o compartilhamento acima respondeu normalmente.

### E4 — OpenCNPJ utilizado pelo pipeline

Fonte: [documentação OpenCNPJ](https://opencnpj.org/), incluindo o conteúdo de documentação entregue pelo JavaScript da própria página, consultado em 14/10/2026.

A documentação caracteriza receita como dataset cadastral de atualização mensal e expõe porte_empresa. Não foi identificado mecanismo documentado de consulta temporal do porte. Isso delimita a documentação consultada; não constitui prova de inexistência de recurso interno ou futuro do fornecedor.

## Evidências no código local

| Local | Comportamento observado | Consequência |
| --- | --- | --- |
| [ingestion/fornecedores.py](../app/pipeline/ingestion/fornecedores.py), _montar_requisicao_opencnpj | Consulta /{cnpj} com datasets=receita, sem data. | O período do empenho não seleciona uma versão cadastral. |
| Mesmo módulo, obter_fornecedores_com_cache | Cache encontrado tem TTL de 30 dias; preserva último resultado conhecido quando a renovação falha. | TTL define atualização, não vigência histórica. |
| [cleaners/opencnpj.py](../app/pipeline/cleaners/opencnpj.py), limpar_fornecedores | Marca fonte RECEITA_FEDERAL_VIA_OPENCNPJ, procedência RETRATO_ATUAL e observado_em no processamento; opção MEI positiva substitui a classificação por MEI. | A data observada é local; o indicador MEI também precisa de referência temporal antes de uso histórico. |
| [core/models.py](../app/core/models.py), FornecedorCache | Uma linha por CNPJ, com payload, dados normalizados e datas de observação/cache. | Não existe coleção de versões cadastrais no modelo. |
| [core/database.py](../app/core/database.py), salvar_fornecedores_cache | ON CONFLICT por CNPJ atualiza payload, classificação e observado_em. | Uma renovação substitui a observação anterior. |
| [persistence/tce_despesas.py](../app/pipeline/persistence/tce_despesas.py) | Consultas ligam empenhos ao FornecedorCache por CNPJ, sem junção temporal. | Atualizar o cache pode reclassificar despesas antigas. |
| [pipeline/analitico.py](../app/pipeline/analitico.py) | Transporta porte_fornecedor, fonte_porte, procedencia_porte e observado_em. | Há campos de procedência úteis, mas eles não criam uma série histórica. |

A versão de lote de empenhos não versiona automaticamente o cadastro dos fornecedores. No estado atual, a análise deve ser descrita como despesas do período classificadas pelo retrato cadastral disponível, não como porte comprovado no momento da despesa.

## Condições para avançar

1. Definir com o produto que o resultado será porte observado por competência, com cobertura explícita. A necessidade de vigência diária mantém o NO-GO até obtenção de fonte adequada.
2. Validar, em pelo menos duas competências, os arquivos reais completos de EMPRESAS para raízes de fornecedores efetivamente presentes no pipeline. Registrar URL, tamanho, hash, competência, data de coleta e referência da extração quando comprovável. Não presumir que o CNPJ esteja sempre no mesmo ZIP.
3. Conferir layout, integridade, duplicidades, nulos e correspondência entre CNPJ completo e raiz. Não publicar percentual de cobertura ou alegar mudanças antes dessa medição real. Porte não informado e ausência de arquivo/empresa precisam de estados distintos.
4. Verificar separadamente Simples/MEI nas mesmas competências. Não converter condição atual de MEI nem opção tributária em trajetória histórica de ME/EPP. Não substituir a ausência de histórico por receita, capital social ou outra inferência.
5. Se o piloto passar, projetar uma tabela de observações separada do cache atual, por raiz e competência, com fonte e versão do artefato. Conservar a inscrição completa nas despesas. Definir precedência de MEI e regra de associação temporal, sem utilizar observação futura ou preencher lacunas silenciosamente.
6. Expor competência cadastral, procedência e cobertura no contrato analítico; manter classificação desconhecida onde faltar evidência. Comparações temporais devem informar quando a fonte cadastral mudar.
7. Usar armazenamento persistente e retenção dos artefatos. O compose atual usa tmpfs no PostgreSQL, inadequado para preservar o histórico produzido.

Esses pontos são proposta para uma etapa seguinte, não funcionalidades implementadas nesta entrega. Não há estimativa de custo, prazo ou cobertura: isso depende do piloto e do volume real.

## Limites e encerramento

Foram verificados código, layout, documentação e metadados remotos. Não houve ingestão de arquivos históricos, consulta autenticada ao Conecta, contato com fornecedores ou validação de transições de empresas. Ausência de recurso na documentação examinada não demonstra inexistência de histórico nos sistemas internos da Receita.

Critério atendido da PEN-003: disponibilidade investigada, porte por data confrontado com as fontes e recomendação documentada. A implementação histórica não está concluída nem liberada para produção por esta investigação. Não foram executados testes de aplicação, pois a entrega altera somente documentação.

