"""E1: emissor e data do documento, extraídos do texto limpo, sem LLM.

Data do documento por fonte:
- Copom: último dia da reunião ("15 e 16 de setembro de 2026" é 2026-09-16), a data da decisão.
- CVM: data da linha de assinatura ("São Paulo, 08 de outubro de 2026.").
- B3: data-base do balanço ("CONSOLIDADO EM 30/06/2026").
"""

import re
from datetime import date

from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import Pagina, TipoDocumento

EMISSOR_FIXO = {
    "copom_ata": "Comitê de Política Monetária (Copom) do Banco Central do Brasil",
    "b3_release": "B3 S.A. - Brasil, Bolsa, Balcão",
}
_MESES = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_NOMES_MESES = "|".join(_MESES)
_REUNIAO = re.compile(rf"\b(\d{{1,2}})\s+e\s+(\d{{1,2}})\s+de\s+({_NOMES_MESES})\s+de\s+(\d{{4}})\b", re.IGNORECASE)
_ASSINATURA = re.compile(rf"^[^\d,]+,\s*(\d{{1,2}})\s+de\s+({_NOMES_MESES})\s+de\s+(\d{{4}})\.?$", re.IGNORECASE)
_BASE_B3 = re.compile(r"CONSOLIDADO EM (\d{2})/(\d{2})/(\d{4})")
_TITULO_CVM = re.compile(r"(JOINT\s+)?(FATO RELEVANTE|MATERIAL FACT)\b", re.IGNORECASE)
_REGISTRO_CVM = re.compile(
    r"(CNPJ|NIRE|Companhia Aberta|Companhia de Capital Aberto|Publicly Held Company)\b", re.IGNORECASE
)


def extrair(tipo: TipoDocumento, paginas: list[Pagina]) -> tuple[str, date]:
    linhas = [l for p in paginas for l in p.texto_limpo.split("\n")]
    if tipo == "copom_ata":
        return EMISSOR_FIXO[tipo], _data_copom(linhas)
    if tipo == "b3_release":
        return EMISSOR_FIXO[tipo], _data_b3(linhas)
    return _emissor_cvm(paginas[0]), _data_cvm(linhas)


def _data(dia: str, mes: str, ano: str, onde: str) -> date:
    try:
        return date(int(ano), _MESES[mes.lower()] if not mes.isdigit() else int(mes), int(dia))
    except (ValueError, KeyError) as erro:
        raise FalhaTerminal(f"E1: data inválida em {onde}: {dia} {mes} {ano}") from erro


def _data_copom(linhas: list[str]) -> date:
    for linha in linhas:
        achado = _REUNIAO.search(linha)
        if achado:
            return _data(achado.group(2), achado.group(3), achado.group(4), "reunião do Copom")
    raise FalhaTerminal("E1: não encontrei a data da reunião no formato '15 e 16 de setembro de 2026'")


def _data_cvm(linhas: list[str]) -> date:
    for linha in reversed(linhas):
        achado = _ASSINATURA.match(linha.strip())
        if achado:
            return _data(achado.group(1), achado.group(2), achado.group(3), "assinatura do fato relevante")
    raise FalhaTerminal("E1: não encontrei a linha de assinatura com a data ('Cidade, 02 de outubro de 2026.')")


def _data_b3(linhas: list[str]) -> date:
    for linha in linhas:
        achado = _BASE_B3.search(linha)
        if achado:
            return _data(achado.group(1), achado.group(2), achado.group(3), "data-base do release da B3")
    raise FalhaTerminal("E1: não encontrei 'CONSOLIDADO EM dd/mm/aaaa' no release da B3")


def _emissor_cvm(primeira: Pagina) -> str:
    """Nome de cada companhia do cabeçalho, antes do título ("FATO RELEVANTE" ou "MATERIAL FACT"): as
    linhas do nome terminam numa linha de registro (CNPJ, NIRE, "Companhia Aberta"). Fato relevante
    conjunto tem um nome por companhia, unidos por "; ". Linhas sem letra (resto de diagramação) são
    ignoradas. Sem título ou sem nome antes dele, o emissor é a primeira linha com letras, com aviso
    no terminal; só a página sem nenhuma letra aborta."""
    linhas = [linha.strip() for linha in primeira.texto_limpo.split("\n")]
    nomes, partes, com_titulo = [], [], False
    for linha in linhas:
        if _TITULO_CVM.match(linha):
            com_titulo = True
            break
        if _REGISTRO_CVM.match(linha):
            if partes:
                nomes.append(" ".join(partes))
                partes = []
        elif _tem_letra(linha):
            partes.append(linha)
    if com_titulo and nomes:
        return "; ".join(nomes)
    primeira_com_letra = next((linha for linha in linhas if _tem_letra(linha)), None)
    if primeira_com_letra is None:
        raise FalhaTerminal("E1: a primeira página do fato relevante não tem texto, sem o nome do emissor")
    print(f"E1: emissor não identificado no cabeçalho; usando a primeira linha: {primeira_com_letra!r}", flush=True)
    return primeira_com_letra


def _tem_letra(linha: str) -> bool:
    return any(c.isalpha() for c in linha)
