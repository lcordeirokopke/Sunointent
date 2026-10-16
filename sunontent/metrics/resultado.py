"""Monta o MetricaResultado de qualquer métrica (docs/arquiteturas.md, Scorecard e violação).

Valor e faixa: `numeric_fidelity`, `analogy_rule` e M7 (`plan_contract`) valem o número de violações, com faixa "0";
`text_size` vale o número de unidades fora do limite, e a faixa traz o limite renderizado do LevelSpec;
`readability` e `term_density` valem o que medem (o IFL e a densidade), passado em `valor`; `llm_judge` vale true
quando não reprova nada e false quando reprova.
"""

from sunontent.schemas import Detalhes, MetricaResultado, Violacao


def montar(
    detalhes: Detalhes, violacoes: list[Violacao], faixa_esperada: str = "0", valor: float | None = None
) -> MetricaResultado:
    nome = detalhes.metrica
    return MetricaResultado(
        nome=nome,
        valor=not violacoes if nome == "llm_judge" else float(len(violacoes)) if valor is None else valor,
        faixa_esperada="true" if nome == "llm_judge" else faixa_esperada,
        passou=not violacoes,
        violacoes=violacoes,
        detalhes=detalhes,
    )
