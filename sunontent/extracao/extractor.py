"""E2. Extractor & Anchor: o modelo lê o documento e propõe claims; o código ancora cada um.

- `extrair`: primeira rodada, documento inteiro numa chamada; os ids são renumerados C01, C02...
  na ordem devolvida.
- `corrigir`: rodadas seguintes, só os claims recusados, com o motivo; mantém os ids.
- `ancorar`: resolve âncora e números de todas as propostas (extracao/ancoragem.py) e separa as
  que não resolvem, com o motivo, para a E3 devolver ao modelo.

O prompt vem de prompts/extractor.j2 e mostra o mesmo texto limpo que a E3 usa para validar.
"""

import json
from collections.abc import Callable
from pathlib import Path

import jinja2

from sunontent.extracao import ancoragem
from sunontent.llm import ClienteLLM
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import Claim, ClaimProposto, DocumentoProcessado, SaidaExtractor, Span

DESCRICAO_TIPO = {
    "copom_ata": "ata de reunião do Comitê de Política Monetária",
    "cvm_fato_relevante": "fato relevante publicado na CVM",
    "b3_release": "release de resultados trimestrais",
}


def blocos(documento: DocumentoProcessado) -> list[dict]:
    """Chunks com o texto separado por página, para o modelo saber onde cada página começa."""
    texto = {p.pagina: p.texto_limpo for p in documento.paginas}
    saida = []
    for chunk in documento.chunks:
        partes = []
        for pagina in range(chunk.pagina_inicio, chunk.pagina_fim + 1):
            inicio = chunk.offset_inicio if pagina == chunk.pagina_inicio else 0
            fim = chunk.offset_fim if pagina == chunk.pagina_fim else len(texto[pagina])
            trecho = texto[pagina][inicio:fim]
            if trecho.strip():
                partes.append({"pagina": pagina, "texto": trecho})
        saida.append({"chunk_id": chunk.chunk_id, "secao": chunk.secao, "partes": partes})
    return saida


def montar_prompt(
    raiz: Path, documento: DocumentoProcessado, correcoes: list[tuple[ClaimProposto, str]] | None = None
) -> str:
    ambiente = jinja2.Environment(
        loader=jinja2.FileSystemLoader(Path(raiz) / "prompts"),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return ambiente.get_template("extractor.j2").render(
        tipo_documento_descricao=DESCRICAO_TIPO[documento.tipo_documento],
        emissor=documento.emissor,
        data_documento=documento.data_documento.isoformat(),
        blocos=blocos(documento),
        correcoes=[
            {
                "claim": claim,
                "motivo": motivo,
                "claim_json": json.dumps(claim.model_dump(), ensure_ascii=False, indent=2),
            }
            for claim, motivo in (correcoes or [])
        ],
    )


def extrair(
    llm: ClienteLLM,
    raiz: Path,
    documento: DocumentoProcessado,
    *,
    run_id: str,
    registrar_span: Callable[[Span], None],
) -> dict[str, ClaimProposto]:
    saida = llm.chamar(
        "extractor", montar_prompt(raiz, documento), SaidaExtractor, run_id=run_id, registrar_span=registrar_span
    )
    if not saida.claims:
        raise FalhaTerminal(f"E2: o extractor não devolveu nenhum claim para {documento.doc_id}")
    largura = max(2, len(str(len(saida.claims))))
    propostas = {}
    for numero, claim in enumerate(saida.claims, start=1):
        claim_id = f"C{numero:0{largura}d}"
        propostas[claim_id] = claim.model_copy(update={"id": claim_id})
    return propostas


def corrigir(
    llm: ClienteLLM,
    raiz: Path,
    documento: DocumentoProcessado,
    propostas: dict[str, ClaimProposto],
    correcoes: dict[str, str],
    *,
    run_id: str,
    registrar_span: Callable[[Span], None],
) -> dict[str, ClaimProposto]:
    """Propostas com os claims recusados substituídos pela versão corrigida. Id fora da lista é ignorado;
    claim recusado que o modelo não devolver fica como estava e volta a ser recusado."""
    pedido = [(propostas[claim_id], motivo) for claim_id, motivo in sorted(correcoes.items())]
    saida = llm.chamar(
        "extractor",
        montar_prompt(raiz, documento, pedido),
        SaidaExtractor,
        run_id=run_id,
        registrar_span=registrar_span,
    )
    atualizadas = dict(propostas)
    for claim in saida.claims:
        if claim.id in correcoes:
            atualizadas[claim.id] = claim
    return atualizadas


def ancorar(
    documento: DocumentoProcessado, propostas: dict[str, ClaimProposto]
) -> tuple[list[Claim], dict[str, str]]:
    """Claims ancorados, em ordem de id, e o motivo de cada proposta que não resolveu."""
    claims, pendencias = [], {}
    for claim_id in sorted(propostas):
        try:
            claims.append(ancoragem.resolver(documento, propostas[claim_id]))
        except ancoragem.NaoResolvido as erro:
            pendencias[claim_id] = str(erro)
    return claims, pendencias
