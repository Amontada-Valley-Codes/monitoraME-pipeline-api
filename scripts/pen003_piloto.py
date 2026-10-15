"""PEN-003: piloto isolado de retratos mensais reais da Receita Federal.

Somente biblioteca padrao. Nao modifica o banco nem a API.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

SHARE = 'YggdBLfdninEJX9'
BASE = 'https://arquivos.receitafederal.gov.br/public.php/webdav/'
PORTES = {'00': 'NAO_INFORMADO', '01': 'ME', '03': 'EPP', '05': 'DEMAIS'}
NS = {'d': 'DAV:'}


def agora():
    return datetime.now(timezone.utc).isoformat()


def requisicao(url, method='GET'):
    token = base64.b64encode((SHARE + ':').encode()).decode()
    return urllib.request.Request(url, method=method, headers={
        'Authorization': 'Basic ' + token, 'Depth': '1',
        'User-Agent': 'monitoraME-PEN003-pilot/1.0',
    })


def listar(mes):
    with urllib.request.urlopen(requisicao(BASE + mes + '/', 'PROPFIND'), timeout=60) as r:
        tree = ET.fromstring(r.read())
    arquivos = []
    for response in tree.findall('d:response', NS):
        href = response.findtext('d:href', namespaces=NS) or ''
        nome = urllib.parse.unquote(href.rsplit('/', 1)[-1])
        if not (re.fullmatch(r'Empresas[0-9]+\.zip', nome) or nome == 'Simples.zip'):
            continue
        props = None
        for ps in response.findall('d:propstat', NS):
            if ' 200 ' in (ps.findtext('d:status', namespaces=NS) or ''):
                props = ps.find('d:prop', NS)
                break
        if props is None:
            raise ValueError('Metadados indisponiveis: ' + href)
        arquivos.append({'nome': nome, 'url': BASE + mes + '/' + nome,
                         'bytes': int(props.findtext('d:getcontentlength', namespaces=NS)),
                         'last_modified': props.findtext('d:getlastmodified', namespaces=NS),
                         'etag': props.findtext('d:getetag', namespaces=NS)})
    empresas = [a for a in arquivos if a['nome'].startswith('Empresas')]
    if {a['nome'] for a in empresas} != {f'Empresas{i}.zip' for i in range(10)}:
        raise ValueError('Conjunto de partes EMPRESAS inesperado em ' + mes)
    if not any(a['nome'] == 'Simples.zip' for a in arquivos):
        raise ValueError('Simples.zip ausente em ' + mes)
    return sorted(arquivos, key=lambda a: a['nome'])


def baixar(arquivo, pasta, *, offline=False):
    pasta.mkdir(parents=True, exist_ok=True)
    indice = pasta / (arquivo['nome'] + '.source.json')
    if indice.exists():
        meta = json.loads(indice.read_text(encoding='utf-8'))
        cache = pasta / meta['arquivo_local']
        if all(meta.get(k) == arquivo.get(k) for k in ('url', 'etag', 'bytes', 'last_modified')) and cache.is_file() and cache.stat().st_size == arquivo['bytes']:
            with cache.open('rb') as fonte:
                sha = hashlib.file_digest(fonte, 'sha256').hexdigest()
            if sha == meta['sha256']:
                return cache, sha
    if offline:
        raise ValueError('Artefato local ausente ou divergente; download desabilitado')
    destino = pasta / arquivo['nome']
    parcial = destino.with_suffix('.zip.part')
    # Nao reutiliza arquivos pela mera coincidencia de tamanho.
    digest = hashlib.sha256()
    # Uma transferencia continua evita um novo atraso de origem a cada MiB.
    pedido = requisicao(arquivo['url'])
    if arquivo.get('etag'):
        pedido.add_header('If-Match', arquivo['etag'])
    if parcial.exists():
        preservado = parcial.with_name(agora().replace(':', '-') + '-' + parcial.name)
        parcial.replace(preservado)
    with urllib.request.urlopen(pedido, timeout=120) as response:
        if response.status != 200:
            raise ValueError('Resposta incompleta ao solicitar arquivo integral')
        with parcial.open('wb') as out:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                out.write(chunk)
                out.flush()
    if parcial.stat().st_size != arquivo['bytes']:
        raise ValueError('Tamanho divergente: ' + arquivo['nome'])
    sha = digest.hexdigest()
    # Artefatos anteriores permanecem preservados por hash.
    destino = pasta / (sha + '-' + arquivo['nome'])
    parcial.replace(destino)
    indice.write_text(json.dumps(dict(arquivo, sha256=sha, arquivo_local=destino.name), indent=2), encoding='utf-8')
    return destino, sha


def alvos(arquivo):
    if arquivo:
        texto = Path(arquivo).read_text(encoding='utf-8-sig')
        entradas = [v for v in texto.splitlines() if v.strip()]
    else:
        sql = "SELECT DISTINCT e.cnpj_fornecedor FROM tce_empenhos e JOIN tce_despesa_ingestion_runs r ON r.id=e.run_id WHERE r.status='PUBLICADO' AND e.cnpj_fornecedor IS NOT NULL ORDER BY 1"
        r = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql',
                            '-U', 'monitorame', '-d', 'monitorame', '-At', '-c', sql],
                           capture_output=True, text=True, check=True, timeout=30)
        entradas = r.stdout.splitlines()
    resultado = set()
    for entrada in entradas:
        cnpj = re.sub(r'[./\-\s]', '', entrada).upper()
        # Preserva letras na raiz para inscricoes alfanumericas.
        if not re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}', cnpj):
            raise ValueError('Inscricao com formato invalido na selecao real')
        resultado.add(cnpj)
    return sorted(resultado)


def processar(path, tipo, raizes):
    registros, linhas, duplicadas = {}, 0, 0
    with zipfile.ZipFile(path) as z:
        membros = [m for m in z.infolist() if not m.is_dir()]
        if not membros:
            raise ValueError('ZIP sem arquivos: ' + str(path))
        for membro in membros:
            # Leitura integral ate EOF verifica CRC; nao extrai caminhos do ZIP.
            with z.open(membro) as raw:
                for row in csv.reader(io.TextIOWrapper(raw, encoding='iso-8859-1', newline=''), delimiter=';'):
                    linhas += 1
                    esperado = 7
                    if len(row) != esperado or not re.fullmatch(r'[A-Z0-9]{8}', row[0]):
                        raise ValueError(f'Layout invalido: {path.name}, linha {linhas}')
                    if row[0] not in raizes:
                        continue
                    if row[0] in registros:
                        duplicadas += 1
                        raise ValueError('Raiz duplicada: ' + row[0])
                    if tipo == 'EMPRESAS':
                        codigo = row[5].strip()
                        if codigo not in PORTES:
                            raise ValueError('Codigo de porte nao documentado: ' + repr(codigo))
                        registros[row[0]] = {'porte_codigo': codigo, 'porte': PORTES[codigo]}
                    else:
                        if row[1] not in {'S', 'N', ''} or row[4] not in {'S', 'N', ''}:
                            raise ValueError('Indicador Simples/MEI invalido')
                        for data in (row[2], row[3], row[5], row[6]):
                            if data and data != '00000000':
                                datetime.strptime(data, '%Y%m%d')
                        # Conserva os campos tributarios separados do porte cadastral.
                        registros[row[0]] = {'campos_originais': row[1:], 'opcao_mei': row[4],
                                            'data_opcao_mei': row[5], 'data_exclusao_mei': row[6]}
    return registros, linhas, duplicadas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--months', nargs=2, required=True)
    parser.add_argument('--cnpjs-file', help='Um CNPJ real por linha; padrao: fornecedores de lotes publicados no Compose')
    parser.add_argument('--output', default='artifacts/pen-003')
    parser.add_argument('--offline', action='store_true', help='Valida a versao preservada no cache, sem consultar a origem')
    parser.add_argument('--cache-dir', help='Diretorio persistente de ZIPs oficiais, com revalidacao de metadados e SHA-256')
    parser.add_argument('--download', action='store_true', help='Baixa e valida integralmente os arquivos; sem a flag apenas faz preflight')
    args = parser.parse_args()
    if args.offline and not args.cache_dir:
        parser.error('--offline exige --cache-dir')
    meses = sorted(set(args.months))
    if len(meses) != 2 or any(not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', m) for m in meses):
        parser.error('Informe duas competencias distintas no formato YYYY-MM')
    pasta = Path(args.output) / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    pasta.mkdir(parents=True, exist_ok=False)
    report = {'iniciado_em': agora(), 'competencias': meses, 'semantica': 'PORTE_OBSERVADO_NA_COMPETENCIA',
              'referencia_extracao_comprovada': False, 'origem_alvos': args.cnpjs_file or 'LOTES_PUBLICADOS_LOCAIS',
              'artefatos': [], 'cobertura': {}, 'status': 'INICIADO'}
    try:
        cnpjs = alvos(args.cnpjs_file)
        raizes = {c[:8] for c in cnpjs}
        report.update(cnpjs_selecionados=len(cnpjs), raizes_selecionadas=len(raizes))
        if not raizes:
            report['status'] = 'SEM_FORNECEDORES_REAIS'
            return 2
        if args.offline:
            manifestos = {}
            esperados = {f'Empresas{i}.zip' for i in range(10)} | {'Simples.zip'}
            for mes in meses:
                metas = [json.loads(p.read_text(encoding='utf-8')) for p in (Path(args.cache_dir)/mes).glob('*.zip.source.json')]
                if {m['nome'] for m in metas} != esperados or len(metas) != len(esperados):
                    raise ValueError('Manifesto local incompleto: ' + mes)
                for m in metas:
                    if m['url'] != BASE + mes + '/' + m['nome']:
                        raise ValueError('Origem do manifesto local divergente')
                manifestos[mes] = sorted(metas, key=lambda m:m['nome'])
            report['modo_fonte'] = 'VERSAO_PRESERVADA_SEM_REVALIDACAO_REMOTA'
        else:
            manifestos = {mes: listar(mes) for mes in meses}
            report['modo_fonte'] = 'METADADOS_REVALIDADOS_NA_ORIGEM'
        report['manifestos'] = manifestos
        total = sum(a['bytes'] for arquivos in manifestos.values() for a in arquivos)
        report['download_bytes_previstos'] = total
        report['espaco_livre_bytes'] = shutil.disk_usage(pasta).free
        if not args.download:
            report['status'] = 'PREFLIGHT_CONCLUIDO_SEM_DOWNLOAD'
            return 0
        if report['espaco_livre_bytes'] < total + 512 * 1024 * 1024:
            raise RuntimeError('Espaco insuficiente para conservar os ZIPs')
        banco = sqlite3.connect(pasta / 'observacoes.sqlite3')
        try:
            banco.execute('CREATE TABLE observacoes (raiz TEXT, competencia TEXT, porte_codigo TEXT, porte TEXT, simples_json TEXT, PRIMARY KEY(raiz, competencia))')
            banco.execute('CREATE TABLE inscricoes (cnpj TEXT PRIMARY KEY, raiz TEXT)')
            banco.executemany('INSERT INTO inscricoes VALUES (?,?)', [(c, c[:8]) for c in cnpjs])
            por_mes = {}
            for mes in meses:
                print('Processando competencia ' + mes, flush=True)
                mes_dir = (Path(args.cache_dir) if args.cache_dir else pasta) / mes
                mes_dir.mkdir(parents=True, exist_ok=True)
                empresas, simples = {}, {}
                for arquivo in manifestos[mes]:
                    print('Download e validacao: ' + arquivo['nome'], flush=True)
                    path, sha = baixar(arquivo, mes_dir, offline=args.offline)
                    tipo = 'EMPRESAS' if arquivo['nome'].startswith('Empresas') else 'SIMPLES'
                    dados, linhas, duplicadas = processar(path, tipo, raizes)
                    alvo = empresas if tipo == 'EMPRESAS' else simples
                    if alvo.keys() & dados.keys():
                        raise ValueError('Raizes duplicadas entre partes da competencia')
                    alvo.update(dados)
                    with zipfile.ZipFile(path) as validado:
                        membros = [{'nome': m.filename, 'data_zip': list(m.date_time),
                                    'bytes_descompactados': m.file_size, 'crc32': format(m.CRC, '08x')}
                                   for m in validado.infolist() if not m.is_dir()]
                    report['artefatos'].append(dict(arquivo, competencia=mes, sha256=sha, tipo=tipo, membros_zip=membros,
                        coletado_em=agora(), caminho=str(path), linhas_lidas=linhas, duplicadas=duplicadas))
                for raiz in sorted(raizes):
                    item = empresas.get(raiz, {})
                    banco.execute('INSERT INTO observacoes VALUES (?,?,?,?,?)',
                        (raiz, mes, item.get('porte_codigo'), item.get('porte'),
                         json.dumps(simples[raiz]) if raiz in simples else None))
                report['cobertura'][mes] = {'raizes_encontradas': len(empresas),
                    'raizes_ausentes': len(raizes - empresas.keys()),
                    'porte_nao_informado': sum(v['porte_codigo'] == '00' for v in empresas.values()),
                    'simples_encontradas': len(simples),
                    'simples_ausentes': len(raizes - simples.keys()),
                    'mei_sim': sum(v['opcao_mei'] == 'S' for v in simples.values()),
                    'mei_nao': sum(v['opcao_mei'] == 'N' for v in simples.values()),
                    'mei_nao_informado': sum(v['opcao_mei'] == '' for v in simples.values()),
                    'cobertura_cadastral_percentual': round(100 * len(empresas) / len(raizes), 2)}
                por_mes[mes] = empresas
            a, b = (por_mes[m] for m in meses)
            comuns = a.keys() & b.keys()
            comparaveis = [r for r in comuns if a[r]['porte_codigo'] != '00' and b[r]['porte_codigo'] != '00']
            report['comparacao'] = {'raizes_com_porte_identificado_nos_dois_meses': len(comparaveis),
                'mudancas_observadas': sum(a[r]['porte_codigo'] != b[r]['porte_codigo'] for r in comparaveis),
                'vigencia_exata_demonstrada': False}
            banco.commit()
            report['status'] = 'VALIDACAO_MENSAL_CONCLUIDA'
        finally:
            banco.close()
        return 0
    except Exception as error:
        report['status'] = 'FALHA_NA_VALIDACAO'
        report['erro'] = type(error).__name__ + ': ' + str(error)
        return 1
    finally:
        report['finalizado_em'] = agora()
        (pasta / 'resultado.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'relatorio': str(pasta / 'resultado.json')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())







