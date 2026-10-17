"""Matrizes de confusão de níveis (docs/metricas.md, M6 e M3). Só informativas: não reprovam nada.

A do juiz cruza o nível pedido e o `nivel_percebido`, por formato. A estatística cruza o nível pedido e o sinal de
nível da peça (M3, `nivel_estatistico`): no artigo, o nível da faixa de IFL em que o IFL cai; no carrossel e no
roteiro, `iniciante` quando o IFL está no piso do Iniciante e a peça tem analogia, senão `acima_de_iniciante`.

`montar` e `formatar` (e as duas versões `_estatistica`) são puras e servem à calibração do juiz
(tests/test_juiz.py). O comando lê os scorecards gravados no Supabase, pela ConsultaSupabase (só leitura), e imprime
as duas matrizes da última tentativa avaliada de cada célula:

    python -m sunontent.metrics.matriz_niveis [run_id ...]

Sem run_id, usa todos os runs.
"""

import argparse
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from sunontent.levelspec import LevelSpec
from sunontent.metrics import readability
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


LADOS = ("iniciante", "acima_de_iniciante")
MatrizEstatistica = dict[str, dict[str, dict[str, int]]]  # formato -> nível pedido -> sinal -> células
_ROTULO_LADO = {"iniciante": "iniciante", "acima_de_iniciante": "acima de iniciante", **_ROTULO}


def colunas(formato: str) -> tuple[str, ...]:
    """Os valores do sinal de nível no formato: os 3 níveis no artigo (faixa de IFL); no carrossel e no roteiro,
    iniciante ou acima de iniciante (IFL e analogia)."""
    return tuple(NIVEIS) if formato == "artigo" else LADOS


def sinal_da_linha(linha: dict, specs: Mapping[str, LevelSpec] | None = None) -> str | None:
    """O sinal de uma linha de `ConsultaSupabase.niveis_estatisticos`. No artigo, é recalculado do `ifl` gravado
    pelas faixas atuais do LevelSpec quando `specs` e o `ifl` existem; senão, vale o `nivel_estatistico` gravado."""
    if linha["formato"] == "artigo" and specs and linha.get("ifl") is not None:
        return readability.nivel_pelo_ifl_do_artigo(float(linha["ifl"]), specs)
    return linha["nivel_estatistico"]


def montar_estatistica(linhas: Iterable[tuple[str, str, str]]) -> MatrizEstatistica:
    """`linhas` são (formato, nível pedido, sinal de nível da peça)."""
    matriz = {f: {p: {c: 0 for c in colunas(f)} for p in NIVEIS} for f in FORMATOS}
    for formato, pedido, sinal in linhas:
        matriz[formato][pedido][sinal] += 1
    return matriz


def acerto_estatistico(matriz: MatrizEstatistica, formato: str | None = None) -> tuple[int, int]:
    """(células no lado certo, total). No artigo, o sinal igual ao nível pedido; no carrossel e no roteiro, o
    Iniciante pedido em `iniciante` e os outros dois níveis em `acima_de_iniciante`."""
    formatos = [formato] if formato else list(FORMATOS)
    certas = sum(
        matriz[f][p][p if f == "artigo" else "iniciante" if p == "iniciante" else "acima_de_iniciante"]
        for f in formatos for p in NIVEIS
    )
    total = sum(v for f in formatos for p in NIVEIS for v in matriz[f][p].values())
    return certas, total


def formatar_estatistica(matriz: MatrizEstatistica) -> str:
    largura = max(len(r) for r in _ROTULO_LADO.values()) + 2
    blocos = []
    for formato in FORMATOS:
        certas, total = acerto_estatistico(matriz, formato)
        cols = colunas(formato)
        linhas = [f"{formato}: {certas} de {total} no sinal certo",
                  "pedido \\ IFL".ljust(largura + 2) + "".join(_ROTULO_LADO[c].rjust(largura) for c in cols)]
        for pedido in NIVEIS:
            linhas.append(_ROTULO[pedido].ljust(largura + 2)
                          + "".join(str(matriz[formato][pedido][c]).rjust(largura) for c in cols))
        blocos.append("\n".join(linhas))
    certas, total = acerto_estatistico(matriz)
    blocos.append(f"total: {certas} de {total} no sinal certo")
    return "\n\n".join(blocos)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sunontent.metrics.matriz_niveis", description=__doc__.split("\n\n")[0])
    parser.add_argument("run_ids", nargs="*", help="runs a incluir; sem nenhum, todos")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    from sunontent import levelspec, persistencia
    from sunontent.retentativa import Falha

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    try:
        consulta = persistencia.criar_consulta_supabase(os.environ)
        linhas = consulta.niveis_percebidos(args.run_ids or None)
        estatisticas = consulta.niveis_estatisticos(args.run_ids or None)
        specs = levelspec.carregar_todos(Path(__file__).resolve().parents[2])
    except Falha as falha:
        print(f"erro ({falha.classe}): {falha.mensagem}", file=sys.stderr)
        return 1
    if not linhas and not estatisticas:
        print("nenhuma célula avaliada nos runs pedidos")
        return 0
    if linhas:
        print("Nível percebido pelo juiz (M6)\n")
        print(formatar(montar((l["formato"], l["nivel"], l["nivel_percebido"]) for l in linhas)))
    if estatisticas:
        print("\nSinal de nível pelo IFL (M3): artigo pela faixa de IFL; carrossel e roteiro pelo IFL e pela analogia\n")
        sinais = [(l["formato"], l["nivel"], sinal_da_linha(l, specs)) for l in estatisticas]
        print(formatar_estatistica(montar_estatistica(x for x in sinais if x[2])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
