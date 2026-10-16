"""E5. Format Synthesizer de uma célula: chama sintese/ e devolve a célula com a peça da tentativa.

Lê o plano por `carregar_plano` e o FactSheet assinado por `carregar_factsheet`, marca a célula como GERANDO,
monta o pacote (E5.1), chama o redator (E5.2) e monta a peça com o `plan_contract` (E5.3). No retry, o redator
recebe a peça anterior (`celula.peca`) com os marcadores restaurados e o relatório corretivo do estado. Grava a
peça com a célula em AVALIANDO e a tentativa nova numa transação (docs/fluxos/sintese.md).

Uma `Falha` (leitura, provider, resposta fora do schema esgotada ou gravação) não sobe: vai para `falha` no estado,
e a E7 grava a célula em ERRO_INFRA. O estado leva também os tokens das chamadas de LLM e o início da tentativa,
para o scorecard da E6. Dependências vêm de config["configurable"]: repositorio, raiz_dados, raiz (config/ e
prompts/) e llm.
"""

import time
from pathlib import Path

from sunontent import glossario as modulo_glossario
from sunontent import levelspec, persistencia
from sunontent.retentativa import Falha, FalhaTerminal
from sunontent.schemas import CelulaGrafoState, FalhaCelula, Span
from sunontent.sintese import montagem, pacote, redator


def synth(state: CelulaGrafoState, config) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados, raiz = configuravel["repositorio"], Path(configuravel["raiz_dados"]), configuravel["raiz"]
    run_id, celula = state.run_id, state.celula
    inicio = time.monotonic()
    tokens = 0

    def registrar_span(span: Span) -> None:
        nonlocal tokens
        tokens += span.tokens_entrada + span.tokens_saida
        persistencia.gravar_span(repositorio, raiz_dados, span)

    try:
        spec = levelspec.carregar(raiz, celula.nivel)
        glossario = modulo_glossario.carregar(raiz)
        gravado = persistencia.carregar_plano(repositorio, raiz_dados, run_id)
        if gravado is None:
            raise FalhaTerminal(f"E5: o run {run_id} não tem plano editorial gravado")
        plano, _ = gravado
        factsheet = persistencia.carregar_factsheet(repositorio, run_id)

        persistencia.atualizar_celula(repositorio, raiz_dados, run_id, celula.model_copy(update={"estado": "GERANDO"}))

        do_formato = pacote.montar(plano, celula.nivel, celula.formato, factsheet, spec, glossario)
        tentativa = celula.tentativa + 1
        saida = redator.redigir(
            configuravel["llm"], raiz, do_formato, spec,
            run_id=run_id,
            registrar_span=registrar_span,
            tentativa=tentativa,
            anterior=montagem.restaurar(celula.peca) if celula.peca is not None else None,
            relatorio=state.relatorio,
        )
        peca, contrato = montagem.montar(saida, do_formato, spec, tentativa)

        avaliando = celula.model_copy(update={
            "estado": "AVALIANDO", "tentativa": tentativa, "peca": peca, "plan_contract": contrato, "scorecard": None,
        })
        persistencia.gravar_peca(repositorio, raiz_dados, run_id, peca, avaliando)
    except Falha as erro:
        return {"falha": FalhaCelula(classe=erro.classe, mensagem=erro.mensagem), "tokens_tentativa": tokens}
    return {"celula": avaliando, "falha": None, "tokens_tentativa": tokens, "inicio_tentativa": inicio}
