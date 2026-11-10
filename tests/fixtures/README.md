# Fixtures de pagamentos TCE

`tce_pagamentos_acarau_2025_coleta_real.json.gz` contém uma projeção da coleta
real do TCE-CE para Acaraú (`004`), exercício orçamentário de 2025 e documentos
até a referência `2026-09`. A projeção mantém os campos usados pela conciliação
e os pagamentos/empenhos do recorte; omite campos alheios ao cálculo e os
payloads cadastrais integrais. O cache mantém somente status, porte, indicador
MEI e data de observação necessários para reproduzir a classificação.

A fixture foi capturada em 2026-11-10. Os agregados reproduzíveis são:

- financeiro elegível: R$ 136.213.306,64;
- ME reconhecida no snapshot: R$ 23.857.159,02;
- classificação cadastral incompleta: 131 pendências.

Como a classificação está incompleta, o subtotal de ME é parcial, não deve ser
tratado como total definitivo, e o total MPE permanece `null`. A fixture
reproduz o estado real observado; ela não confirma os valores de ME apresentados
em outras referências.

Execute a regressão offline com:

```bash
python -m unittest discover -s tests -p 'test_pagamentos_acarau_regressao.py' -v
```
