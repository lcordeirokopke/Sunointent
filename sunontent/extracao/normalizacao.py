"""E1: normalização do texto das páginas.

Remove cabeçalho e rodapé repetidos e número de página, junta palavras hifenizadas quebradas por
linha e colapsa espaço. Depois, `sem_versao_em_ingles` descarta a versão em inglês do fim do documento. O texto limpo da página é a referência de todos os offsets do pipeline:
nada depois desta etapa altera esse texto.
"""

import math
import re
from collections import Counter

from sunontent.extracao.pdf import Linha, PaginaLida
from sunontent.schemas import Pagina

MARGEM = 0.12  # fração da altura da página, em cima e embaixo, onde mora cabeçalho e rodapé
MINIMO_PAGINAS_PARA_REPETICAO = 3

_SUBSTITUICOES = {
    0x00AD: "",  # hífen opcional
    0x200B: "",  # espaço de largura zero
    0x200C: "",
    0x200D: "",
    0xFEFF: "",
    0x00A0: " ",  # espaço sem quebra
    0x2007: " ",
    0x202F: " ",
    0xFB00: "ff",  # ligaduras
    0xFB01: "fi",
    0xFB02: "fl",
    0xFB03: "ffi",
    0xFB04: "ffl",
}
_TABELA = _SUBSTITUICOES
_ESPACOS = re.compile(r"\s+")
_DIGITOS = re.compile(r"\d+")
_NUMERO_DE_PAGINA = re.compile(
    r"^(?:(?:p[áa]g(?:ina|\.)?\s*)?\d{1,4}(?:\s*(?:de|/)\s*\d{1,4})?)$", re.IGNORECASE
)
_FIM_HIFENIZADO = re.compile(r"[^\W\d_]-$")

# Palavras curtas que só uma das duas línguas usa com frequência; a contagem decide a língua da página.
_PALAVRA = re.compile(r"[^\W\d_]+")
_PALAVRAS_PT = frozenset("de da do das dos que para com não uma pela pelo ao em na no e à".split())
_PALAVRAS_EN = frozenset("the of and to in for by with on is which shall will be this that at".split())
MINIMO_PALAVRAS_EN = 20


def limpar_linha(texto: str) -> str:
    return _ESPACOS.sub(" ", texto.translate(_TABELA)).strip()


def normalizar(paginas: list[PaginaLida]) -> list[Pagina]:
    repetidas = _linhas_repetidas(paginas)
    deslocamento = _deslocamento_da_numeracao(paginas)
    return [_normalizar_pagina(pagina, repetidas, deslocamento) for pagina in paginas]


def sem_versao_em_ingles(paginas: list[Pagina]) -> list[Pagina]:
    """Descarta o bloco final de páginas em inglês (a versão traduzida que alguns documentos trazem
    depois da original). Só o bloco final: as páginas que ficam seguem numeradas como no PDF. Página
    vazia não interrompe o bloco. Documento todo em inglês fica inteiro."""
    corte = len(paginas)
    for indice in range(len(paginas) - 1, -1, -1):
        if not paginas[indice].texto_limpo.strip():
            continue
        if not _em_ingles(paginas[indice]):
            break
        corte = indice
    return paginas[:corte] if corte > 0 else paginas


def _em_ingles(pagina: Pagina) -> bool:
    palavras = _PALAVRA.findall(pagina.texto_limpo.lower())
    ingles = sum(p in _PALAVRAS_EN for p in palavras)
    portugues = sum(p in _PALAVRAS_PT for p in palavras)
    return ingles >= MINIMO_PALAVRAS_EN and ingles > 2 * portugues


def _valor_de_pagina(texto: str) -> int | None:
    """O número impresso, se a linha é só um número de página ("3", "Página 3 de 9", "3/9")."""
    if not _NUMERO_DE_PAGINA.match(texto):
        return None
    return int(re.search(r"\d+", texto).group())


def _deslocamento_da_numeracao(paginas: list[PaginaLida]) -> int:
    """Diferença entre o número impresso e o índice da página (0 quando a numeração começa na capa).

    Vale o valor mais comum entre as linhas de número na margem, se aparece em duas páginas ou mais.
    """
    contagem: Counter[int] = Counter()
    for pagina in paginas:
        for linha in pagina.linhas:
            valor = _valor_de_pagina(limpar_linha(linha.texto))
            if valor is not None and _na_margem(linha, pagina.altura):
                contagem[valor - pagina.numero] += 1
    if contagem:
        valor, vezes = contagem.most_common(1)[0]
        if vezes >= 2:
            return valor
    return 0


def _na_margem(linha: Linha, altura: float) -> bool:
    return linha.y1 <= altura * MARGEM or linha.y0 >= altura * (1 - MARGEM)


def _chave(texto: str) -> str:
    """Chave de repetição: os dígitos são mascarados só em linha com letras ("Relatório 3"). Linha só de
    número é comparada pelo valor, porque o número de página tem regra própria."""
    texto = limpar_linha(texto)
    return (_DIGITOS.sub("#", texto) if any(c.isalpha() for c in texto) else texto).lower()


def _linhas_repetidas(paginas: list[PaginaLida]) -> set[str]:
    if len(paginas) < MINIMO_PAGINAS_PARA_REPETICAO:
        return set()
    contagem: Counter[str] = Counter()
    for pagina in paginas:
        contagem.update({_chave(l.texto) for l in pagina.linhas if _na_margem(l, pagina.altura)})
    minimo = math.ceil(len(paginas) / 2)
    return {chave for chave, n in contagem.items() if chave and n >= minimo}


def _normalizar_pagina(pagina: PaginaLida, repetidas: set[str], deslocamento: int) -> Pagina:
    linhas: list[str] = []
    for linha in pagina.linhas:
        texto = limpar_linha(linha.texto)
        if not texto:
            continue
        if _na_margem(linha, pagina.altura) and (
            _valor_de_pagina(texto) == pagina.numero + deslocamento or _chave(texto) in repetidas
        ):
            continue
        linhas.append(texto)
    return Pagina(pagina=pagina.numero, texto_limpo="\n".join(_juntar_hifenizadas(linhas)))


def _juntar_hifenizadas(linhas: list[str]) -> list[str]:
    """"pala-" no fim da linha e "vra" no começo da seguinte viram "palavra"."""
    saida: list[str] = []
    for linha in linhas:
        if saida and _FIM_HIFENIZADO.search(saida[-1]) and linha[0].islower():
            saida[-1] = saida[-1][:-1] + linha
        else:
            saida.append(linha)
    return saida
