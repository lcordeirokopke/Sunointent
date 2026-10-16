"""M5. Regra de analogia (docs/arquiteturas.md, E6). Determinístico, sem LLM.

Só no nível Iniciante: todo termo do glossário com `nivel_minimo` acima de iniciante, encontrado no texto das
unidades da peça (seção, slide ou fala da cena), precisa de uma `Analogia` no plano do nível e de um marcador de
analogia (`analogia.marcadores` do LevelSpec) na mesma unidade da primeira ocorrência do termo. O título e a linha
fina não são unidades e não entram na busca. Os termos são casados pela regra de docs/glossario.md.
"""

import re

from sunontent.glossario import Glossario, dobrar
from sunontent.levelspec import LevelSpec
from sunontent.metrics import campos, resultado
from sunontent.schemas import AnalogyRuleDetalhes, MetricaResultado, Peca, PlanoNivel, TermoAnalogia, Violacao


def _tem_marcador(texto: str, marcadores: list[str]) -> bool:
    dobrado = dobrar(texto)
    for marcador in marcadores:
        padrao = r"(?<!\w)" + r"\s+".join(re.escape(p) for p in dobrar(marcador).split()) + r"(?!\w)"
        if re.search(padrao, dobrado):
            return True
    return False


def _analogias_do_plano(plano: PlanoNivel, glossario: Glossario) -> dict:
    """Id do termo do glossário -> Analogia do plano. A analogia do glossário traz a referência `<id>#<n>`; a nova
    é ligada ao termo pela forma escrita."""
    achadas = {}
    for analogia in plano.analogias:
        termo_id = analogia.glossario_ref.split("#")[0] if analogia.glossario_ref else glossario.termo_da_forma(analogia.termo)
        if termo_id is not None:
            achadas.setdefault(termo_id, analogia)
    return achadas


def avaliar(peca: Peca, spec: LevelSpec, plano: PlanoNivel, glossario: Glossario) -> MetricaResultado:
    if spec.analogia is None:
        return resultado.montar(AnalogyRuleDetalhes(aplicavel=False, termos=[]), [])

    marcadores = spec.analogia.marcadores
    expressoes = ", ".join(f'"{m}"' for m in marcadores)
    unidade = campos.nome_da_unidade(peca.payload)
    em = "no" if unidade == "slide" else "na"
    analogias = _analogias_do_plano(plano, glossario)
    primeiras: dict[str, tuple[int, str, int, int]] = {}  # id -> (unidade, texto, inicio, fim) da primeira ocorrência
    textos = {c.unidade_indice: c.texto for c in campos.campos_da_peca(peca.payload) if c.campo == "texto"}
    for indice, texto in textos.items():
        for o in glossario.casar(texto):
            if glossario.termos[o.id].nivel_minimo != "iniciante":
                primeiras.setdefault(o.id, (indice, texto, o.inicio, o.fim))

    termos: list[TermoAnalogia] = []
    violacoes: list[Violacao] = []
    for termo_id, (indice, texto, inicio, fim) in primeiras.items():
        termo = glossario.termos[termo_id].termo
        analogia = analogias.get(termo_id)
        tem_marcador = _tem_marcador(texto, marcadores)
        termos.append(TermoAnalogia(
            termo=termo, glossario_id=termo_id, unidade_indice=indice,
            analogia_no_plano=analogia is not None, marcador_na_unidade=tem_marcador,
        ))
        if analogia is not None and tem_marcador:
            continue
        onde = f"{em} {unidade} {indice + 1}"
        if analogia is None:
            esperado, obtido = "analogia no plano do nível e marcador na unidade", "termo sem analogia no plano"
            instrucao = (
                f"O termo «{texto[inicio:fim]}» aparece {onde} sem analogia no plano: "
                "substitua-o por uma explicação em palavras simples."
            )
        else:
            esperado, obtido = f"marcador de analogia {onde}", "termo explicado sem marcador de analogia"
            instrucao = (
                f"Explique «{texto[inicio:fim]}» {onde}, na primeira vez que o usar, com a analogia do plano "
                f"({analogia.explicacao}), usando uma destas expressões: {expressoes}."
            )
        violacoes.append(Violacao(
            metrica="analogy_rule", trecho=texto[inicio:fim], esperado=esperado, obtido=obtido,
            instrucao_corretiva=instrucao, unidade_indice=indice, campo="texto", inicio=inicio, fim=fim,
        ))

    return resultado.montar(AnalogyRuleDetalhes(aplicavel=True, termos=termos), violacoes)
