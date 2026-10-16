"""M3. Legibilidade (docs/metricas.md). Determinístico, sem LLM.

Índice de Facilidade de Leitura de Flesch adaptado ao português (Martins et al., 1996), sobre o texto das unidades da
peça (seção, slide ou fala da cena; o título, a linha fina e o gancho visual ficam de fora):

    IFL = 248,835 - 1,015 x (palavras / frases) - 84,6 x (sílabas / palavras)

Palavra, sílaba e frase são as de `sunontent/texto.py`. O nível que tem piso de IFL no formato (`legibilidade` do
LevelSpec) reprova abaixo dele; os outros só medem. O resultado também diz se a peça tem o estilo de Iniciante (IFL
no piso do Iniciante e um marcador de analogia da M5 em alguma unidade), para a matriz de confusão estatística: o IFL
sozinho confunde frase curta com texto de Iniciante.
"""

from sunontent.levelspec import LevelSpec
from sunontent.metrics import analogy_rule, campos, resultado
from sunontent.schemas import MetricaResultado, Peca, ReadabilityDetalhes, Violacao
from sunontent.texto import contar_silabas, palavras_de_leitura, segmentar_frases

_FRASES_POR_VIOLACAO = 3


def ifl(palavras: int, silabas: int, frases: int) -> float:
    if palavras == 0 or frases == 0:
        return 0.0
    return 248.835 - 1.015 * (palavras / frases) - 84.6 * (silabas / palavras)


def piso(spec: LevelSpec | None, formato: str) -> float | None:
    """O piso de IFL do nível neste formato, ou None quando o nível só mede."""
    if spec is None or spec.legibilidade is None:
        return None
    return getattr(spec.legibilidade.ifl_minimo, formato)


def avaliar(peca: Peca, spec: LevelSpec, spec_iniciante: LevelSpec | None = None) -> MetricaResultado:
    unidade = campos.nome_da_unidade(peca.payload)
    em = "no" if unidade == "slide" else "na"
    frases_medidas = []  # (palavras, unidade, inicio, fim, texto da unidade)
    palavras = silabas = 0
    marcadores = spec_iniciante.analogia.marcadores if spec_iniciante and spec_iniciante.analogia else []
    com_analogia = False
    for campo in campos.campos_da_peca(peca.payload):
        if campo.campo != "texto":
            continue
        com_analogia = com_analogia or (bool(marcadores) and analogy_rule.tem_marcador(campo.texto, marcadores))
        for inicio, fim in segmentar_frases(campo.texto):
            lidas = palavras_de_leitura(campo.texto[inicio:fim])
            if not lidas:
                continue
            palavras += len(lidas)
            silabas += sum(contar_silabas(p) for p in lidas)
            frases_medidas.append((len(lidas), campo.unidade_indice, inicio, fim, campo.texto))

    valor = round(ifl(palavras, silabas, len(frases_medidas)), 1)
    minimo = piso(spec, peca.formato)
    meta = spec.legibilidade.frase_max_palavras if spec.legibilidade else None
    piso_iniciante = piso(spec_iniciante, peca.formato)
    estatistico = None
    if piso_iniciante is not None and marcadores:
        estatistico = "iniciante" if valor >= piso_iniciante and com_analogia else "acima_de_iniciante"

    violacoes: list[Violacao] = []
    if minimo is not None and valor < minimo:
        longas = sorted(frases_medidas, key=lambda f: -f[0])
        escolhidas = [f for f in longas if meta is None or f[0] > meta][:_FRASES_POR_VIOLACAO] or longas[:1]
        limite = f" de até {meta} palavras" if meta else ""
        for n, indice, inicio, fim, texto in escolhidas:
            onde = f"{em} {unidade} {indice + 1}"
            violacoes.append(Violacao(
                metrica="readability", trecho=texto[inicio:fim], esperado=f"IFL de pelo menos {minimo:g}",
                obtido=f"IFL {valor:.1f}",
                instrucao_corretiva=(
                    f"O índice de legibilidade da peça é {valor:.1f} e o mínimo do nível é {minimo:g}: reescreva a "
                    f"frase {onde} ({n} palavras) em frases mais curtas{limite} e troque as palavras longas por "
                    "equivalentes simples, sem mudar os números nem os claims."
                ),
                unidade_indice=indice, campo="texto", inicio=inicio, fim=fim,
            ))

    detalhes = ReadabilityDetalhes(
        palavras=palavras, silabas=silabas, frases=len(frases_medidas), ifl=valor, ifl_minimo=minimo,
        frase_max_palavras=meta, analogia_na_peca=com_analogia, nivel_estatistico=estatistico,
    )
    faixa = f"IFL de pelo menos {minimo:g}" if minimo is not None else "sem limite (só mede)"
    return resultado.montar(detalhes, violacoes, faixa, valor)
