from concurrent.futures import ThreadPoolExecutor, as_completed
import json, time
from pathlib import Path
import runpy
p=runpy.run_path('scripts/pen003_piloto.py', run_name='piloto')
base=Path('artifacts/pen-003/rfb-cache')
base.mkdir(parents=True, exist_ok=True)
def obter(par):
    mes, arquivo=par
    print('DOWNLOAD '+mes+' '+arquivo['nome'], flush=True)
    for tentativa in range(3):
        try:
            path, sha=p['baixar'](arquivo,base/mes)
            break
        except Exception as error:
            print(json.dumps({'competencia':mes,'arquivo':arquivo['nome'],'tentativa':tentativa+1,'erro':type(error).__name__+': '+str(error)}),flush=True)
            if tentativa == 2:
                raise
            time.sleep(2 ** tentativa)
    print(json.dumps({'competencia':mes,'arquivo':path.name,'bytes':arquivo['bytes'],'sha256':sha}),flush=True)
    return path
if __name__ == '__main__':
    itens=[(mes,a) for mes in ('2025-01','2026-10') for a in p['listar'](mes)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for futuro in as_completed([pool.submit(obter,item) for item in itens]):
            futuro.result()
    print('DOWNLOADS_CONCLUIDOS',flush=True)

