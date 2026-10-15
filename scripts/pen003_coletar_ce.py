"""Coleta real dos municipios CE para a selecao do piloto PEN-003."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.core import database, orm
from app.core.models import TceEmpenho, TceDespesaIngestionRun
from app.pipeline.ingestion import tce, ibge
from app.pipeline.tce_despesas import executar_ingestao_competencia_tce


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--month', default='2025-01')
    parser.add_argument('--output', default='artifacts/pen-003/coleta-ce-2025-01')
    args = parser.parse_args()
    pasta = Path(args.output)
    pasta.mkdir(parents=True, exist_ok=True)
    report = {'competencia': args.month, 'uf': 'CE', 'iniciado_em': datetime.now(timezone.utc).isoformat(),
              'criterio': 'Todos os municipios CE validados no IBGE; TCM excluido', 'municipios': [], 'status':'EM_EXECUCAO'}
    def gravar():
        tmp = pasta / 'coleta.json.tmp'
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        tmp.replace(pasta / 'coleta.json')
    try:
        oficiais = ibge.listar_municipios('CE')
        ids = {str(m['id']) for m in oficiais}
        municipios_tce = tce.buscar_municipios()
        municipios = [m for m in municipios_tce if str(m.get('codigo_municipio_ibge')) in ids]
        codigos = [str(m['codigo_municipio']) for m in municipios]
        if len(ids) != 184 or len(municipios) != 184 or len(set(codigos)) != 184:
            raise RuntimeError('Catalogo nao corresponde aos 184 municipios CE; coleta interrompida')
        database.salvar_municipios_tce(municipios, 'CE')
        report['municipios_esperados'] = len(municipios)
        gravar()
        def coletar(m):
            base = {'codigo_tce': str(m['codigo_municipio']), 'codigo_ibge': str(m['codigo_municipio_ibge']),
                    'nome': m['nome_municipio']}
            try:
                resultado = executar_ingestao_competencia_tce(args.month, codigo_municipio_tce=base['codigo_tce'])
                base.update(asdict(resultado))
            except Exception as error:
                base.update(status='FALHA', erro=type(error).__name__ + ': ' + str(error))
            return base
        # Duas coletas em paralelo limitam a carga nas APIs de origem.
        with ThreadPoolExecutor(max_workers=2) as pool:
            futuros = [pool.submit(coletar, m) for m in municipios]
            for futuro in as_completed(futuros):
                item = futuro.result()
                report['municipios'].append(item)
                gravar()
                print(json.dumps({'concluidos': len(report['municipios']), 'total':184,
                      'municipio':item['nome'], 'status':item['status']}, ensure_ascii=False), flush=True)
        # Usa apenas lotes aprovados nesta execucao e o recorte autorizado.
        runs = [m['run_id'] for m in report['municipios'] if m['status'] == 'PUBLICADO']
        with orm.main_session() as session:
            cnpjs = session.scalars(select(TceEmpenho.cnpj_fornecedor).join(
                TceDespesaIngestionRun, TceEmpenho.run_id == TceDespesaIngestionRun.id).where(
                TceEmpenho.run_id.in_(runs), TceDespesaIngestionRun.status == 'PUBLICADO',
                TceEmpenho.cnpj_fornecedor.is_not(None)).distinct().order_by(TceEmpenho.cnpj_fornecedor)).all()
        (pasta / 'cnpjs-reais.txt').write_text('\n'.join(cnpjs) + ('\n' if cnpjs else ''), encoding='utf-8')
        report['cnpjs_selecionados'] = len(cnpjs)
        report['publicados'] = len(runs)
        report['status'] = 'CONCLUIDO' if len(runs) == 184 else 'CONCLUIDO_COM_PENDENCIAS'
        return 0 if len(runs) == 184 else 2
    except Exception as error:
        report.update(status='FALHA', erro=type(error).__name__ + ': ' + str(error))
        return 1
    finally:
        report['finalizado_em'] = datetime.now(timezone.utc).isoformat()
        gravar()
        print(json.dumps({'status': report['status'], 'relatorio': str(pasta / 'coleta.json')}), flush=True)

if __name__ == '__main__':
    raise SystemExit(main())
