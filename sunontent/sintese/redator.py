"""E5.2 Redator (docs/arquiteturas.md, E5.2): uma chamada ao modelo por tentativa da célula.

O prompt vem de prompts/synthesizer.j2: as regras comuns, a parte do nível (foco e expressões de analogia, de
`levelspec.instrucoes_redator`), a parte do formato (o orçamento calculado na E5.1) e, no retry, a peça
anterior com os marcadores restaurados e o relatório corretivo, agrupado pelo lugar da peça. A resposta é
validada pelo schema do formato (SaidaArtigo, SaidaCarrossel ou SaidaRoteiro); fora do schema, volta ao modelo
pelo `llm.py` sem consumir o `K`. No retry, usa o raciocínio `synthesizer_correcao` de config/models.yaml.
"""

import json
from collections.abc import Callable
from pathlib import Path

import jinja2

from sunontent.levelspec import LevelSpec
from sunontent.llm import ClienteLLM
from sunontent.schemas import (
    DESCRICAO_TIPO,
    RelatorioCorretivo,
    SaidaArtigo,
    SaidaCarrossel,
    SaidaRoteiro,
    Span,
)
from sunontent.sintese.montagem import Saida
from sunontent.sintese.pacote import Pacote

NO = "synthesizer"
RACIOCINIO_CORRECAO = "synthesizer_correcao"
SAIDA_DO_FORMATO: dict[str, type[Saida]] = {"artigo": SaidaArtigo, "carrossel": SaidaCarrossel, "roteiro": SaidaRoteiro}
_DESCRICAO_FORMATO = {
    "artigo": "um artigo analítico para web ou newsletter",
    "carrossel": "um carrossel para rede social, com gancho, slides de corpo e conclusão",
    "roteiro": "o roteiro de um vídeo curto, falado em voz alta, com marcações de tempo",
}
_DESCRICAO_NIVEL = {"iniciante": "iniciante", "intermediario": "intermediário", "avancado": "avançado ou institucional"}
_UNIDADE = {"artigo": "seção", "carrossel": "slide", "roteiro": "cena"}
_CAMPO = {"titulo": "título", "linha_fina": "linha fina", "gancho_visual": "gancho visual"}


def _grupos(relatorio: RelatorioCorretivo, nome_unidade: str) -> list[dict]:
    """As violações agrupadas pelo lugar da peça, na ordem em que o lugar aparece no relatório."""
    grupos: dict[str, list] = {}
    for v in relatorio.violacoes:
        if v.unidade_indice is not None:
            onde = f"{nome_unidade} {v.unidade_indice}"
            if v.campo in _CAMPO:
                onde += f", {_CAMPO[v.campo]}"
        elif v.campo in _CAMPO:
            onde = _CAMPO[v.campo]
        else:
            onde = "a peça inteira"
        grupos.setdefault(onde, []).append(v)
    return [{"onde": onde, "violacoes": lista} for onde, lista in grupos.items()]


def montar_prompt(
    raiz: Path,
    pacote: Pacote,
    spec: LevelSpec,
    anterior: Saida | None = None,
    relatorio: RelatorioCorretivo | None = None,
) -> str:
    ambiente = jinja2.Environment(
        loader=jinja2.FileSystemLoader(Path(raiz) / "prompts"),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    relatorio = relatorio or RelatorioCorretivo(certos=[], violacoes=[])
    return ambiente.get_template("synthesizer.j2").render(
        formato=pacote.formato,
        formato_descricao=_DESCRICAO_FORMATO[pacote.formato],
        nivel=pacote.nivel,
        nivel_descricao=_DESCRICAO_NIVEL[pacote.nivel],
        tipo_documento_descricao=DESCRICAO_TIPO[pacote.tipo_documento],
        emissor=pacote.emissor,
        data_documento=pacote.data_documento.isoformat(),
        ano_documento=pacote.data_documento.year,
        foco=spec.foco,
        marcadores=pacote.instrucoes.marcadores,
        legibilidade=pacote.instrucoes.legibilidade,
        orcamento=pacote.instrucoes.orcamento,
        orcamento_abertura=pacote.orcamento_abertura,
        orcamento_fechamento=pacote.orcamento_fechamento,
        tese=pacote.tese,
        claims=[{**c.__dict__, "texto": " ".join(c.texto.split())} for c in pacote.claims],
        analogias=pacote.analogias,
        anterior_json=json.dumps(anterior.model_dump(), ensure_ascii=False, indent=2) if anterior else "",
        certos=relatorio.certos,
        grupos=_grupos(relatorio, _UNIDADE[pacote.formato]),
        restantes=relatorio.restantes,
    )


def redigir(
    llm: ClienteLLM,
    raiz: Path,
    pacote: Pacote,
    spec: LevelSpec,
    *,
    run_id: str,
    registrar_span: Callable[[Span], None],
    tentativa: int,
    anterior: Saida | None = None,
    relatorio: RelatorioCorretivo | None = None,
) -> Saida:
    """Tentativa 1 sem `anterior`; no retry, com a peça anterior restaurada e o relatório corretivo."""
    prompt = montar_prompt(raiz, pacote, spec, anterior, relatorio)
    return llm.chamar(
        NO,
        prompt,
        SAIDA_DO_FORMATO[pacote.formato],
        run_id=run_id,
        registrar_span=registrar_span,
        nivel=pacote.nivel,
        celula_id=pacote.celula_id,
        tentativa=tentativa,
        raciocinio=RACIOCINIO_CORRECAO if anterior is not None else None,
    )
