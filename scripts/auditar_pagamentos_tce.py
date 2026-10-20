"""Consulta a conciliacao TCE; saida JSON, sem escrever em bancos de dados."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline.pagamentos import consultar_pagamentos_prefeitura, coletar_documentos_prefeitura


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--municipio", required=True)
    parser.add_argument("--exercicio", type=int, required=True)
    parser.add_argument("--referencia-final", required=True, help="YYYY-MM")
    parser.add_argument("--somente-financeiro", action="store_true")
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--salvar-coleta", type=Path, help="Preserva entradas antes da conciliacao, inclusive em caso de erro")
    parser.add_argument("--entrada", type=Path, help="Reprocessa uma coleta JSON sem consultar o TCE")
    args = parser.parse_args()
    if args.entrada:
        coleta = json.loads(args.entrada.read_text(encoding="utf-8"))
    else:
        coleta = coletar_documentos_prefeitura(
            codigo_municipio=args.municipio, exercicio=args.exercicio,
            referencia_final=args.referencia_final,
        )
    if args.salvar_coleta:
        args.salvar_coleta.parent.mkdir(parents=True, exist_ok=True)
        args.salvar_coleta.write_text(json.dumps(coleta, ensure_ascii=False, default=str), encoding="utf-8")
    resultado = consultar_pagamentos_prefeitura(
        codigo_municipio=args.municipio, exercicio=args.exercicio,
        referencia_final=args.referencia_final, consultar_porte=not args.somente_financeiro, coleta=coleta,
    )
    texto = json.dumps(resultado, ensure_ascii=False, indent=2)
    if args.saida:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_text(texto + "\n", encoding="utf-8")
        print(json.dumps({"arquivo": str(args.saida), "auditoria": resultado["auditoria"],
                          "controle_financeiro": resultado["controle_financeiro"],
                          "total_me_centavos": resultado["total_me_centavos"],
                          "classificacao_completa": resultado["classificacao_completa"]}, ensure_ascii=True))
    else:
        print(texto)


if __name__ == "__main__":
    main()
