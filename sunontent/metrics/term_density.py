"""M4. Densidade de termos (docs/metricas.md). Determinístico, sem LLM; só mede, não reprova.

Ocorrências de termos do glossário com `nivel_minimo` acima de iniciante no texto das unidades da peça (seção, slide
ou fala da cena), por 100 palavras, casadas pela mesma regra da M5 (docs/glossario.md, Casamento no texto). O
resultado traz também a densidade só dos termos de `nivel_minimo` avancado e a contagem por termo.
"""

from sunontent.glossario import Glossario
from sunontent.metrics import campos, resultado
from sunontent.schemas import MetricaResultado, Peca, TermDensityDetalhes, TermoContado
from sunontent.texto import contar_palavras


def avaliar(peca: Peca, glossario: Glossario) -> MetricaResultado:
    palavras = 0
    contagem: dict[str, int] = {}
    for campo in campos.campos_da_peca(peca.payload):
        if campo.campo != "texto":
            continue
        palavras += contar_palavras(campo.texto)
        for o in glossario.casar(campo.texto):
            if glossario.termos[o.id].nivel_minimo != "iniciante":
                contagem[o.id] = contagem.get(o.id, 0) + 1

    termos = [
        TermoContado(glossario_id=i, termo=glossario.termos[i].termo, nivel_minimo=glossario.termos[i].nivel_minimo, ocorrencias=n)
        for i, n in contagem.items()
    ]
    ocorrencias = sum(contagem.values())
    avancado = sum(t.ocorrencias for t in termos if t.nivel_minimo == "avancado")

    def por_cem(n: int) -> float:
        return round(100 * n / palavras, 1) if palavras else 0.0

    detalhes = TermDensityDetalhes(
        palavras=palavras, ocorrencias=ocorrencias, densidade=por_cem(ocorrencias), ocorrencias_avancado=avancado,
        densidade_avancado=por_cem(avancado), termos=termos,
    )
    return resultado.montar(detalhes, [], "sem limite (só mede)", detalhes.densidade)
