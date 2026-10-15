# PEN-003 — Coleta real de todos os municípios do Ceará

Execução de 14/10/2026. Escopo autorizado: todos os municípios do Ceará. Período adotado para o piloto: janeiro de 2025, mês fechado que coincide com a primeira fotografia cadastral proposta. Isso não representa coleta de todos os anos.

## Resultado da coleta TCE-CE

| Medida verificada no banco | Resultado |
| --- | ---: |
| Municípios validados contra o catálogo do IBGE | 184 |
| Lotes publicados de 2025-01 | 184 |
| Lotes com falha ou rejeição nesta execução | 0 |
| Notas de empenho armazenadas | 100750 |
| Anulações armazenadas | 535 |
| CNPJs distintos dos fornecedores selecionados | 8744 |

A lista do TCE continha 185 entradas; T.C.M., sem correspondência municipal IBGE, foi excluído. Os 184 municípios foram identificados por correspondência com o catálogo oficial do Ceará, não por uma sequência presumida de códigos. Foram usados os clientes, normalizadores, validações e publicação atômica do próprio pipeline. Duas coletas municipais simultâneas limitaram a carga sobre a origem.

A seleção utiliza somente empenhos dos lotes PUBLICADO desta execução. Não houve geração de empresas, notas ou valores fictícios. CPF não foi convertido em CNPJ. A classificação cadastral histórica não foi preenchida a partir do porte atual.

## Evidências locais preservadas

Na pasta `artifacts/pen-003/coleta-ce-2025-01/`:

- `coleta.json`: resultado individual de cada município, identificadores dos lotes e contagens.
- `cnpjs-reais.txt`: 8744 CNPJs reais, um por linha, selecionados dos lotes publicados.
- `monitorame.dump`: backup PostgreSQL em formato custom, fora do tmpfs.
- `backup.json`: tamanho e SHA-256 do backup.

O backup tem 15159099 bytes e SHA-256 `21e03b0fafddcc6362f95f4bf611fef535a6279e594457d0bef69a786794037f`. Sua listagem foi lida com `pg_restore --list`, com sucesso. Não foi executada restauração em outro banco; legibilidade não substitui um ensaio de restauração.

Os artefatos locais estão excluídos do versionamento por `.gitignore`. O backup é preservação desta execução, não altera a configuração tmpfs do Compose.

## Validação operacional

A consulta HTTP de `/pipeline/tce/empenhos` respondeu com dados reais de janeiro de 2025, `data_source=LOCAL_DATABASE` e `external_requests=false`. A ausência de porte cadastral no cache não foi convertida em classificação presumida. A API de saúde permaneceu operacional.

O leitor do piloto foi executado integralmente sobre uma parte real da Receita (`Empresas2.zip`, competência 2025-01), com 4494860 linhas, CRC válido e sem raízes selecionadas duplicadas nessa parte. Esse teste verifica o leitor; não comprova cobertura nas duas competências nem vigência histórica.

## Execução reproduzível

O comando abaixo roda no container com as dependências do projeto e monta a pasta atual para preservar as evidências:

```powershell
docker compose run --rm --no-deps -v "$($PWD.Path):/app" api python -B scripts/pen003_coletar_ce.py --month 2025-01
```

Uma nova execução pode publicar novos lotes e substituir os anteriores conforme as regras do pipeline; preserve o backup e o relatório quando precisar manter a evidência original.

Para comparar as duas fotografias com a seleção real já preservada:

```powershell
docker compose run --rm --no-deps -v "$($PWD.Path):/app" api python -B scripts/pen003_piloto.py --months 2025-01 2026-10 --cnpjs-file artifacts/pen-003/coleta-ce-2025-01/cnpjs-reais.txt --cache-dir artifacts/pen-003/rfb-cache --download
```

O cache conserva ZIPs oficiais, metadados e SHA-256; sua reutilização exige metadados compatíveis e recálculo do hash local. Datas de publicação/observação permanecem distintas da vigência do porte. O estado da comparação cadastral deve ser consultado no relatório próprio do piloto, não inferido da conclusão da coleta municipal.

## Piloto cadastral concluído

A comparação foi concluída e está documentada em [resultado do piloto](PEN-003-resultado-piloto.md). Foram comparadas 8348 raízes com porte identificado nas duas fotografias e observadas 274 mudanças. Para reproduzir a análise dos artefatos preservados, acrescente --offline ao comando de comparação. Esse modo verifica hashes e integridade local sem afirmar revalidação remota dos metadados.

