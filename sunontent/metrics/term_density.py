"""M4. Densidade de termos (docs/metricas.md). Determinístico, sem LLM.

Ocorrências de termos do glossário com `nivel_minimo` acima de iniciante no texto das unidades da peça (seção, slide
ou fala da cena), por 100 palavras, casadas pela mesma regra da M5 (docs/glossario.md, Casamento no texto). O
resultado traz também a densidade só dos termos de `nivel_minimo` avancado e a contagem por termo.

O nível com o bloco `densidade` no LevelSpec (o Iniciante) reprova acima do teto do formato, com piso de uma
ocorrência por unidade; os outros só medem. A violação aponta ocorrências a trocar por palavras simples: primeiro as
repetições, da última para a primeira, e só depois a primeira ocorrência de cada termo, que carrega a analogia da M5.
"""

import math

from sunontent.glossario import Glossario
from sunontent.levelspec import LevelSpec
from sunontent.metrics import campos, resultado
from sunontent.schemas import MetricaResultado, Peca, TermDensityDetalhes, TermoContado, Violacao
from sunontent.texto import contar_palavras

_VIOLACOES_MAX = 5


def _numero(valor: float) -> str:
    return f"{valor:g}".replace(".", ",")


def avaliar(peca: Peca, glossario: Glossario, spec: LevelSpec | None = None) -> MetricaResultado:
    palavras = unidades = 0
    contagem: dict[str, int] = {}
    achadas = []  # (unidade, inicio, fim, id, texto da unidade), na ordem de leitura
    for campo in campos.campos_da_peca(peca.payload):
        if campo.campo != "texto":
            continue
        unidades += 1
        palavras += contar_palavras(campo.texto)
        for o in glossario.casar(campo.texto):
            if glossario.termos[o.id].nivel_minimo != "iniciante":
                contagem[o.id] = contagem.get(o.id, 0) + 1
                achadas.append((campo.unidade_indice, o.inicio, o.fim, o.id, campo.texto))

    termos = [
        TermoContado(glossario_id=i, termo=glossario.termos[i].termo, nivel_minimo=glossario.termos[i].nivel_minimo, ocorrencias=n)
        for i, n in contagem.items()
    ]
    ocorrencias = sum(contagem.values())
    avancado = sum(t.ocorrencias for t in termos if t.nivel_minimo == "avancado")

    def por_cem(n: int) -> float:
        return round(100 * n / palavras, 1) if palavras else 0.0

    teto = getattr(spec.densidade.termos_max_por_100, peca.formato) if spec is not None and spec.densidade else None
    maximo = max(math.floor(teto * palavras / 100 + 1e-9), unidades) if teto is not None else None

    violacoes: list[Violacao] = []
    if maximo is not None and ocorrencias > maximo:
        unidade = campos.nome_da_unidade(peca.payload)
        em = "no" if unidade == "slide" else "na"
        vistos: set[str] = set()
        repeticoes, primeiras = [], []
        for achada in achadas:
            (primeiras if achada[3] not in vistos else repeticoes).append(achada)
            vistos.add(achada[3])
        excesso = ocorrencias - maximo
        escolhidas = [(a, True) for a in reversed(repeticoes)] + [(a, False) for a in reversed(primeiras)]
        for (indice, inicio, fim, termo_id, texto), repeticao in escolhidas[: min(excesso, _VIOLACOES_MAX)]:
            termo = glossario.termos[termo_id].termo
            onde = f"{em} {unidade} {indice + 1}"
            cabeca = (
                f"A densidade de termos da peça é {por_cem(ocorrencias):.1f} por 100 palavras e o máximo do nível é "
                f"{_numero(teto)} (corte pelo menos {excesso} ocorrências no total)"
            )
            if repeticao:
                corpo = f"troque «{texto[inicio:fim]}» {onde} por palavras simples: o termo já foi explicado antes."
            else:
                corpo = (
                    f"evite o termo «{texto[inicio:fim]}» {onde}: escreva a ideia em palavras simples, sem o termo "
                    "(e sem a analogia dele, que deixa de ser necessária)."
                )
            violacoes.append(Violacao(
                metrica="term_density", trecho=texto[inicio:fim],
                esperado=f"no máximo {maximo} ocorrências de termos ({_numero(teto)} por 100 palavras)",
                obtido=f"{ocorrencias} ocorrências ({por_cem(ocorrencias):.1f} por 100 palavras)",
                instrucao_corretiva=f"{cabeca}: {corpo} Termo: {termo}.",
                unidade_indice=indice, campo="texto", inicio=inicio, fim=fim,
            ))

    detalhes = TermDensityDetalhes(
        palavras=palavras, ocorrencias=ocorrencias, densidade=por_cem(ocorrencias), ocorrencias_avancado=avancado,
        densidade_avancado=por_cem(avancado), termos=termos, densidade_maxima=teto, ocorrencias_maximas=maximo,
    )
    faixa = f"no máximo {_numero(teto)} ocorrências por 100 palavras" if teto is not None else "sem limite (só mede)"
    return resultado.montar(detalhes, violacoes, faixa, detalhes.densidade)
