"""E5.3 Montagem (docs/arquiteturas.md, E5.3): da saída do redator à `Peca`, por código.

1. Troca cada marcador válido (de um claim do plano do nível, com a ordem que existe) pelo trecho do texto do
   claim que ele cobriu na E5.1 e registra a posição em `numeros_usados`, com o campo (`texto` ou `titulo`). O
   marcador inválido fica no texto: a M1 o encontra e o aponta.
2. Calcula o `plan_contract`: quantidade de slides do carrossel, fim da última cena do roteiro até `duracao_s`,
   `claim_ids` de cada unidade dentro do plano do nível e cobertura do formato (todo claim do formato em alguma
   unidade). Cada falha é uma violação e consome uma tentativa do `K`.

Números escritos fora de marcador não são tocados: a M1 os aponta na E6. `restaurar` faz o caminho inverso, para
o retry: a peça anterior com os marcadores de volta no lugar dos números.
"""

from sunontent.levelspec import LevelSpec
from sunontent.metrics import resultado
from sunontent.metrics.campos import nome_da_unidade
from sunontent.schemas import (
    PADRAO_MARCADOR,
    ArtigoPayload,
    CarrosselPayload,
    ChecagemContrato,
    MetricaResultado,
    NumeroUsado,
    Payload,
    Peca,
    PlanContractDetalhes,
    RoteiroPayload,
    SaidaArtigo,
    SaidaCarrossel,
    SaidaRoteiro,
    Violacao,
)
from sunontent.sintese.pacote import Marcador, Pacote

Saida = SaidaArtigo | SaidaCarrossel | SaidaRoteiro


def trocar(
    texto: str, validos: dict[str, Marcador], campo: str, unidade_indice: int | None
) -> tuple[str, list[NumeroUsado]]:
    partes: list[str] = []
    usados: list[NumeroUsado] = []
    cursor = 0
    tamanho = 0
    for m in PADRAO_MARCADOR.finditer(texto):
        marcador = validos.get(m.group())
        if marcador is None:
            continue
        antes = texto[cursor:m.start()]
        partes += [antes, marcador.trecho]
        inicio = tamanho + len(antes)
        tamanho = inicio + len(marcador.trecho)
        usados.append(NumeroUsado(
            campo=campo, unidade_indice=unidade_indice, inicio=inicio, fim=tamanho,
            claim_id=marcador.claim_id, ordem=marcador.ordem,
        ))
        cursor = m.end()
    partes.append(texto[cursor:])
    return "".join(partes), usados


def _payload(saida: Saida, validos: dict[str, Marcador]) -> tuple[Payload, list[NumeroUsado]]:
    usados: list[NumeroUsado] = []

    def unidade(texto: str, indice: int) -> str:
        final, novos = trocar(texto, validos, "texto", indice)
        usados.extend(novos)
        return final

    if isinstance(saida, SaidaArtigo):
        titulo, do_titulo = trocar(saida.titulo, validos, "titulo", None)
        usados.extend(do_titulo)
        secoes = [{**s.model_dump(), "texto": unidade(s.texto, i)} for i, s in enumerate(saida.secoes)]
        return ArtigoPayload(titulo=titulo, linha_fina=saida.linha_fina, secoes=secoes), usados
    if isinstance(saida, SaidaCarrossel):
        slides = [{**s.model_dump(), "texto": unidade(s.texto, i)} for i, s in enumerate(saida.slides)]
        return CarrosselPayload(slides=slides), usados
    cenas = [{**c.model_dump(), "fala": unidade(c.fala, i)} for i, c in enumerate(saida.cenas)]
    return RoteiroPayload(cenas=cenas), usados


def _violacao(trecho: str, esperado: str, obtido: str, instrucao: str, unidade_indice: int | None = None) -> Violacao:
    return Violacao(
        metrica="plan_contract", trecho=trecho, esperado=esperado, obtido=obtido, instrucao_corretiva=instrucao,
        unidade_indice=unidade_indice,
    )


