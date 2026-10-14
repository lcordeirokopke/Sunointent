"""E4. Plano editorial: chama adaptacao/ sobre o FactSheet assinado e coloca o plano e as 9 células no estado.

Roda o pré-filtro, o planejador, o complemento e o gate em loop, até `rodadas` (graph.RODADAS_PLANO): a
rodada 1 é o plano e as seguintes corrigem só os problemas do gate. Grava cada rodada em validacoes_plano;
se o problema continua depois da última, retira os ids inexistentes e segue com `aviso`. Grava o plano e as
9 células em PENDENTE. Na retomada, se o plano do run já está gravado, reaproveita o plano e as células e
não chama o modelo (docs/fluxos/plano-editorial.md).
Dependências vêm de config["configurable"]: repositorio, raiz_dados, raiz (config/ e prompts/) e llm.
"""

from pathlib import Path

from sunontent import glossario as modulo_glossario
from sunontent import levelspec, persistencia
from sunontent.adaptacao import complemento, gate, planejador, prefiltro
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import FORMATOS, NIVEIS, CelulaState, celula_id


def adapt(state, config, *, rodadas: int) -> dict:
    configuravel = config["configurable"]
    repositorio, raiz_dados, raiz = configuravel["repositorio"], Path(configuravel["raiz_dados"]), configuravel["raiz"]
    run_id = state.run_id

    gravado = persistencia.carregar_plano(repositorio, raiz_dados, run_id)
    if gravado is not None:
        plano, celulas = gravado
        return {"rodada_plano": plano.rodadas, "plano": plano, "celulas": {c.celula_id: c for c in celulas}}

    # LevelSpec ou glossário inválido: FalhaTerminal antes de chamar o modelo.
    specs = levelspec.carregar_todos(raiz)
    glossario = modulo_glossario.carregar(raiz)

    factsheet = state.factsheet
    claims = {c.id: c for c in factsheet.claims}
    filtro = prefiltro.filtrar(factsheet.claims, specs)
    termos_novos = complemento.termos_sem_analogia(glossario, claims, filtro.candidatos["iniciante"])
    persistencia.reiniciar_validacoes_plano(repositorio, raiz_dados, run_id)

    def registrar_span(span):
        persistencia.gravar_span(repositorio, raiz_dados, span)

    saida, problemas = None, []
    for rodada in range(1, rodadas + 1):
        saida = planejador.planejar(
            configuravel["llm"], raiz, factsheet, specs, filtro, glossario, termos_novos,
            run_id=run_id, registrar_span=registrar_span,
            anterior=saida, problemas=[p.texto() for p in problemas],
        )
        plano = complemento.completar(saida, claims, filtro, glossario, rodadas=rodada)
        problemas = gate.conferir(plano, claims, filtro, specs, glossario)
        persistencia.gravar_validacao_plano(repositorio, raiz_dados, run_id, rodada, problemas)
        if not problemas:
            break

    if problemas:
        plano = complemento.retirar_ids_inexistentes(plano, claims)
        vazios = [p.nivel for p in plano.niveis if not p.claims]
        if not plano.nucleo or vazios:
            falta = "o núcleo ficou vazio" if not plano.nucleo else f"sem claim em {', '.join(vazios)}"
            raise FalhaTerminal(
                f"E4: depois de {rodada} rodadas e da retirada dos ids inexistentes, {falta}. "
                + "; ".join(p.texto() for p in problemas)
            )
        plano = plano.model_validate({**plano.model_dump(), "aviso": "; ".join(p.texto() for p in problemas)})

    celulas = [CelulaState(celula_id=celula_id(n, f), nivel=n, formato=f) for n in NIVEIS for f in FORMATOS]
    persistencia.gravar_plano(repositorio, raiz_dados, run_id, plano, celulas)
    return {"rodada_plano": rodada, "plano": plano, "celulas": {c.celula_id: c for c in celulas}}
