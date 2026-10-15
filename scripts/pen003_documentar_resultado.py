"""Documenta somente resultados reais de uma execucao mensal concluida."""
import argparse
import json
from pathlib import Path
import sqlite3

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--resultado', required=True)
p.add_argument('--output', default='docs/PEN-003-resultado-piloto.md')
a = p.parse_args()
path = Path(a.resultado)
r = json.loads(path.read_text(encoding='utf-8'))
if r['status'] != 'VALIDACAO_MENSAL_CONCLUIDA':
    raise SystemExit('Execucao incompleta: nao e permitido documentar sucesso')
conn = sqlite3.connect(path.with_name('observacoes.sqlite3'))
meses = r['competencias']
transicoes = conn.execute('''SELECT a.porte, b.porte, count(*) FROM observacoes a
    JOIN observacoes b ON a.raiz=b.raiz
    WHERE a.competencia=? AND b.competencia=?
    AND a.porte_codigo IN ('01','03','05') AND b.porte_codigo IN ('01','03','05')
    AND a.porte_codigo<>b.porte_codigo GROUP BY a.porte,b.porte ORDER BY 1,2''', meses).fetchall()
quantidade = conn.execute('SELECT count(*) FROM observacoes').fetchone()[0]
cnpjs = conn.execute('SELECT count(*) FROM inscricoes').fetchone()[0]
if cnpjs != r['cnpjs_selecionados'] or quantidade != 2*r['raizes_selecionadas']:
    raise SystemExit('Contagens SQLite e relatorio divergentes')
if sum(n for _,_,n in transicoes) != r['comparacao']['mudancas_observadas']:
    raise SystemExit('Contagem de mudancas divergente')
rows = ['# PEN-003 — Resultado do piloto com dados reais', '',
    'Execução concluída em ' + r['finalizado_em'] + '.', '',
    '**Recomendação: seguir com a implementação de observações mensais de porte, com cobertura e procedência explícitas. Não implementar porte vigente na data exata do empenho a partir dessas fontes.**', '',
    '## Recorte efetivamente validado', '',
    'A coleta TCE-CE abrangeu os 184 municípios compradores do Ceará em janeiro de 2025: 100750 empenhos, 535 anulações e 184 lotes publicados. Os fornecedores podem ter sede em qualquer UF; o recorte é do município comprador.', '',
    f"Foram selecionados {r['cnpjs_selecionados']} CNPJs reais, correspondentes a {r['raizes_selecionadas']} raízes. As inscrições selecionadas passaram pela validação de dígitos verificadores do pipeline. A análise cadastral foi feita por raiz, conforme o campo de porte da Receita.", '',
    f"Foram baixados e lidos integralmente {len(r['artefatos'])} ZIPs oficiais das competências {meses[0]} e {meses[1]}, totalizando {sum(x['bytes'] for x in r['artefatos'])} bytes compactados. Foram verificados tamanho, SHA-256, CRC, estrutura dos registros e duplicidades de raízes selecionadas. Nenhum dado fictício foi utilizado.", '',
    '## Cobertura por fotografia', '',
    '| Competência | Raízes encontradas | Porte identificado | Porte não informado | Raízes ausentes | Cobertura cadastral |',
    '| --- | ---: | ---: | ---: | ---: | ---: |']
for mes in meses:
    c = r['cobertura'][mes]
    rows.append(f"| {mes} | {c['raizes_encontradas']} | {c['raizes_encontradas']-c['porte_nao_informado']} | {c['porte_nao_informado']} | {c['raizes_ausentes']} | {c['cobertura_cadastral_percentual']}% |")
rows += ['', 'Cobertura cadastral mede presença da raiz em EMPRESAS, incluindo porte não informado; não mede existência de cada estabelecimento nem cobertura nacional. Ausência e porte 00 permanecem distintos. Não houve preenchimento retroativo de lacunas.', '',
    '## Mudanças observadas', '',
    f"Foram comparáveis {r['comparacao']['raizes_com_porte_identificado_nos_dois_meses']} raízes com porte identificado em ambas as fotografias; {r['comparacao']['mudancas_observadas']} apresentaram códigos diferentes.", '',
    '| Porte na primeira fotografia | Porte na segunda fotografia | Raízes |',
    '| --- | --- | ---: |']
