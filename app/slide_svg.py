"""Slide do carrossel em SVG, gerado na hora pelo servidor do dashboard (docs/fluxos/dashboard.md, Slide em SVG).

O pipeline entrega o carrossel como dado (`SaidaCarrossel`); este módulo só desenha. O texto do slide é o
`Slide.texto` final: o código insere quebras de linha entre palavras e não corta, resume nem troca nada. SVG não
quebra linha sozinho, então cada linha vira um `<tspan>` e a largura é medida com o mesmo arquivo `.ttf` que a
página carrega por `@font-face`.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from PIL import ImageFont

FONTES = Path(__file__).resolve().parent / "static" / "fontes"
LARGURA = 1080
ALTURA = 1350
FAMILIA = "Inter, 'Helvetica Neue', Arial, sans-serif"
NAMESPACE = "https://sunontent.local/ns"


@dataclass(frozen=True)
class Caixa:
    """Caixa de texto de um papel do slide, em pixels do SVG (1080 x 1350)."""

    x: int
    y: int
    largura: int
    altura: int
    tamanho: int  # tamanho da fonte
    entrelinha: float  # altura da linha, em múltiplos do tamanho
    peso: str  # "regular" ou "bold": escolhe o arquivo da fonte
    cor: str
    fundo: str


CAIXAS: dict[str, Caixa] = {
    "gancho": Caixa(96, 330, 888, 690, 76, 1.2, "bold", "#FFFFFF", "#0F2A4A"),
    "corpo": Caixa(96, 240, 888, 870, 52, 1.4, "regular", "#14213D", "#F7F5EF"),
    "conclusao": Caixa(96, 330, 888, 690, 64, 1.25, "bold", "#FFFFFF", "#0F2A4A"),
}
ARQUIVO_DO_PESO = {"regular": "Inter-Regular.ttf", "bold": "Inter-Bold.ttf"}


@lru_cache(maxsize=None)
def _fonte(peso: str, tamanho: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTES / ARQUIVO_DO_PESO[peso]), tamanho)


def quebrar_linhas(texto: str, caixa: Caixa) -> list[str]:
    """Divide o texto em linhas que cabem na largura da caixa, medindo cada linha com o `.ttf` do template.

    Só troca o espaço entre duas palavras por uma quebra: juntar as linhas com um espaço devolve as mesmas
    palavras, na mesma ordem (sequências de espaço e quebras de linha do texto valem um espaço só). Uma palavra
    maior que a caixa fica sozinha na linha e passa da largura.
    """
    fonte = _fonte(caixa.peso, caixa.tamanho)
    linhas: list[str] = []
    atual = ""
    for palavra in texto.split():
        candidata = f"{atual} {palavra}" if atual else palavra
        if atual and fonte.getlength(candidata) > caixa.largura:
            linhas.append(atual)
            atual = palavra
        else:
            atual = candidata
    if atual:
        linhas.append(atual)
    return linhas


def largura_da_linha(linha: str, caixa: Caixa) -> float:
    return _fonte(caixa.peso, caixa.tamanho).getlength(linha)


@dataclass(frozen=True)
class SlideSvg:
    svg: str
    linhas: list[str]
    estouro_altura: bool  # o texto não cabe na altura da caixa do papel


def montar_slide(
    *, texto: str, papel: str, claim_ids: list[str], celula_id: str, tentativa: int, posicao: int, total: int
) -> SlideSvg:
    """SVG do slide `posicao` (1-indexada) de `total`. KeyError se o papel não tem caixa."""
    caixa = CAIXAS[papel]
    linhas = quebrar_linhas(texto, caixa)
    passo = caixa.tamanho * caixa.entrelinha
    estouro = len(linhas) * passo > caixa.altura
    ascendente = _fonte(caixa.peso, caixa.tamanho).getmetrics()[0]
    primeira = caixa.y + (passo - caixa.tamanho) / 2 + ascendente
    tspans = "".join(
        f'<tspan x="{caixa.x}" y="{primeira + i * passo:.1f}">{escape(linha)}</tspan>' for i, linha in enumerate(linhas)
    )
    peso_css = "700" if caixa.peso == "bold" else "400"
    metadados = (
        f"<sunontent:slide xmlns:sunontent={quoteattr(NAMESPACE)} celula_id={quoteattr(celula_id)}"
        f' tentativa="{tentativa}" posicao="{posicao}" total="{total}" papel={quoteattr(papel)}'
        f' claim_ids={quoteattr(" ".join(claim_ids))} estouro_altura="{str(estouro).lower()}"/>'
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{LARGURA}" height="{ALTURA}"'
        f' viewBox="0 0 {LARGURA} {ALTURA}" role="img" aria-label={quoteattr(f"Slide {posicao} de {total}")}>'
        f"<title>{escape(celula_id)} · tentativa {tentativa} · slide {posicao}</title>"
        f"<metadata>{metadados}</metadata>"
        f'<rect id="fundo" width="{LARGURA}" height="{ALTURA}" fill="{caixa.fundo}"/>'
        f'<text id="texto" font-family="{FAMILIA}" font-size="{caixa.tamanho}" font-weight="{peso_css}"'
        f' fill="{caixa.cor}" xml:space="preserve">{tspans}</text>'
        f'<text id="posicao" x="{LARGURA - 96}" y="{ALTURA - 96}" text-anchor="end" font-family="{FAMILIA}"'
        f' font-size="32" font-weight="400" fill="{caixa.cor}" fill-opacity="0.6">{posicao} / {total}</text>'
        "</svg>"
    )
    return SlideSvg(svg, linhas, estouro)


def nome_do_arquivo(celula_id: str, tentativa: int, posicao: int) -> str:
    return f"{celula_id}_t{tentativa}_slide{posicao}.svg"
