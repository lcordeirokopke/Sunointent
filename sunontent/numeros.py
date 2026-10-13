"""Parser de número no formato brasileiro, usado pela E1 (tabela de números do fonte) e pelas métricas.

Regras:
- ponto separa milhar e vírgula separa decimal: "1.234,56" é 1234.56. Tudo em Decimal, nunca float.
- formato que não segue isso ("1,234.56", "12.5", "1.93", "3.1") não é adivinhado: levanta
  NumeroNaoPrevisto. Interpretar errado aqui vira falso positivo de fidelidade numérica na E6.
- o `bruto` é o trecho exato do texto, com a unidade ("12,5%", "R$ 1,2 bi", "0,50 p.p.").
- unidades: pct, pp, BRL, BRL_mi, x, contagem, data. "bi", "milhões" e "mil" ao lado de R$
  normalizam para milhões (BRL_mi); R$ sem escala fica BRL; escala sem R$ vira contagem em unidades.
  bps e "pontos-base" viram pp (1 bp = 0,01 p.p.).
- data: cada componente numérico da data é um número (valor = o inteiro escrito): "15 e 16 de
  setembro de 2026" dá 15, 16 e 2026; "2T26" dá 2 e 26; "30/06/2026" dá 30, 6 e 2026.
- cotação "R$5,15/US$" é um BRL com o bruto inteiro; outras quantias em dólar ou euro levantam NumeroNaoPrevisto.
- `datas` lê as datas com dia como dia, mês e ano (quando há), para comparar datas escritas de formas
  diferentes ("19/10" e "19 de outubro"); `varrer` continua dando um número por componente.
- identificadores (CNPJ, CPF, CEP, NIRE, telefone, hora, endereço web), numeração de parágrafo no
  início da linha ("12. ") e números colados a letra ("Copom1", "B3", "5G") não são números do
  texto e são ignorados.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from sunontent.schemas import Unidade


class NumeroNaoPrevisto(ValueError):
    """Trecho numérico em formato que o parser não interpreta."""


@dataclass(frozen=True)
class Encontrado:
    valor: Decimal
    unidade: Unidade
    bruto: str
    inicio: int
    fim: int


_MESES = (
    "janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
)
_LETRA = "A-Za-zÀ-ÿ"
_NUM = r"(?:[1-9]\d{0,2}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?)"
_SINAL = r"(?:(?<![\w.,])[-−](?=\d))?"
_ESCALA = r"(?:trilh(?:ões|ão)|tri|bilh(?:ões|ão)|bi|milh(?:ões|ão)|mi|mil)"
_FRONTEIRA_ESQ = r"(?<![\w.,])"
_FRONTEIRA_DIR = rf"(?![\w{_LETRA}]|[.,]\d)"

_PADRAO = re.compile(
    rf"""
    (?P<ignorar>
        (?:https?://|www\.)\S+
      | \b(?:[A-Za-z0-9-]+\.)+(?:com|org|net|gov|edu|io)(?:\.[a-z]{{2}})?\b(?:/\S*)?
      | \d{{2}}\.\d{{3}}\.\d{{3}}/\d{{4}}-\d{{2}}
      | \d{{3}}\.\d{{3}}\.\d{{3}}-\d{{2}}
      | \d{{2}}\.\d\.\d{{7}}-\d
      | \d{{2}}\.\d{{3}}-\d{{3}}
      | \b\d{{5}}-\d{{3}}\b
      | \+?\d{{1,3}}\s?\(\d{{2}}\)\s?\d{{4,5}}-\d{{4}}
      | (?<![\w.,])\d{{1,2}}(?:h(?:\d{{2}})?|:\d{{2}}h?)\b
      | (?<![^\n])\d{{1,3}}\.(?=\s)
    )
  | (?P<moeda>(?<![\w])(?:US\$|U\$|€|£)\s?\d)
  | (?P<dataext>
        {_FRONTEIRA_ESQ}\d{{1,2}}(?:\s*(?:,|e|a|-|–)\s*\d{{1,2}})*\s+de\s+(?:{_MESES})(?:\s+de\s+\d{{4}})?
    )
  | (?P<datanum>{_FRONTEIRA_ESQ}\d{{1,2}}/\d{{1,2}}(?:/\d{{4}}|/\d{{2}})?(?![\w/]))
  | (?P<trimestre>{_FRONTEIRA_ESQ}[1-4]T(?:\d{{4}}|\d{{2}})(?![\w]))
  | (?P<periodo>{_FRONTEIRA_ESQ}\d{{1,4}}[ºª](?=\s+(?:reuni[ãa]o|(?:tri|se|quadri|bi)mestre|tri)\b))
  | (?P<cambio>{_SINAL}R\$\s?(?P<cambio_num>{_NUM})/US\$)
  | (?P<brl>{_SINAL}R\$\s?(?P<brl_num>{_NUM})(?:\s?(?P<brl_escala>{_ESCALA})(?![\w{_LETRA}]))?{_FRONTEIRA_DIR})
  | (?P<pct>{_SINAL}{_FRONTEIRA_ESQ}(?P<pct_num>{_NUM})\s?(?:%|por\s+cento\b))
  | (?P<pp>{_SINAL}{_FRONTEIRA_ESQ}(?P<pp_num>{_NUM})\s?(?:p\.p\.?|pp\b|pontos?\s+percentuais?\b))
  | (?P<bps>{_SINAL}{_FRONTEIRA_ESQ}(?P<bps_num>{_NUM})\s?(?:bps\b|bp\b|pontos?[-\s]base\b))
  | (?P<mult>{_SINAL}{_FRONTEIRA_ESQ}(?P<mult_num>{_NUM})\s?(?:x\b|vezes\b))
  | (?P<qtd>{_SINAL}{_FRONTEIRA_ESQ}(?P<qtd_num>{_NUM})\s(?P<qtd_escala>{_ESCALA})(?![\w{_LETRA}]))
  | (?P<ordinal>{_FRONTEIRA_ESQ}\d{{1,4}}[ºª](?![\w{_LETRA}]))
  | (?P<ano>{_FRONTEIRA_ESQ}(?:19|20)\d{{2}}{_FRONTEIRA_DIR})
  | (?P<simples>{_SINAL}{_FRONTEIRA_ESQ}(?P<simples_num>{_NUM}){_FRONTEIRA_DIR})
  | (?P<colado>{_FRONTEIRA_ESQ}\d+[_{_LETRA}]\w*)
  | (?P<estranho>{_FRONTEIRA_ESQ}\d+(?:[.,]\d+)+)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_ESCALA_MI = {"mil": -3, "mi": 0, "bi": 3, "tri": 6}
_ESCALA_UN = {"mil": 3, "mi": 6, "bi": 9, "tri": 12}


def converter(token: str) -> Decimal:
    """"1.234,56" -> Decimal("1234.56"). Só aceita o formato brasileiro."""
    if not re.fullmatch(_NUM, token):
        raise NumeroNaoPrevisto(f"formato de número não previsto: {token!r}")
    return Decimal(token.replace(".", "").replace(",", "."))


def _plano(valor: Decimal) -> Decimal:
    """Sem notação científica: 1.2E+3 vira 1200."""
    return Decimal(format(valor, "f"))


def _chave_escala(palavra: str) -> str:
    palavra = palavra.lower()
    for chave in ("tri", "bi", "mi"):
        if palavra.startswith(chave) and palavra != "mil":
            return chave
    return "mil"


def varrer(texto: str) -> list[Encontrado]:
    """Todos os números do texto, em ordem de posição. Offsets relativos a `texto`."""
    achados: list[Encontrado] = []
    for m in _PADRAO.finditer(texto):
        tipo = None
        for nome in (
            "ignorar", "moeda", "dataext", "datanum", "trimestre", "periodo", "cambio", "brl", "pct", "pp",
            "bps", "mult", "qtd", "ordinal", "ano", "simples", "colado", "estranho",
        ):
            if m.group(nome) is not None:
                tipo = nome
                break
        inicio, fim = m.span()
        bruto = m.group(0)
        if tipo in ("ignorar", "colado"):
            continue
        if tipo == "moeda":
            raise NumeroNaoPrevisto(
                f"moeda não prevista em {_contexto(texto, inicio)!r}: só R$ é interpretado"
            )
        if tipo == "estranho":
            raise NumeroNaoPrevisto(
                f"formato de número não previsto: {bruto!r} em {_contexto(texto, inicio)!r}. "
                "O parser só aceita ponto de milhar e vírgula decimal (1.234,56)."
            )
        if tipo in ("dataext", "datanum", "trimestre"):
            for parte in re.finditer(r"\d+", bruto):
                a, b = inicio + parte.start(), inicio + parte.end()
                achados.append(Encontrado(Decimal(parte.group()), "data", texto[a:b], a, b))
            continue
        if tipo == "periodo":
            numero = re.match(r"\d+", bruto).group()
            achados.append(Encontrado(Decimal(numero), "data", bruto, inicio, fim))
            continue
        if tipo == "ordinal":
            achados.append(Encontrado(Decimal(re.match(r"\d+", bruto).group()), "contagem", bruto, inicio, fim))
            continue
        if tipo == "ano":
            achados.append(Encontrado(Decimal(bruto), "data", bruto, inicio, fim))
            continue
        base = converter(m.group(f"{tipo}_num"))
        valor = -base if bruto[0] in "-−" else base
        if tipo == "cambio":
            achados.append(Encontrado(valor, "BRL", bruto, inicio, fim))
        elif tipo == "brl":
            escala = m.group("brl_escala")
            if escala:
                valor = _plano(valor.scaleb(_ESCALA_MI[_chave_escala(escala)]))
                achados.append(Encontrado(valor, "BRL_mi", bruto, inicio, fim))
            else:
                achados.append(Encontrado(valor, "BRL", bruto, inicio, fim))
        elif tipo == "pct":
            achados.append(Encontrado(valor, "pct", bruto, inicio, fim))
        elif tipo == "pp":
            achados.append(Encontrado(valor, "pp", bruto, inicio, fim))
        elif tipo == "bps":
            achados.append(Encontrado(_plano(valor.scaleb(-2)), "pp", bruto, inicio, fim))
        elif tipo == "mult":
            achados.append(Encontrado(valor, "x", bruto, inicio, fim))
        elif tipo == "qtd":
            valor = _plano(valor.scaleb(_ESCALA_UN[_chave_escala(m.group("qtd_escala"))]))
            achados.append(Encontrado(valor, "contagem", bruto, inicio, fim))
        else:  # simples
            achados.append(Encontrado(valor, "contagem", bruto, inicio, fim))
    return achados


def interpretar(bruto: str) -> Encontrado:
    """Interpreta um trecho que é exatamente um número (com unidade). Levanta NumeroNaoPrevisto se não for."""
    achados = varrer(bruto.strip())
    if len(achados) != 1 or achados[0].inicio != 0 or achados[0].fim != len(bruto.strip()):
        raise NumeroNaoPrevisto(f"não é um único número: {bruto!r}")
    return achados[0]


@dataclass(frozen=True)
class Data:
    """Data com dia lida do texto. `ano` é None quando o texto não traz o ano ("19/10", "19 de outubro")."""

    dia: int
    mes: int
    ano: int | None
    bruto: str


_NUMERO_DO_MES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
    "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_DATA_NUMERICA = re.compile(rf"{_FRONTEIRA_ESQ}(\d{{1,2}})/(\d{{1,2}})(?:/(\d{{4}}|\d{{2}}))?(?![\w/])")
_DATA_EXTENSO = re.compile(
    rf"{_FRONTEIRA_ESQ}((?:\d{{1,2}}|1º)(?:\s*(?:,|e|a|-|–)\s*\d{{1,2}})*)\s+de\s+({_MESES})(?:\s+de\s+(\d{{4}}))?",
    re.IGNORECASE,
)


def datas(texto: str) -> list[Data]:
    """Datas com dia do texto: "19/10", "19/10/2026", "19 de outubro", "15 e 16 de setembro de 2026" (uma
    Data por dia). Mês e ano sem dia ("junho de 2026", "dez/25") e trimestre ("2T26") ficam de fora."""
    achadas = []
    for m in _DATA_NUMERICA.finditer(texto):
        dia, mes = int(m.group(1)), int(m.group(2))
        ano = m.group(3)
        if 1 <= dia <= 31 and 1 <= mes <= 12:
            achadas.append(Data(dia, mes, _ano(ano), m.group(0)))
    for m in _DATA_EXTENSO.finditer(texto):
        mes = _NUMERO_DO_MES[m.group(2).lower()]
        for dia in re.findall(r"\d{1,2}", m.group(1)):
            achadas.append(Data(int(dia), mes, _ano(m.group(3)), m.group(0)))
    return achadas


def _ano(texto: str | None) -> int | None:
    if texto is None:
        return None
    return int(texto) + 2000 if len(texto) == 2 else int(texto)


def _contexto(texto: str, posicao: int, raio: int = 30) -> str:
    return texto[max(0, posicao - raio) : posicao + raio].replace("\n", " ")
