"""E2: resolve a âncora e os números de um claim proposto pelo extractor. Determinístico.

O modelo cita o trecho (`literal_fonte`) e o bruto de cada número; a posição vem do código:
- o trecho é procurado no texto limpo de uma única página, primeiro dentro do chunk citado e
  depois no documento inteiro, aceitando diferença só em espaço/quebra de linha e na grafia
  de aspas, apóstrofos e travessões;
- o `literal_fonte` gravado é o texto limpo exato no intervalo encontrado;
- cada número é a entrada da `tabela_numeros` com aquele bruto dentro do intervalo.

O que não resolve vira motivo, devolvido ao extractor na rodada seguinte (docs/fluxos/extracao.md).
"""

import re

from sunontent.schemas import Ancora, Claim, ClaimProposto, DocumentoProcessado, Numero

_EQUIVALENTES = [
    "\"“”„«»",
    "'‘’‚`´",
    "-‐‑‒–—―−",
]
_CLASSE = {c: "[" + re.escape(grupo) + "]" for grupo in _EQUIVALENTES for c in grupo}


class NaoResolvido(Exception):
    """Claim proposto sem âncora ou número válido; a mensagem é o motivo devolvido ao modelo."""


def padrao_do_trecho(trecho: str) -> re.Pattern | None:
    """Regex do trecho com espaço flexível e aspas/travessões equivalentes; None se o trecho é vazio."""
    tokens = trecho.split()
    if not tokens:
        return None
    partes = ["".join(_CLASSE.get(c, re.escape(c)) for c in token) for token in tokens]
    return re.compile(r"\s+".join(partes))


def localizar(documento: DocumentoProcessado, trecho: str, chunk_id: str | None = None) -> Ancora:
    """Âncora do trecho numa única página. Prefere ocorrência dentro do chunk; fora dele, exige uma só."""
    padrao = padrao_do_trecho(trecho)
    if padrao is None:
        raise NaoResolvido("literal_fonte vazio")
    ocorrencias = [
        Ancora(pagina=p.pagina, offset_inicio=m.start(), offset_fim=m.end())
        for p in documento.paginas
        for m in padrao.finditer(p.texto_limpo)
    ]
    chunk = next((c for c in documento.chunks if c.chunk_id == chunk_id), None)
    if chunk is not None:
        no_chunk = [a for a in ocorrencias if _dentro_do_chunk(a, chunk)]
        if no_chunk:
            return no_chunk[0]
    if len(ocorrencias) == 1:
        return ocorrencias[0]
    if len(ocorrencias) > 1:
        paginas = sorted({a.pagina for a in ocorrencias})
        raise NaoResolvido(
            f"literal_fonte aparece {len(ocorrencias)} vezes no documento (páginas {paginas}) e não no "
            f"chunk {chunk_id}; cite um trecho mais longo, que só exista uma vez"
        )
    if _atravessa_pagina(documento, padrao):
        raise NaoResolvido(
            "literal_fonte atravessa a quebra de página ou uma nota de rodapé; cite só a parte de uma página"
        )
    raise NaoResolvido(
        "literal_fonte não foi encontrado no documento; copie o trecho exatamente como está no texto, "
        "sem parafrasear, corrigir ou juntar partes distantes"
    )


def _dentro_do_chunk(ancora: Ancora, chunk) -> bool:
    inicio = (chunk.pagina_inicio, chunk.offset_inicio)
    fim = (chunk.pagina_fim, chunk.offset_fim)
    return inicio <= (ancora.pagina, ancora.offset_inicio) and (ancora.pagina, ancora.offset_fim) <= fim


def _atravessa_pagina(documento: DocumentoProcessado, padrao: re.Pattern) -> bool:
    texto = "\n".join(p.texto_limpo for p in documento.paginas)
    return padrao.search(texto) is not None


def _sem_espaco(texto: str) -> str:
    return "".join(texto.split())


def numeros_no_trecho(documento: DocumentoProcessado, ancora: Ancora, brutos: list[str]) -> list[Numero]:
    """Entrada da tabela_numeros para cada bruto, dentro do intervalo, sem repetir a mesma entrada."""
    dentro = [
        n
        for n in documento.tabela_numeros
        if n.ancora.pagina == ancora.pagina
        and ancora.offset_inicio <= n.ancora.offset_inicio
        and n.ancora.offset_fim <= ancora.offset_fim
    ]
    usados: list[Numero] = []
    for bruto in brutos:
        alvo = _sem_espaco(bruto)
        candidato = next((n for n in dentro if _sem_espaco(n.bruto) == alvo and n not in usados), None)
        if candidato is None:
            presentes = ", ".join(sorted({n.bruto for n in dentro})) or "nenhum"
            raise NaoResolvido(
                f"o número {bruto!r} não está dentro do literal_fonte (números do trecho: {presentes}); "
                "liste cada número exatamente como aparece no trecho citado"
            )
        usados.append(candidato)
    return usados


def resolver(documento: DocumentoProcessado, proposto: ClaimProposto) -> Claim:
    """Claim com âncora e números resolvidos, ou NaoResolvido com o motivo."""
    if not proposto.texto.strip():
        raise NaoResolvido("texto vazio")
    ancora = localizar(documento, proposto.literal_fonte, proposto.chunk_id)
    pagina = documento.paginas[ancora.pagina - 1].texto_limpo
    return Claim(
        id=proposto.id,
        texto=proposto.texto.strip(),
        numeros=numeros_no_trecho(documento, ancora, proposto.numeros),
        tipo=proposto.tipo,
        tags=list(dict.fromkeys(proposto.tags)),
        ancora=ancora,
        literal_fonte=pagina[ancora.offset_inicio : ancora.offset_fim],
    )
