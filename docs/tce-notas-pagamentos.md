# Ingestão incremental de notas de pagamento do TCE-CE

O comando `scripts/ingestir_tce_pagamentos.py` importa uma competência documental do endpoint `notas_pagamentos`, para um exercício orçamentário e município. O mês documental e o ano do orçamento são dimensões independentes: por exemplo, uma nota publicada em `2026-02` pode pertencer ao orçamento de `2025`. O coletor respeita o limite de mil registros por chamada. Quando a consulta mensal atinge esse teto, repete a coleta por órgão e, se necessário, por unidade orçamentária; `$start_index` não é usado como prova de completude documental.

```powershell
.venv\Scripts\python.exe scripts\ingestir_tce_pagamentos.py --codigo-municipio 004 --exercicio-orcamento 2025 --competencia 2026-02
```

Para fechar um intervalo mensal, por exemplo a conferência de Acaraú (`004`) para o orçamento `202500`, de `202501` a `202609`:

```powershell
.venv\Scripts\python.exe scripts\ingestir_tce_pagamentos.py --codigo-municipio 004 --exercicio-orcamento 2025 --competencia-inicial 2025-01 --competencia-final 2026-09
```

O mesmo comando pode ser executado novamente para atualizar a competência. A coleta subdivide o mês quando necessário e persiste um snapshot versionado em `tce_pagamentos`, identificado por município, exercício orçamentário e competência. Uma resposta vazia válida publica um snapshot vazio. Partição de unidade que ainda atinja mil registros, falha de fonte, dado malformado ou duplicidade conflitante grava um run rejeitado e preserva o snapshot publicado anterior.

Valores são convertidos para centavos inteiros. A chave da nota combina a chave do empenho com número do subempenho e número da nota; constraints no banco impedem repetição dentro do lote. O payload de origem é mantido para auditoria. Snapshots substituídos e runs rejeitados são retidos por sete dias.

Antes de executar, aplique a migration do banco principal:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

O comando é uma unidade operacional mensal e pode ser agendado externamente por município, orçamento e competência. Ele consulta órgãos e unidades para excluir a Câmara e comprovar o particionamento, mas não coleta portes cadastrais. O comando de auditoria de pagamentos consulta a fonte ao vivo e não consome esses snapshots.