for origem, destino, n in transicoes:
    rows.append(f'| {origem} | {destino} | {n} |')
if not transicoes:
    rows.append('| Nenhuma mudança de porte identificado observada | — | 0 |')
rows += ['', 'A diferença mostra mudança entre dois retratos cadastrais. Não demonstra o dia da mudança, sua vigência legal, sua causa ou o porte de todos os meses intermediários. Mudanças de/para porte desconhecido não foram contadas como transições de porte identificado.', '',
    '## Simples e MEI, separados do porte', '',
    '| Competência | Raízes no arquivo Simples | Ausentes no Simples | Opção MEI S | Opção MEI N | Indicador vazio |',
    '| --- | ---: | ---: | ---: | ---: | ---: |']
for mes in meses:
    c = r['cobertura'][mes]
    rows.append(f"| {mes} | {c['simples_encontradas']} | {c['simples_ausentes']} | {c['mei_sim']} | {c['mei_nao']} | {c['mei_nao_informado']} |")
rows += ['', 'Indicadores e datas tributárias reais foram preservados no SQLite, sem converter ausência em não optante. O indicador MEI não substituiu o código cadastral ME/EPP/demais neste piloto. A implementação futura deve definir explicitamente a composição das categorias e a referência mensal, sem usar a condição atual para reclassificar o passado.', '',
    '## Evidência e limites temporais', '',
    'As competências são as pastas oficiais de publicação. O relatório JSON registra URLs, ETags, Last-Modified, hashes e nomes/datas internas dos membros dos ZIPs. Esses metadados não comprovam o início ou fim da vigência do porte; a referência exata da extração continua marcada como não comprovada. Não foram utilizados snapshots posteriores como evidência de porte na data de um empenho.', '',
    'O piloto valida duas fotografias, não uma série mensal contínua entre elas. Tampouco valida todos os estabelecimentos em ESTABELECIMENTOS, faturamento, enquadramento jurídico ou todos os ciclos de opção/exclusão tributária.', '',
    '## Encaminhamento técnico', '',
    'GO para uma tabela de observações separada do cache atual, por raiz/competência/versão do artefato, com conservação dos arquivos e fontes. A API deve informar competência, procedência, cobertura e desconhecidos, e adotar uma regra temporal documentada que impeça usar observações futuras. O armazenamento precisa ser persistente.', '',
    'NO-GO para vigência diária do porte ou reconstrução de eventos de reenquadramento. Isso depende de uma fonte adicional com eventos e datas de vigência verificáveis. A implementação produtiva também precisa validar as competências adicionais exigidas pelo produto; a disponibilidade de duas fotografias não garante retenção permanente nem SLA de download.', '',
    'As observações deste piloto estão no SQLite separado do cache cadastral da aplicação. Este resultado não declara integração histórica em produção.', '',
    '## Artefatos e reprodução', '',
    f"- Resultado detalhado: `{path.as_posix()}`.",
    f"- Observações reais e inscrições: `{path.with_name('observacoes.sqlite3').as_posix()}` ({quantidade} observações, incluindo ausências explícitas).",
    '- Coleta municipal e backup: `artifacts/pen-003/coleta-ce-2025-01/`.',
    '- ZIPs preservados: `artifacts/pen-003/rfb-cache/`.',
    '- Comandos de reprodução: [coleta CE](PEN-003-coleta-ce.md).', '',
    'Fontes: [layout oficial](https://www.gov.br/receitafederal/dados/cnpj-metadados.pdf), [compartilhamento oficial CNPJ](https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9) e [investigação inicial no código](PEN-003-historico-porte-cnpj.md).', '']
conn.close()
Path(a.output).write_text('\n'.join(rows), encoding='utf-8')
print(json.dumps({'documento': a.output, 'observacoes': quantidade, 'mudancas': r['comparacao']['mudancas_observadas']}))
