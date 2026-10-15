# Porte observado por competência mensal

O pipeline publica retratos cadastrais versionados por raiz de CNPJ e competência. A unidade temporal é a pasta mensal de publicação da Receita, sem comprovação de vigência diária. A classificação cadastral ME/EPP/demais e a opção pelo MEI são campos separados.

## Publicações carregadas

Foram importadas as 16732 observações reais do piloto: 8366 raízes em cada uma das competências 2025-01 e 2026-10, incluindo ausências explícitas. A origem da amostra são os 8744 CNPJs encontrados em empenhos publicados dos 184 municípios compradores do Ceará em janeiro de 2025. Não se trata de um cadastro de todas as empresas brasileiras nem apenas das empresas sediadas no Ceará.

- 2025-01: 8348 raízes encontradas, 18 ausentes; cobertura 99,78%.
- 2026-10: 8363 raízes encontradas, 3 ausentes; cobertura 99,96%.

As demais competências não foram importadas. Não há interpolação, reaproveitamento da fotografia de outubro para janeiro, nem preenchimento pelo cadastro atual. As rotas existentes de indicadores mantêm sua semântica cadastral; as novas rotas expõem explicitamente as observações mensais.

## Contrato HTTP

- `GET /pipeline/tce/portes/competencias`: publicações ativas, fonte, competência, versão, datas de observação/importação, manifestos e cobertura. Aceita `competencia_final` e `incluir_versoes_anteriores=true`.
- `GET /pipeline/tce/portes/historico/{cnpj}`: retratos da raiz consultada. Aceita `competencia_inicial` e `competencia_final`, em `YYYY-MM`, com limite de 120 meses. O limite final padrão é o mês atual em America/Fortaleza. Somente publicações da competência exata entram em cada posição do histórico.
- `GET /pipeline/tce/portes/versoes/{versao}/{cnpj}`: reproduz uma observação na versão indicada, inclusive uma publicação substituída.

Os retornos identificam `LOCAL_DATABASE`, `external_requests=false`, política `COMPETENCIA_EXATA_SEM_PREENCHIMENTO`, fonte, versão e `vigencia_diaria_comprovada=false`. `observado_em` indica processamento/observação local; não é início de vigência do porte. A referência exata da extração permanece não comprovada. A cobertura informa seu denominador de raízes selecionadas.

| Estado | Significado |
| --- | --- |
| SEM_PUBLICACAO | Não há publicação para o mês consultado. Porte e MEI permanecem nulos. |
| FORA_DA_AMOSTRA | A raiz não integra o universo selecionado da publicação. |
| AUSENTE_NA_FOTOGRAFIA | Raiz selecionada, ausente do arquivo EMPRESAS daquela competência. |
| PORTE_NAO_INFORMADO | A Receita informou código 00. |
| IDENTIFICADO | A Receita informou ME, EPP ou demais. |

`optante_mei=null` não significa não optante; `simples_disponivel` distingue ausência do registro tributário. Um CNPJ completo é aceito sem máscara, mas a resposta representa a raiz empresarial. Não comprova existência ou histórico de cada estabelecimento. Erros retornam 422, 404 ou 503 com `X-Request-ID` e envelope padronizado.

Exemplo com uma inscrição real já coletada:

```powershell
$cnpj = (Get-Content artifacts/pen-003/coleta-ce-2025-01/cnpjs-reais.txt)[0]
Invoke-RestMethod "http://localhost:8000/pipeline/tce/portes/historico/${cnpj}?competencia_inicial=2025-01&competencia_final=2026-10"
```

## Importação e versionamento

A migration `0007_portes_mensais` cria `porte_publicacoes` e `porte_observacoes`. O importador aceita somente resultados concluídos do piloto, verifica integridade SQLite, cobertura, seleção de raízes e SHA-256/tamanho dos ZIPs oficiais preservados. Não consulta a Receita durante a importação.

```powershell
docker compose run --rm --no-deps -v "$($PWD.Path):/app:ro" api python -B scripts/importar_portes_mensais.py --resultado artifacts/pen-003/20261014T191044138270Z/resultado.json
```

A versão identifica conteúdo cadastral, seleção e artefatos da competência. Reimportar o mesmo conteúdo reutiliza a publicação e não duplica observações. Uma publicação nova substitui apenas a referência ativa do mês, conservando o conteúdo das versões anteriores. Reimportar uma versão antiga não a reativa. Todas as competências do arquivo são publicadas em uma transação; um bloqueio por competência serializa importações concorrentes.

Cada manifesto conserva hashes dos artefatos e do SQLite/relatório de origem. Os ZIPs e os resultados ficam preservados em `artifacts/pen-003/`, fora do Git. Não altere esses arquivos após uma publicação; conserve-os com backups e retenção adequados. O importador é um comando administrativo local, não uma rota HTTP de escrita.

Para adicionar competências, execute o coletor/piloto com arquivos reais da competência desejada e importe seu resultado concluído. Duas fotografias não equivalem à validação dos meses intermediários. A política de associação de porte a despesas por data exata depende de evidência adicional de vigência e não é implementada por estas rotas.

## Persistência e verificação

O PostgreSQL agora utiliza o volume nomeado `monitorame-pipeline_postgres_data`. Antes da conversão foi salvo e verificado o backup em `artifacts/pen-003/persistencia-20261014T194206/antes-da-conversao.dump`; foram restaurados e conferidos 100750 empenhos, 535 anulações e 184 lotes. Um volume persistente não substitui backup.

Os testes de integração usam apenas a coorte real já importada, sem criar fornecedores ou despesas fictícios:

```powershell
docker compose run --rm --no-deps -e RUN_PORTE_INTEGRATION=1 -v "$($PWD.Path):/app:ro" api python -B -m unittest discover -s tests -p test_portes_historicos.py
```

Verificam lacunas sem preenchimento futuro, limite temporal e reprodução por versão. A importação foi repetida com os mesmos artefatos para verificar idempotência. A validação do piloto e suas limitações estão em [resultado PEN-003](PEN-003-resultado-piloto.md).

Verificação final: após reiniciar o PostgreSQL, permaneceram 100750 empenhos, 535 anulações, 2 publicações e 16732 observações. A suíte executou 221 testes (2 integrações ignoradas nessa execução); os 4 testes do módulo mensal passaram separadamente com integração real habilitada. O backup após a importação e o registro de implantação estão em artifacts/pen-003/persistencia-20261014T194206/.

