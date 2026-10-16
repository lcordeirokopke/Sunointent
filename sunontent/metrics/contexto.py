"""Recuperação de contexto do juiz (M6), sem embedding (docs/metricas.md, M6).

Para cada unidade da peça (seção, slide ou cena): os claims que ela cita (a unidade sem `claim_ids`, como o gancho
e a abertura, usa todos os claims da peça), o chunk da âncora de cada claim, os vizinhos desse chunk (o anterior e
o seguinte) e os 3 chunks de maior sobreposição lexical com o texto da unidade, por BM25 simples, entre os que
ainda não entraram. Os chunks vão uma vez só para o prompt, na ordem do documento, e cada unidade cita os seus.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

from sunontent.glossario import dobrar
from sunontent.schemas import Ancora, Chunk, Claim, Peca, RoteiroPayload

QUANTIDADE_LEXICA = 3
K1 = 1.5
B = 0.75
# Palavras sem conteúdo, já sem acento (o texto passa por glossario.dobrar antes de virar token).
_VAZIAS = frozenset(
    "a ao aos as com como da das de do dos e em entre era foi ha isso ja mais mas na nas nao no nos o os ou para "
    "pela pelas pelo pelos por que se sem ser sera seu sua suas seus so sobre tambem tem um uma umas uns".split()
)
_PALAVRA = re.compile(r"\w+")


def tokens(texto: str) -> list[str]:
    """Palavras sem maiúsculas e sem acento, sem as palavras vazias e sem as de uma letra."""
    return [t for t in _PALAVRA.findall(dobrar(texto)) if len(t) > 1 and t not in _VAZIAS]


class BM25:
    """BM25 (Okapi) sobre os textos dados, com a idf sempre positiva: ln(1 + (N - df + 0,5) / (df + 0,5))."""

    def __init__(self, textos: list[str]):
        self._docs = [Counter(tokens(t)) for t in textos]
        self._tamanhos = [sum(d.values()) for d in self._docs]
        self._media = (sum(self._tamanhos) / len(self._docs)) if self._docs else 0.0
        frequencia = Counter(termo for d in self._docs for termo in d)
        n = len(self._docs)
        self._idf = {termo: math.log(1 + (n - df + 0.5) / (df + 0.5)) for termo, df in frequencia.items()}

    def pontuar(self, consulta: str) -> list[float]:
        termos = set(tokens(consulta))
        notas = []
        for doc, tamanho in zip(self._docs, self._tamanhos):
            nota = 0.0
            normalizador = K1 * (1 - B + B * tamanho / self._media) if self._media else K1
            for termo in termos:
                f = doc.get(termo, 0)
                if f:
                    nota += self._idf[termo] * f * (K1 + 1) / (f + normalizador)
            notas.append(nota)
        return notas


def chunk_da_ancora(chunks: list[Chunk], ancora: Ancora) -> int | None:
    """Posição do chunk que contém o início da âncora; None se nenhum contém."""
    ponto = (ancora.pagina, ancora.offset_inicio)
    for i, c in enumerate(chunks):
        if (c.pagina_inicio, c.offset_inicio) <= ponto < (c.pagina_fim, c.offset_fim):
            return i
    return None


@dataclass(frozen=True)
class ContextoUnidade:
    unidade_indice: int
    texto: str  # o texto da unidade; no roteiro, a fala e o gancho visual
    claim_ids: list[str]  # os claims que a unidade cita, ou todos os da peça se ela não cita nenhum
    cita_claims: bool  # False quando a unidade não cita claim e recebe todos os da peça
    chunks_ancora: list[str]  # chunk_id da âncora de cada claim e os vizinhos, na ordem do documento
    chunks_lexicos: list[str]  # até QUANTIDADE_LEXICA chunk_id por BM25, do maior para o menor


@dataclass(frozen=True)
class ContextoJuiz:
    unidades: list[ContextoUnidade]
    chunks: list[Chunk]  # os chunks citados por alguma unidade, na ordem do documento


def textos_das_unidades(peca: Peca) -> list[str]:
    """O texto que o juiz julga em cada unidade; no roteiro, a fala e o que aparece na tela."""
    if isinstance(peca.payload, RoteiroPayload):
        return [f"{c.fala}\n[na tela: {c.gancho_visual}]" for c in peca.payload.cenas]
    return peca.payload.textos()


def montar(peca: Peca, claims: dict[str, Claim], chunks: list[Chunk]) -> ContextoJuiz:
    """`claims` é o mapa id -> Claim do FactSheet; `chunks` são os do documento, na ordem do documento."""
    bm25 = BM25([c.texto for c in chunks])
    ancoras = {cid: chunk_da_ancora(chunks, claims[cid].ancora) for cid in peca.claim_ids if cid in claims}
    usados: set[int] = set()
    unidades = []
    for indice, (texto, citados) in enumerate(zip(textos_das_unidades(peca), peca.payload.claim_ids_por_unidade())):
        claim_ids = [c for c in (citados or peca.claim_ids) if c in claims]
        perto: set[int] = set()
        for cid in claim_ids:
            posicao = ancoras.get(cid)
            if posicao is not None:
                perto.update(p for p in (posicao - 1, posicao, posicao + 1) if 0 <= p < len(chunks))
        notas = bm25.pontuar(texto)
        candidatos = sorted(
            (i for i in range(len(chunks)) if i not in perto and notas[i] > 0), key=lambda i: (-notas[i], i)
        )[:QUANTIDADE_LEXICA]
        usados.update(perto)
        usados.update(candidatos)
        unidades.append(ContextoUnidade(
            unidade_indice=indice, texto=texto, claim_ids=claim_ids, cita_claims=bool(citados),
            chunks_ancora=[chunks[i].chunk_id for i in sorted(perto)],
            chunks_lexicos=[chunks[i].chunk_id for i in candidatos],
        ))
    return ContextoJuiz(unidades=unidades, chunks=[chunks[i] for i in sorted(usados)])
