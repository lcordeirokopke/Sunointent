"""M2. Format Contract Check (docs/arquiteturas.md, E6). Determinístico, sem LLM.

Aritmética sobre o payload, com o contrato de formato do LevelSpec e a contagem de `sunontent/texto.py`:
- artigo: palavras do texto das seções (o título e a linha fina não contam) na faixa do nível;
- carrossel: caracteres de cada slide até o limite do papel;
- roteiro: palavras de cada cena até a duração da cena vezes `palavras_por_segundo`, e o total até
  `duracao_s` vezes `palavras_por_segundo`.
O valor é o número de unidades fora do limite. A quantidade de slides e a soma das cenas são do `plan_contract`.
"""

import math

from sunontent.levelspec import LevelSpec, instrucoes_redator
from sunontent.metrics import resultado
from sunontent.schemas import (
    ArtigoPayload,
    CarrosselPayload,
    MedidaUnidade,
    MetricaResultado,
    Peca,
    TextSizeDetalhes,
    Violacao,
)
from sunontent.texto import contar_caracteres, contar_palavras


def _violacao(trecho, esperado, obtido, instrucao, unidade_indice=None, campo=None, inicio=None, fim=None) -> Violacao:
    return Violacao(
        metrica="text_size", trecho=trecho, esperado=esperado, obtido=obtido, instrucao_corretiva=instrucao,
        unidade_indice=unidade_indice, campo=campo, inicio=inicio, fim=fim,
    )


def avaliar(peca: Peca, spec: LevelSpec) -> MetricaResultado:
    limite = instrucoes_redator(spec, peca.formato).faixa_esperada
    payload = peca.payload
    medidas: list[MedidaUnidade] = []
    violacoes: list[Violacao] = []

    if isinstance(payload, ArtigoPayload):
        grandeza = "palavras"
        faixa = spec.formatos.artigo.palavras
        total = sum(contar_palavras(t) for t in payload.textos())
        dentro = faixa.min <= total <= faixa.max
        medidas.append(MedidaUnidade(medida=total, limite_min=faixa.min, limite_max=faixa.max, dentro=dentro))
        if not dentro:
            acao = (
                f"Acrescente texto às seções: de {total} para pelo menos {faixa.min} palavras"
                if total < faixa.min
                else f"Reduza o texto das seções: de {total} para no máximo {faixa.max} palavras"
            )
            violacoes.append(_violacao(
                f"{total} palavras no texto das seções", f"de {faixa.min} a {faixa.max} palavras", f"{total} palavras",
                f"{acao}, sem mudar os números nem os claims.",
            ))
    elif isinstance(payload, CarrosselPayload):
        grandeza = "caracteres"
        limites = spec.formatos.carrossel.caracteres
        for i, slide in enumerate(payload.slides):
            maximo = getattr(limites, slide.papel)
            n = contar_caracteres(slide.texto)
            medidas.append(MedidaUnidade(unidade_indice=i, papel=slide.papel, medida=n, limite_max=maximo, dentro=n <= maximo))
            if n > maximo:
                violacoes.append(_violacao(
                    slide.texto, f"até {maximo} caracteres no slide de {slide.papel}", f"{n} caracteres",
                    f"Reduza o slide {i + 1} ({slide.papel}) de {n} para no máximo {maximo} caracteres, sem mudar os números.",
                    i, "texto", 0, len(slide.texto),
                ))
    else:
        grandeza = "palavras"
        roteiro = spec.formatos.roteiro
        for i, cena in enumerate(payload.cenas):
            duracao = cena.fim_s - cena.inicio_s
            maximo = math.floor(duracao * roteiro.palavras_por_segundo)
            n = contar_palavras(cena.fala)
            medidas.append(MedidaUnidade(unidade_indice=i, medida=n, limite_max=maximo, dentro=n <= maximo))
            if n > maximo:
                violacoes.append(_violacao(
                    cena.fala, f"até {maximo} palavras em {duracao} s", f"{n} palavras",
                    f"Reduza a fala da cena {i + 1} de {n} para no máximo {maximo} palavras, que cabem em {duracao} s "
                    "ao falar.",
                    i, "texto", 0, len(cena.fala),
                ))
        total = sum(m.medida for m in medidas)
        maximo_total = math.floor(roteiro.duracao_s * roteiro.palavras_por_segundo)
        medidas.append(MedidaUnidade(medida=total, limite_max=maximo_total, dentro=total <= maximo_total))
        if total > maximo_total:
            violacoes.append(_violacao(
                f"{total} palavras no roteiro", f"até {maximo_total} palavras em {roteiro.duracao_s} s", f"{total} palavras",
                f"Reduza o roteiro de {total} para no máximo {maximo_total} palavras no total, cortando as falas mais longas.",
            ))

    detalhes = TextSizeDetalhes(grandeza=grandeza, limite=limite, unidades=medidas)
    return resultado.montar(detalhes, violacoes, f"0 unidades fora do limite ({limite})")
