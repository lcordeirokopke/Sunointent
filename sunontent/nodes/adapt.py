"""E4. Plano editorial: chama adaptacao/ sobre o FactSheet assinado e coloca o plano e as 9 células no estado.

Roda o pré-filtro, o planejador, o complemento e o gate em loop, até `rodadas` (graph.RODADAS_PLANO): a
rodada 1 é o plano e as seguintes corrigem só os problemas do gate. Grava cada rodada (a saída do planejador
e os problemas) em rodadas_plano e validacoes_plano; se o problema continua depois da última, a limpeza
(adaptacao/limpeza.py) corrige o plano por código e o `aviso` registra o que mudou. Sem nenhum claim
candidato nos 3 níveis, FalhaTerminal antes de chamar o modelo. Grava o plano e as 9 células em PENDENTE
(docs/fluxos/plano-editorial.md).
Dependências vêm de config["configurable"]: repositorio, raiz_dados, raiz (config/ e prompts/) e llm.
"""

from pathlib import Path

from sunontent import glossario as modulo_glossario
from sunontent import levelspec, persistencia
from sunontent.adaptacao import complemento, gate, limpeza, planejador, prefiltro
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import FORMATOS, NIVEIS, CelulaState, celula_id


def adapt(state, config, *, rodadas: int) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados, raiz = configuravel["repositorio"], Path(configuravel["raiz_dados"]), configuravel["raiz"]
    run_id = state.run_id

    # LevelSpec ou glossário inválido: FalhaTerminal antes de chamar o modelo.
    specs = levelspec.carregar_todos(raiz)
    glossario = modulo_glossario.carregar(raiz)

    factsheet = state.factsheet
    claims = {c.id: c for c in factsheet.claims}
    filtro = prefiltro.filtrar(factsheet.claims, specs)
    if not filtro.candidatos_nos_3:
        raise FalhaTerminal("E4: nenhum claim é candidato nos 3 níveis; o núcleo não tem de onde sair")
    termos_novos = complemento.termos_sem_analogia(glossario, claims, filtro.candidatos["iniciante"])
    persistencia.reiniciar_validacoes_plano(repositorio, raiz_dados, run_id)

    def registrar_span(span):
        persistencia.gravar_span(repositorio, raiz_dados, span)

    saida, problemas = None, []
    for rodada in range(1, rodadas + 1):
        saida = planejador.planejar(
            configuravel["llm"], raiz, factsheet, specs, filtro, glossario, termos_novos,
            run_id=run_id, registrar_span=registrar_span, rodada=rodada,
            anterior=saida, problemas=[p.texto() for p in problemas],
        )
        plano = complemento.completar(saida, claims, filtro, glossario, rodadas=rodada)
        problemas = gate.conferir(plano, claims, filtro, specs, glossario)
        persistencia.gravar_validacao_plano(repositorio, raiz_dados, run_id, rodada, problemas, saida)
        if not problemas:
            break

    if problemas:
        plano = limpeza.limpar(plano, claims, filtro, specs, glossario, problemas)

    celulas = [CelulaState(celula_id=celula_id(n, f), nivel=n, formato=f) for n in NIVEIS for f in FORMATOS]
    persistencia.gravar_plano(repositorio, raiz_dados, run_id, plano, celulas)
    return {"rodada_plano": rodada, "plano": plano, "celulas": {c.celula_id: c for c in celulas}}
