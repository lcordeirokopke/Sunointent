"""E7. Router de uma célula (docs/arquiteturas.md, E7. Router): lógica completa aqui, sem pasta de domínio.

Avalia, em ordem:
1. falha de infraestrutura (`falha` no estado, da E5 ou da E6): ERRO_INFRA, com a classe e a mensagem;
2. todas as métricas passaram: APROVADA;
5. só com K >= 3, antes das regras 3 e 4: a tentativa não melhorou a anterior (as mesmas métricas reprovadas, ou
   mais, e nenhuma contagem de violações diminuiu): REPROVADA com motivo `nao_convergencia`;
3. falhou e tentativa < K: monta o relatório corretivo e devolve a célula à E5;
4. falhou e tentativa == K: REPROVADA com motivo `k_esgotado`.

A E7 é o único nó que grava estado final de célula (APROVADA, REPROVADA ou ERRO_INFRA), por
`persistencia.atualizar_celula`. No retry não grava nada: a E5 grava GERANDO ao começar. O scorecard da tentativa
anterior, para a regra 5, é lido de `scorecards`, `metricas` e `violacoes`. Dependências vêm de
config["configurable"]: repositorio e raiz_dados; `K` vem do graph.py.
"""

from pathlib import Path

from sunontent import persistencia
from sunontent.schemas import CelulaGrafoState, MetricaResultado, RelatorioCorretivo, Scorecard

ESTADOS_FINAIS = ("APROVADA", "REPROVADA", "ERRO_INFRA")
VIOLACOES_POR_METRICA = 5


def _valor(m: MetricaResultado) -> str:
    if isinstance(m.valor, bool):
        return "true" if m.valor else "false"
    return f"{m.valor:g}"


def relatorio_corretivo(scorecard: Scorecard) -> RelatorioCorretivo:
    """O que passou, com o valor; as violações de cada métrica que falhou, até 5, na ordem do scorecard; e quantas
    ficaram de fora, por métrica."""
    certos = [f"{m.nome}: {_valor(m)} (esperado: {m.faixa_esperada})" for m in scorecard.metricas if m.passou]
    violacoes, restantes = [], {}
    for m in scorecard.metricas:
        violacoes += m.violacoes[:VIOLACOES_POR_METRICA]
        if len(m.violacoes) > VIOLACOES_POR_METRICA:
            restantes[m.nome] = len(m.violacoes) - VIOLACOES_POR_METRICA
    return RelatorioCorretivo(certos=certos, violacoes=violacoes, restantes=restantes)


def nao_convergiu(atual: Scorecard, anterior: Scorecard) -> bool:
    """A tentativa não melhorou: reprova as mesmas métricas da anterior, ou mais, e nenhuma contagem de violações
    diminuiu."""
    antes = {m.nome: len(m.violacoes) for m in anterior.metricas}
    agora = {m.nome: len(m.violacoes) for m in atual.metricas}
    reprovadas_antes = {nome for nome, n in antes.items() if n}
    reprovadas_agora = {nome for nome, n in agora.items() if n}
    return reprovadas_antes <= reprovadas_agora and all(agora.get(nome, 0) >= n for nome, n in antes.items())


def router(state: CelulaGrafoState, config, *, K: int) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados = configuravel["repositorio"], Path(configuravel["raiz_dados"])
    run_id, celula = state.run_id, state.celula

    def finalizar(**campos) -> dict:
        final = celula.model_copy(update=campos)
        persistencia.atualizar_celula(repositorio, raiz_dados, run_id, final)
        return {"celula": final, "relatorio": None}

    if state.falha is not None:
        return finalizar(estado="ERRO_INFRA", erro_classe=state.falha.classe, erro_mensagem=state.falha.mensagem)

    scorecard = celula.scorecard
    if scorecard is None or scorecard.tentativa != celula.tentativa:
        raise ValueError(f"E7: a célula {celula.celula_id} chegou sem o scorecard da tentativa {celula.tentativa}")
    if scorecard.veredito == "APROVADA":
        return finalizar(estado="APROVADA")

    if K >= 3 and celula.tentativa >= 2:
        anterior = persistencia.carregar_scorecard(repositorio, run_id, celula.celula_id, celula.tentativa - 1)
        if anterior is not None and nao_convergiu(scorecard, anterior):
            return finalizar(estado="REPROVADA", motivo_reprovacao="nao_convergencia")

    if celula.tentativa < K:
        return {"relatorio": relatorio_corretivo(scorecard)}
    return finalizar(estado="REPROVADA", motivo_reprovacao="k_esgotado")
