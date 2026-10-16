"""E6. Hybrid Evaluator de uma célula: chama metrics/ e grava o scorecard da tentativa.

As 4 verificações determinísticas rodam sempre, todas: M1 (`numeric_fidelity`), M2 (`text_size`), M5
(`analogy_rule`) e o `plan_contract` que a montagem da E5.3 deixou na célula. O juiz (M6) só roda se as 4 passarem
e se config["configurable"] traz um `juiz`; sem ele, o scorecard tem as 4. Os claims do plano do nível e a
`tabela_numeros` vêm do FactSheet assinado. O veredito é da tentativa: APROVADA se tudo passou, REPROVADA se algo
falhou, ERRO_INFRA se o juiz falhou com a peça existente. Scorecard, métricas e violações vão numa transação
(docs/fluxos/avaliacao.md).

Uma `Falha` (leitura, juiz ou gravação) não sobe: vai para `falha` no estado, e a E7 grava a célula em ERRO_INFRA.
O nó não grava estado de célula. Dependências vêm de config["configurable"]: repositorio, raiz_dados, raiz e,
opcional, juiz.
"""

import time
from pathlib import Path

from sunontent import glossario as modulo_glossario
from sunontent import levelspec, persistencia
from sunontent.metrics import analogy_rule, numeric_fidelity, text_size
from sunontent.retentativa import Falha, FalhaTerminal
from sunontent.schemas import CelulaGrafoState, FalhaCelula, Scorecard, Span


def evaluate(state: CelulaGrafoState, config) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados, raiz = configuravel["repositorio"], Path(configuravel["raiz_dados"]), configuravel["raiz"]
    run_id, celula = state.run_id, state.celula
    peca, contrato = celula.peca, celula.plan_contract
    if peca is None or contrato is None:
        raise ValueError(f"E6: a célula {celula.celula_id} chegou sem a peça ou sem o plan_contract da E5")

    tokens = state.tokens_tentativa

    def registrar_span(span: Span) -> None:
        nonlocal tokens
        tokens += span.tokens_entrada + span.tokens_saida
        persistencia.gravar_span(repositorio, raiz_dados, span)

    falha: Falha | None = None
    try:
        spec = levelspec.carregar(raiz, celula.nivel)
        glossario = modulo_glossario.carregar(raiz)
        gravado = persistencia.carregar_plano(repositorio, raiz_dados, run_id)
        if gravado is None:
            raise FalhaTerminal(f"E6: o run {run_id} não tem plano editorial gravado")
        plano_nivel = next(p for p in gravado[0].niveis if p.nivel == celula.nivel)
        factsheet = persistencia.carregar_factsheet(repositorio, run_id)

        do_plano = {c.claim_id for c in plano_nivel.claims}
        claims_do_plano = [c for c in factsheet.claims if c.id in do_plano]
        metricas = [
            numeric_fidelity.avaliar(peca, claims_do_plano, factsheet.tabela_numeros, factsheet.data_documento, glossario),
            text_size.avaliar(peca, spec),
            analogy_rule.avaliar(peca, spec, plano_nivel, glossario),
            contrato,
        ]
        veredito = "APROVADA" if all(m.passou for m in metricas) else "REPROVADA"
        juiz = configuravel.get("juiz")
        if veredito == "APROVADA" and juiz is not None:
            try:
                m6 = juiz(peca, plano_nivel, factsheet, glossario, run_id=run_id, registrar_span=registrar_span)
            except Falha as erro:
                falha, veredito = erro, "ERRO_INFRA"
            else:
                metricas.append(m6)
                veredito = "APROVADA" if m6.passou else "REPROVADA"

        inicio = state.inicio_tentativa if state.inicio_tentativa is not None else time.monotonic()
        scorecard = Scorecard(
            celula_id=celula.celula_id, tentativa=peca.tentativa, metricas=metricas, veredito=veredito,
            custo_tokens=tokens, duracao_ms=max(0, round((time.monotonic() - inicio) * 1000)),
        )
        persistencia.gravar_scorecard(repositorio, raiz_dados, run_id, scorecard)
    except Falha as erro:
        return {"falha": FalhaCelula(classe=erro.classe, mensagem=erro.mensagem), "tokens_tentativa": tokens}

    resultado = {"celula": celula.model_copy(update={"scorecard": scorecard}), "tokens_tentativa": tokens}
    if falha is not None:
        resultado["falha"] = FalhaCelula(classe=falha.classe, mensagem=falha.mensagem)
    return resultado
