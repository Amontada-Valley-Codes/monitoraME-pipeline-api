Atualização: a coleta dos 184 municípios e a validação dos arquivos reais foram concluídas. Consulte o [resultado final do piloto](PEN-003-resultado-piloto.md). As limitações de vigência exata permanecem válidas; os estados anteriores descritos neste documento registram etapas da investigação.

# PEN-003 — Execução do piloto mensal

O comando `scripts/pen003_piloto.py` compara duas competências reais da Receita sem alterar o banco da API. Usa somente a biblioteca padrão do Python e o compartilhamento público oficial identificado no relatório da investigação.

Seleção padrão: CNPJs dos empenhos de lotes PUBLICADO no PostgreSQL do Compose local, usuário/banco monitorame. Alternativamente, `--cnpjs-file` recebe arquivo UTF-8 com uma inscrição real por linha. Não gera nem escolhe empresas fictícias. A validação da entrada é de formato, não de existência cadastral ou dígito verificador.

Preflight (metadados e volume, sem baixar ZIPs):

```powershell
.\.venv\Scripts\python.exe -B scripts/pen003_piloto.py --months 2025-01 2026-10
```

Validação completa, após existir seleção real:

```powershell
.\.venv\Scripts\python.exe -B scripts/pen003_piloto.py --months 2025-01 2026-10 --download
```

Com seleção externa real, acrescente `--cnpjs-file caminho-do-arquivo.txt`. As competências do comando são recortes propostos para o piloto e podem ser alteradas; não representam vigência comprovada do porte.

O processamento baixa todas as dez partes EMPRESAS e o Simples de cada mês, pois não presume partição fixa por CNPJ. Pode consumir vários GB e levar tempo. Valida tamanho, estrutura CSV, códigos de porte, indicadores/datas tributários e CRC na leitura integral dos ZIPs. Detecta duplicidade de raízes selecionadas dentro e entre partes. A verificação de duplicidades não abrange empresas fora da seleção.

Saídas em `artifacts/pen-003/<execucao>/`:

- `resultado.json`: estado, fontes, metadados, SHA-256, datas de coleta, cobertura das raízes e comparação agregada. Falhas não são reportadas como ausência cadastral.
- `observacoes.sqlite3`: observações reais por raiz/competência e relação entre inscrição completa e raiz. Só há commit depois da validação de ambas as competências.
- ZIPs preservados em subpastas por competência, com hash no nome. Não extrai arquivos para o sistema de arquivos.

A cobertura é por raiz selecionada, não pela população de empresas do Brasil. Porte 00 é separado de raiz ausente; datas e condição de MEI são conservadas separadamente e não alteram o porte cadastral. A comparação conta somente raízes com porte identificado em ambos os meses. Uma diferença demonstra mudança entre retratos, sem estabelecer a data exata de vigência. A referência da extração fica explicitamente não comprovada.

Não versionar ZIPs, SQLite ou inscrições individuais no Git. Os artefatos são locais, preservados no disco do projeto e independentes do tmpfs do PostgreSQL. A política de retenção, backup e espaço deve ser definida antes de uso recorrente.

## Estado real em 14/10/2026

Consulta ao PostgreSQL local: zero empenhos, zero fornecedores em cache e zero lotes publicados. Não há amostra real no banco para executar uma comparação. Nesse estado, o comando encerra com `SEM_FORNECEDORES_REAIS` (código 2), grava a evidência e não acessa arquivos da Receita nem calcula percentuais.

A execução completa depende do recorte de município/período a ingerir ou de uma lista real de CNPJs. O piloto preparado não constitui implementação histórica em produção. A recomendação da investigação permanece: não afirmar porte vigente na data do empenho; validar a abordagem mensal com os arquivos reais antes de integrar à API.

