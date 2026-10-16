"""M6. Juiz LLM (`llm_judge`, docs/metricas.md, M6): a única métrica não determinística, e ela só reprova.

Uma chamada por tentativa da célula, feita pela E6 só depois de M1, M2, M3, M5 e M7 (`plan_contract`) passarem. O prompt
(prompts/judge.j2) traz os claims citados (texto e `literal_fonte`), os trechos do documento da recuperação de
contexto (`contexto.py`), cada unidade da peça, para as analogias do plano usadas na peça a `definicao_tecnica` do
glossário e, para o nível percebido, o foco de cada nível (LevelSpec) e os termos do glossário encontrados na peça,
com o `nivel_minimo` de cada um. O nível pedido não vai para o prompt, para não influenciar o `nivel_percebido`.

A resposta precisa ter uma entrada para cada unidade e para cada analogia pedida; resposta incompleta é uma
`FalhaTerminal`, que a E6 transforma em ERRO_INFRA. Cada `false` vira uma `Violacao` da `llm_judge`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import jinja2

from sunontent import levelspec
from sunontent.glossario import Glossario, dobrar
from sunontent.llm import ClienteLLM
from sunontent.metrics import analogy_rule, campos, contexto, resultado
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import (
    DESCRICAO_TIPO,
    ArtigoPayload,
    CarrosselPayload,
    Chunk,
    FactSheet,
    LlmJudgeDetalhes,
    MetricaResultado,
    Peca,
    PlanoNivel,
    RoteiroPayload,
    SaidaJuiz,
    Span,
    VeredictoUnidade,
    Violacao,
)

NO = "judge"
RACIOCINIO = "judge"  # chave de `raciocinio` em config/models.yaml
_DESCRICAO_FORMATO = {
    "artigo": "um artigo analítico para web ou newsletter",
    "carrossel": "um carrossel para rede social",
    "roteiro": "o roteiro de um vídeo curto, falado em voz alta",
}
_DESCRICAO_UNIDADE = {
    "artigo": "uma seção do artigo",
    "carrossel": "um slide do carrossel",
    "roteiro": "uma cena do roteiro, com a fala e o que aparece na tela",
}


@dataclass(frozen=True)
class AnalogiaJulgada:
    """Analogia do plano usada na peça: o termo, a explicação do plano, a definição técnica do glossário e a
    primeira ocorrência do termo no texto das unidades."""

    termo: str
    explicacao: str
    definicao_tecnica: str
    unidade_indice: int
    inicio: int
    fim: int
    trecho: str  # o termo como está escrito na unidade


def analogias_usadas(peca: Peca, plano: PlanoNivel, glossario: Glossario) -> list[AnalogiaJulgada]:
    """As analogias do plano cujo termo aparece no texto das unidades, na ordem da primeira ocorrência."""
    do_plano = analogy_rule.analogias_do_plano(plano, glossario)
    achadas: dict[str, AnalogiaJulgada] = {}
    for campo in campos.campos_da_peca(peca.payload):
        if campo.campo != "texto":
            continue
        for o in glossario.casar(campo.texto):
            if o.id in do_plano and o.id not in achadas:
                analogia = do_plano[o.id]
                achadas[o.id] = AnalogiaJulgada(
                    termo=analogia.termo, explicacao=analogia.explicacao,
                    definicao_tecnica=glossario.termos[o.id].definicao_tecnica,
                    unidade_indice=campo.unidade_indice, inicio=o.inicio, fim=o.fim,
                    trecho=campo.texto[o.inicio:o.fim],
                )
    return list(achadas.values())


def termos_da_peca(peca: Peca, glossario: Glossario) -> list[tuple[str, str]]:
    """(termo, nivel_minimo) de cada termo do glossário encontrado em algum campo da peça, na ordem da primeira
    ocorrência."""
    achados: dict[str, None] = {}
    for campo in campos.campos_da_peca(peca.payload):
        for o in glossario.casar(campo.texto):
            achados.setdefault(o.id, None)
    return [(glossario.termos[i].termo, glossario.termos[i].nivel_minimo) for i in achados]


def _papeis(peca: Peca) -> list[str | None]:
    if isinstance(peca.payload, CarrosselPayload):
        return [s.papel for s in peca.payload.slides]
    if isinstance(peca.payload, RoteiroPayload):
        ultima = len(peca.payload.cenas) - 1
        return ["abertura" if i == 0 else "fechamento" if i == ultima else None for i in range(ultima + 1)]
    return [None] * len(peca.payload.secoes)


def montar_prompt(
    raiz: Path,
    peca: Peca,
    factsheet: FactSheet,
    ctx: contexto.ContextoJuiz,
    analogias: list[AnalogiaJulgada],
    glossario: Glossario,
) -> str:
    ambiente = jinja2.Environment(
        loader=jinja2.FileSystemLoader(Path(raiz) / "prompts"),
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    por_id = {c.id: c for c in factsheet.claims}
    citados = list(dict.fromkeys(cid for u in ctx.unidades for cid in u.claim_ids))
    artigo = peca.payload if isinstance(peca.payload, ArtigoPayload) else None
    papeis = _papeis(peca)
    return ambiente.get_template("judge.j2").render(
        tipo_documento_descricao=DESCRICAO_TIPO[factsheet.tipo_documento],
        emissor=factsheet.emissor,
        data_documento=factsheet.data_documento.isoformat(),
        formato_descricao=_DESCRICAO_FORMATO[peca.formato],
        unidade_descricao=_DESCRICAO_UNIDADE[peca.formato],
        total_unidades=len(ctx.unidades),
        claims=[
            {"id": cid, "tipo": por_id[cid].tipo, "texto": " ".join(por_id[cid].texto.split()),
             "literal_fonte": " ".join(por_id[cid].literal_fonte.split())}
            for cid in citados
        ],
        trechos=[{"chunk_id": c.chunk_id, "secao": c.secao, "texto": c.texto.strip()} for c in ctx.chunks],
        titulo=artigo.titulo if artigo else "",
        linha_fina=artigo.linha_fina if artigo else "",
        unidades=[
            {"indice": u.unidade_indice, "papel": papeis[u.unidade_indice], "claim_ids": u.claim_ids,
             "cita_claims": u.cita_claims, "trechos": u.chunks_ancora + u.chunks_lexicos, "texto": u.texto}
            for u in ctx.unidades
        ],
        analogias=[a.__dict__ for a in analogias],
        foco={nivel: spec.foco for nivel, spec in levelspec.carregar_todos(raiz).items()},
        termos=[{"termo": termo, "nivel": nivel} for termo, nivel in termos_da_peca(peca, glossario)],
    )


def conferir_completude(saida: SaidaJuiz, total_unidades: int, analogias: list[AnalogiaJulgada]) -> None:
    """Uma resposta para cada unidade (0 a total - 1) e para cada analogia pedida, sem repetição nem sobra."""
    problemas = []
    indices = [u.unidade_indice for u in saida.unidades]
    faltando = sorted(set(range(total_unidades)) - set(indices))
    sobrando = sorted({i for i in indices if i >= total_unidades or indices.count(i) > 1})
    if faltando:
        problemas.append(f"unidades sem resposta: {', '.join(map(str, faltando))}")
    if sobrando:
        problemas.append(f"unidades repetidas ou inexistentes: {', '.join(map(str, sobrando))}")
    pedidos = [dobrar(a.termo).strip() for a in analogias]
    respondidos = [dobrar(a.termo).strip() for a in saida.analogias]
    sem_resposta = [a.termo for a, p in zip(analogias, pedidos) if p not in respondidos]
    extras = sorted({a.termo for a, r in zip(saida.analogias, respondidos) if r not in pedidos or respondidos.count(r) > 1})
    if sem_resposta:
        problemas.append(f"analogias sem resposta: {', '.join(sem_resposta)}")
    if extras:
        problemas.append(f"analogias repetidas ou não pedidas: {', '.join(extras)}")
    if problemas:
        raise FalhaTerminal(f"{NO}: resposta incompleta ({'; '.join(problemas)})")


def _violacao_da_unidade(peca: Peca, veredito: VeredictoUnidade, claim_ids: list[str]) -> Violacao:
    indice = veredito.unidade_indice
    unidade = campos.nome_da_unidade(peca.payload)
    em = "no" if unidade == "slide" else "na"
    textos = [(c.campo, c.texto) for c in campos.campos_da_peca(peca.payload) if c.unidade_indice == indice]
    trecho = (veredito.trecho_problematico or "").strip()
    posicao = {}
    for campo, texto in textos:
        if trecho and trecho in texto:
            inicio = texto.index(trecho)
            posicao = {"campo": campo, "inicio": inicio, "fim": inicio + len(trecho)}
            break
    if not posicao:
        campo, texto = textos[0]
        trecho, posicao = (trecho or texto), {"campo": campo}
    return Violacao(
        metrica="llm_judge", trecho=trecho,
        esperado=f"tudo o que {em} {unidade} afirma é implicado pelos claims {', '.join(claim_ids)}",
        obtido=f"não implicado: {veredito.justificativa}",
        instrucao_corretiva=(
            f"Reescreva {em} {unidade} {indice + 1} dizendo só o que os claims {', '.join(claim_ids)} afirmam, sem "
            "criar causa, consequência ou fato que eles não dizem, e com a atribuição à fonte nas projeções, "
            f"condicionantes e riscos. Problema apontado: {veredito.justificativa}"
        ),
        unidade_indice=indice, **posicao,
    )


def _violacao_da_analogia(peca: Peca, analogia: AnalogiaJulgada, justificativa: str) -> Violacao:
    unidade = campos.nome_da_unidade(peca.payload)
    em = "no" if unidade == "slide" else "na"
    return Violacao(
        metrica="llm_judge", trecho=analogia.trecho,
        esperado=f"analogia que explica corretamente «{analogia.termo}»",
        obtido=f"analogia incorreta: {justificativa}",
        instrucao_corretiva=(
            f"A analogia de «{analogia.trecho}» {em} {unidade} {analogia.unidade_indice + 1} explica o conceito de "
            f"forma errada ({justificativa}). Reescreva-a de modo que respeite o conceito: {analogia.definicao_tecnica}"
        ),
        unidade_indice=analogia.unidade_indice, campo="texto", inicio=analogia.inicio, fim=analogia.fim,
    )


def julgar(
    llm: ClienteLLM,
    raiz: Path,
    peca: Peca,
    plano: PlanoNivel,
    factsheet: FactSheet,
    chunks: list[Chunk],
    glossario: Glossario,
    *,
    run_id: str,
    registrar_span: Callable[[Span], None],
) -> MetricaResultado:
    ctx = contexto.montar(peca, {c.id: c for c in factsheet.claims}, chunks)
    analogias = analogias_usadas(peca, plano, glossario)
    saida = llm.chamar(
        NO, montar_prompt(raiz, peca, factsheet, ctx, analogias, glossario), SaidaJuiz,
        run_id=run_id, registrar_span=registrar_span, nivel=peca.nivel, celula_id=peca.celula_id,
        tentativa=peca.tentativa, raciocinio=RACIOCINIO,
    )
    conferir_completude(saida, len(ctx.unidades), analogias)

    por_termo = {dobrar(a.termo).strip(): a for a in saida.analogias}
    vereditos_analogia = [por_termo[dobrar(a.termo).strip()] for a in analogias]
    unidades = sorted(saida.unidades, key=lambda u: u.unidade_indice)
    violacoes = [
        _violacao_da_analogia(peca, a, v.justificativa) for a, v in zip(analogias, vereditos_analogia) if not v.correta
    ]
    violacoes += [
        _violacao_da_unidade(peca, u, ctx.unidades[u.unidade_indice].claim_ids) for u in unidades if not u.implicada
    ]
    violacoes.sort(key=lambda v: v.unidade_indice)
    detalhes = LlmJudgeDetalhes(
        analogias=vereditos_analogia, unidades=unidades, justificativa_nivel=saida.justificativa_nivel,
        nivel_percebido=saida.nivel_percebido,
    )
    return resultado.montar(detalhes, violacoes)


def criar_juiz(llm: ClienteLLM, raiz: Path) -> Callable[..., MetricaResultado]:
    """O juiz que a E6 recebe em config["configurable"]["juiz"]."""

    def juiz(peca, plano, factsheet, chunks, glossario, *, run_id, registrar_span):
        return julgar(llm, raiz, peca, plano, factsheet, chunks, glossario, run_id=run_id, registrar_span=registrar_span)

    return juiz