def plan_contract(payload: Payload, pacote: Pacote, spec: LevelSpec) -> MetricaResultado:
    checagens: list[ChecagemContrato] = []
    violacoes: list[Violacao] = []
    unidade = nome_da_unidade(payload)
    masculino = unidade == "slide"
    alguma, nenhuma, numa, da, dela = (
        ("algum", "nenhum", "num", "do", "dele") if masculino else ("alguma", "nenhuma", "numa", "da", "dela")
    )

    if isinstance(payload, CarrosselPayload):
        faixa = spec.formatos.carrossel.slides
        n = len(payload.slides)
        esperado = f"de {faixa.min} a {faixa.max} slides"
        checagens.append(ChecagemContrato(
            nome="quantidade_de_slides", esperado=esperado, obtido=f"{n} slides", passou=faixa.min <= n <= faixa.max,
        ))
        if n > faixa.max:
            violacoes.append(_violacao(
                f"{n} slides", esperado, f"{n} slides",
                f"Reduza o carrossel para no máximo {faixa.max} slides, contando o gancho e a conclusão: junte "
                "slides de corpo, sem tirar nenhum claim.",
            ))
        elif n < faixa.min:
            violacoes.append(_violacao(
                f"{n} slides", esperado, f"{n} slides",
                f"Aumente o carrossel para pelo menos {faixa.min} slides, contando o gancho e a conclusão: divida "
                "os claims em mais slides de corpo.",
            ))

    if isinstance(payload, RoteiroPayload):
        duracao = spec.formatos.roteiro.duracao_s
        fim = payload.cenas[-1].fim_s
        esperado = f"fim da última cena até {duracao} s"
        checagens.append(ChecagemContrato(
            nome="soma_das_cenas", esperado=esperado, obtido=f"{fim} s", passou=fim <= duracao,
        ))
        if fim > duracao:
            violacoes.append(_violacao(
                f"{fim} s", esperado, f"{fim} s",
                f"Encurte as cenas para o roteiro terminar em até {duracao} s, mantendo as cenas contíguas a partir "
                "de 0 s e a fala de cada cena dentro do tempo dela.",
                len(payload.cenas) - 1,
            ))

    plano = set(pacote.plano_claim_ids)
    fora = [(i, c) for i, ids in enumerate(payload.claim_ids_por_unidade()) for c in ids if c not in plano]
    checagens.append(ChecagemContrato(
        nome="claim_ids_no_plano", esperado="só claims do plano do nível",
        obtido="nenhum fora do plano" if not fora else ", ".join(dict.fromkeys(c for _, c in fora)) + " fora do plano",
        passou=not fora,
    ))
    for i, claim_id in fora:
        violacoes.append(_violacao(
            claim_id, "claim do plano do nível", claim_id,
            f"O claim {claim_id} não está no plano deste nível: tire-o de claim_ids {da} {unidade} {i} e escreva "
            "só com os claims fornecidos.",
            i,
        ))

    citados = set(payload.claim_ids())
    faltando = [c for c in pacote.claim_ids if c not in citados]
    checagens.append(ChecagemContrato(
        nome="cobertura_do_formato", esperado=f"os claims {', '.join(pacote.claim_ids)} em {alguma} {unidade}",
        obtido="todos presentes" if not faltando else ", ".join(faltando) + " ausentes", passou=not faltando,
    ))
    for claim_id in faltando:
        violacoes.append(_violacao(
            claim_id, f"o claim em {alguma} {unidade}", "ausente",
            f"O claim {claim_id} faz parte deste formato e não aparece em {nenhuma} {unidade}: "
            f"escreva o fato dele {numa} {unidade} e coloque {claim_id} em claim_ids {dela}.",
        ))

    return resultado.montar(PlanContractDetalhes(checagens=checagens), violacoes)


def montar(saida: Saida, pacote: Pacote, spec: LevelSpec, tentativa: int) -> tuple[Peca, MetricaResultado]:
    payload, usados = _payload(saida, pacote.marcadores)
    peca = Peca(
        celula_id=pacote.celula_id, nivel=pacote.nivel, formato=pacote.formato, payload=payload,
        claim_ids=payload.claim_ids(), numeros_usados=usados, tentativa=tentativa,
    )
    return peca, plan_contract(payload, pacote, spec)


def _devolver(texto: str, usados: list[NumeroUsado]) -> str:
    for n in sorted(usados, key=lambda n: n.inicio, reverse=True):
        texto = f"{texto[:n.inicio]}{{{{{n.claim_id}.{n.ordem}}}}}{texto[n.fim:]}"
    return texto


def restaurar(peca: Peca) -> Saida:
    """A saída do redator que deu esta peça: os números de `numeros_usados` voltam a ser marcadores; o marcador
    inválido já estava no texto."""

    def de(campo: str, indice: int | None) -> list[NumeroUsado]:
        return [n for n in peca.numeros_usados if n.campo == campo and n.unidade_indice == indice]

    p = peca.payload
    if isinstance(p, ArtigoPayload):
        return SaidaArtigo(
            titulo=_devolver(p.titulo, de("titulo", None)), linha_fina=p.linha_fina,
            secoes=[{**s.model_dump(), "texto": _devolver(s.texto, de("texto", i))} for i, s in enumerate(p.secoes)],
        )
    if isinstance(p, CarrosselPayload):
        return SaidaCarrossel(
            slides=[{**s.model_dump(), "texto": _devolver(s.texto, de("texto", i))} for i, s in enumerate(p.slides)],
        )
    return SaidaRoteiro(
        cenas=[{**c.model_dump(), "fala": _devolver(c.fala, de("texto", i))} for i, c in enumerate(p.cenas)],
    )
