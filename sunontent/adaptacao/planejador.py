"""E4.2 Planejador dos 3 níveis (docs/arquiteturas.md, E4.2): uma chamada ao modelo para os 3 níveis.

O prompt vem de prompts/adapter.j2, parametrizado pelo LevelSpec dos 3 níveis. Recebe os claims do
FactSheet assinado (id, tipo, tags e texto), onde cada um é candidato, os termos do glossário acima de
iniciante de cada claim, com o nível mínimo e se o termo tem analogia no glossário, e os termos do glossário
sem analogia. O prompt diz também o que vem primeiro no tipo de documento. Na correção, recebe também o
plano enviado e só a lista de problemas do gate, e usa o raciocínio `adapter_correcao` de config/models.yaml.
"""

import json
from collections.abc import Callable
from pathlib import Path

import jinja2

from sunontent.adaptacao.prefiltro import Prefiltro
from sunontent.glossario import Glossario
from sunontent.levelspec import LevelSpec
from sunontent.llm import ClienteLLM
from sunontent.schemas import DESCRICAO_TIPO, NIVEIS, FactSheet, SaidaPlanejador, Span

NO = "adapter"
RACIOCINIO_CORRECAO = "adapter_correcao"


def _onde_e_candidato(claim_id: str, prefiltro: Prefiltro) -> str:
    niveis = [n for n in NIVEIS if claim_id in prefiltro.candidatos[n]]
    if len(niveis) == len(NIVEIS):
        return "candidato nos 3 níveis"
    if not niveis:
        return "não é candidato"
    return f"candidato só em {', '.join(niveis)}"


def _termos_do_claim(texto: str, glossario: Glossario) -> str:
    termos = glossario.acima_de(glossario.termos_em(texto), "iniciante")
    if not termos:
        return "nenhum"
    return ", ".join(
        f"{glossario.termos[t].termo} ({glossario.termos[t].nivel_minimo}, "
        f"{'com analogia' if glossario.termos[t].analogias else 'sem analogia'})"
        for t in termos
    )


def montar_prompt(
    raiz: Path,
    factsheet: FactSheet,
    specs: dict[str, LevelSpec],
    prefiltro: Prefiltro,
    glossario: Glossario,
    termos_sem_analogia: list[str],
    anterior: SaidaPlanejador | None = None,
    problemas: list[str] = (),
) -> str:
    ambiente = jinja2.Environment(
        loader=jinja2.FileSystemLoader(Path(raiz) / "prompts"),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return ambiente.get_template("adapter.j2").render(
        tipo_documento=factsheet.tipo_documento,
        tipo_documento_descricao=DESCRICAO_TIPO[factsheet.tipo_documento],
        emissor=factsheet.emissor,
        data_documento=factsheet.data_documento.isoformat(),
        specs=[specs[n] for n in NIVEIS],
        claims=[
            {
                "id": c.id,
                "tipo": c.tipo,
                "tags": c.tags,
                "candidato": _onde_e_candidato(c.id, prefiltro),
                "termos": _termos_do_claim(c.texto, glossario),
                "texto": " ".join(c.texto.split()),
            }
            for c in factsheet.claims
        ],
        termos=[glossario.termos[t] for t in termos_sem_analogia],
        problemas=list(problemas),
        anterior_json=json.dumps(anterior.model_dump(), ensure_ascii=False, indent=2) if anterior else "",
    )


def planejar(
    llm: ClienteLLM,
    raiz: Path,
    factsheet: FactSheet,
    specs: dict[str, LevelSpec],
    prefiltro: Prefiltro,
    glossario: Glossario,
    termos_sem_analogia: list[str],
    *,
    run_id: str,
    registrar_span: Callable[[Span], None],
    rodada: int = 1,
    anterior: SaidaPlanejador | None = None,
    problemas: list[str] = (),
) -> SaidaPlanejador:
    """Rodada 1 sem `anterior`; nas correções, com o plano enviado e os problemas do gate. A rodada vai no span."""
    prompt = montar_prompt(raiz, factsheet, specs, prefiltro, glossario, termos_sem_analogia, anterior, problemas)
    return llm.chamar(
        NO,
        prompt,
        SaidaPlanejador,
        run_id=run_id,
        registrar_span=registrar_span,
        rodada=rodada,
        raciocinio=RACIOCINIO_CORRECAO if anterior is not None else None,
    )
