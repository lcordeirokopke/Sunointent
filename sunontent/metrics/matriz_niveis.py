"""Matriz de confusão de níveis (docs/metricas.md, M6): cruza o nível pedido e o `nivel_percebido` pelo juiz, por
formato. Só informativa: não reprova nada.

`montar` e `formatar` são puras e servem à calibração do juiz (tests/test_juiz.py). O comando lê os scorecards
gravados no Supabase, pela ConsultaSupabase (só leitura), e imprime a matriz da última tentativa julgada de cada
célula:

    python -m sunontent.metrics.matriz_niveis [run_id ...]

Sem run_id, usa todos os runs.
"""

import argparse
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

from sunontent.schemas import FORMATOS, NIVEIS

Matriz = dict[str, dict[str, dict[str, int]]]  # formato -> nível pedido -> nível percebido -> células
_ROTULO = {"iniciante": "iniciante", "intermediario": "intermediário", "avancado": "avançado"}


def montar(linhas: Iterable[tuple[str, str, str]]) -> Matriz:
    """`linhas` são (formato, nível pedido, nível percebido)."""
    matriz = {f: {p: {q: 0 for q in NIVEIS} for p in NIVEIS} for f in FORMATOS}
    for formato, pedido, percebido in linhas:
        matriz[formato][pedido][percebido] += 1
    return matriz


def acerto(matriz: Matriz, formato: str | None = None) -> tuple[int, int]:
    """(na diagonal, total), num formato ou em todos."""
    formatos = [formato] if formato else list(FORMATOS)
    diagonal = sum(matriz[f][n][n] for f in formatos for n in NIVEIS)
    total = sum(v for f in formatos for p in NIVEIS for v in matriz[f][p].values())
    return diagonal, total


def formatar(matriz: Matriz) -> str:
    """Uma tabela por formato: linhas com o nível pedido, colunas com o percebido."""
    largura = max(len(r) for r in _ROTULO.values()) + 2
    blocos = []
    for formato in FORMATOS:
        diagonal, total = acerto(matriz, formato)
        linhas = [f"{formato}: {diagonal} de {total} no nível pedido",
                  "pedido \\ percebido".ljust(largura + 8) + "".join(_ROTULO[n].rjust(largura) for n in NIVEIS)]
        for pedido in NIVEIS:
            linhas.append(_ROTULO[pedido].ljust(largura + 8)
                          + "".join(str(matriz[formato][pedido][q]).rjust(largura) for q in NIVEIS))
        blocos.append("\n".join(linhas))
    diagonal, total = acerto(matriz)
    blocos.append(f"total: {diagonal} de {total} no nível pedido")
    return "\n\n".join(blocos)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sunontent.metrics.matriz_niveis", description=__doc__.split("\n\n")[0])
    parser.add_argument("run_ids", nargs="*", help="runs a incluir; sem nenhum, todos")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    from sunontent import persistencia
    from sunontent.retentativa import Falha

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        linhas = persistencia.criar_consulta_supabase(os.environ).niveis_percebidos(args.run_ids or None)
    except Falha as falha:
        print(f"erro ({falha.classe}): {falha.mensagem}", file=sys.stderr)
        return 1
    if not linhas:
        print("nenhuma célula julgada pelo juiz nos runs pedidos")
        return 0
    print(formatar(montar((l["formato"], l["nivel"], l["nivel_percebido"]) for l in linhas)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
