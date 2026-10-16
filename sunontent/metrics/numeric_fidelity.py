"""M1. Numeric Fidelity Check (docs/arquiteturas.md, E6). Determinístico, sem LLM.

Todo número da peça que não seja data vem de um marcador: coincide com uma posição de `Peca.numeros_usados`.
O que não fecha é violação, em quatro desfechos (mais o formato que o parser não lê):
- fora de marcador, com o mesmo valor e a mesma unidade de um número de claim do plano do nível;
- fora de marcador, presente na `tabela_numeros` mas em nenhum claim do plano do nível;
- fora de marcador e ausente da `tabela_numeros` (alucinação);
- marcador que a montagem deixou no texto por ser inválido (inexistente ou de claim fora do plano).
As formas do glossário com dígito (IPCA-15) são mascaradas antes da varredura. Datas com dia são conferidas contra
os claims do plano; anos, trimestres e número de reunião, contra os claims ou o ano da `data_documento`.
Número por extenso fica para o juiz. Na linha fina e no gancho visual nenhum número é aceito.
"""

import re
from collections import Counter
from datetime import date
from decimal import Decimal

from sunontent import numeros
from sunontent.glossario import Glossario
from sunontent.metrics import campos, resultado
from sunontent.schemas import (
    Claim,
    DesfechosNumericos,
    MetricaResultado,
    Numero,
    NumericFidelityDetalhes,
    Peca,
    Violacao,
)

_MARCADOR = re.compile(r"\{\{[^{}]*\}\}")
_DIGITO = re.compile(r"\d")
_TRECHO_ILEGIVEL = re.compile(r"\S+(?:\s+\d[\w.,]*)?")

ONDE = {"titulo": "o título", "linha_fina": "a linha fina", "texto": "o texto", "gancho_visual": "o gancho visual"}


def _mascarar(texto: str, trechos: list[tuple[int, int]]) -> str:
    for inicio, fim in trechos:
        texto = texto[:inicio] + " " * (fim - inicio) + texto[fim:]
    return texto


def _varrer(texto: str) -> tuple[list[numeros.Encontrado], list[tuple[int, int]]]:
    """Os números do texto e, à parte, os trechos que o parser não lê (cada um é mascarado e a varredura segue)."""
    ilegiveis: list[tuple[int, int]] = []
    while True:
        try:
            return numeros.varrer(texto), ilegiveis
        except numeros.NumeroNaoPrevisto as erro:
            achado = _TRECHO_ILEGIVEL.match(texto, erro.posicao) if erro.posicao is not None else None
            if achado is None:
                raise
            ilegiveis.append((achado.start(), achado.end()))
            texto = _mascarar(texto, [(achado.start(), achado.end())])


class _Plano:
    """O que os claims do plano do nível dizem, para conferir números e datas."""

    def __init__(self, claims: list[Claim], preferidos: list[str], data_documento: date):
        ordem = {cid: i for i, cid in enumerate(preferidos)}
        self.claims = sorted(claims, key=lambda c: ordem.get(c.id, len(ordem)))  # estável: o resto fica na ordem
        self.numeros = {(n.valor, n.unidade) for c in claims for n in c.numeros}
        self.datas = [d for c in claims for d in numeros.datas(c.texto)]
        self.componentes: set[Decimal] = {Decimal(data_documento.year), Decimal(data_documento.year % 100)}
        for c in claims:
            self.componentes |= {e.valor for e in _componentes_sem_dia(c.texto)}
            self.componentes |= {Decimal(d.ano) for d in numeros.datas(c.texto) if d.ano}
        self.ano_do_documento = data_documento.year

    def marcador_de(self, valor: Decimal, unidade: str) -> str | None:
        for claim in self.claims:
            for ordem, numero in enumerate(claim.numeros, start=1):
                if (numero.valor, numero.unidade) == (valor, unidade):
                    return "{{" + f"{claim.id}.{ordem}" + "}}"
        return None

    def tem_data(self, data: numeros.Data) -> bool:
        for d in self.datas:
            if (d.dia, d.mes) != (data.dia, data.mes):
                continue
            if data.ano is None or d.ano == data.ano or (d.ano is None and data.ano == self.ano_do_documento):
                return True
        return False


def _componentes_sem_dia(texto: str) -> list[numeros.Encontrado]:
    """Os componentes de data que não fazem parte de uma data com dia: ano, trimestre e número de reunião."""
    sem_datas = _mascarar(texto, [(a, b) for _, a, b in numeros.datas_com_posicao(texto)])
    achados, _ = _varrer(sem_datas)
    return [e for e in achados if e.unidade == "data"]


def _violacao(campo: campos.CampoTexto, inicio: int, fim: int, esperado: str, obtido: str, instrucao: str) -> Violacao:
    return Violacao(
        metrica="numeric_fidelity", trecho=campo.texto[inicio:fim], esperado=esperado, obtido=obtido,
        instrucao_corretiva=instrucao, unidade_indice=campo.unidade_indice, campo=campo.campo,
        inicio=inicio, fim=fim,
    )


def avaliar(
    peca: Peca,
    claims_do_plano: list[Claim],
    tabela_numeros: list[Numero],
    data_documento: date,
    glossario: Glossario,
) -> MetricaResultado:
    plano = _Plano(claims_do_plano, peca.claim_ids, data_documento)
    na_tabela = {(n.valor, n.unidade) for n in tabela_numeros}
    usados = [(n.campo, n.unidade_indice, n.inicio, n.fim) for n in peca.numeros_usados]
    desfechos: Counter[str] = Counter()
    violacoes: list[Violacao] = []
    varridos = com_marcador = datas_conferidas = 0

    for campo in campos.campos_da_peca(peca.payload):
        texto = campo.texto

        # Marcador que a montagem deixou no texto é inválido: os válidos já foram trocados.
        for m in _MARCADOR.finditer(texto):
            desfechos["marcador_invalido"] += 1
            violacoes.append(_violacao(
                campo, m.start(), m.end(), "marcador de um número de claim do plano do nível", "marcador inválido",
                f"O marcador {m.group()} não existe na tabela de marcadores deste nível: use só os marcadores "
                f"fornecidos ou escreva {ONDE[campo.campo]} sem o número.",
            ))
        texto = _mascarar(texto, [(m.start(), m.end()) for m in _MARCADOR.finditer(texto)])

        # Formas do glossário com dígito (IPCA-15) não são números da peça.
        texto = _mascarar(texto, [(o.inicio, o.fim) for o in glossario.casar(texto) if _DIGITO.search(o.forma)])

        achados, ilegiveis = _varrer(texto)
        for inicio, fim in ilegiveis:
            desfechos["formato_nao_previsto"] += 1
            violacoes.append(_violacao(
                campo, inicio, fim, "número que o parser lê (ponto de milhar, vírgula decimal e R$)", "formato não previsto",
                f"O trecho «{campo.texto[inicio:fim]}» tem um número em formato que não se lê: "
                f"escreva {ONDE[campo.campo]} sem ele ou use o marcador do número.",
            ))

        for e in achados:
            if e.unidade == "data":
                continue
            varridos += 1
            posicao = (campo.campo if campo.campo == "titulo" else "texto", campo.unidade_indice)
            if campo.campo in ("titulo", "texto") and any(
                (c, u) == posicao and a < e.fim and e.inicio < b for c, u, a, b in usados
            ):
                com_marcador += 1
                continue
            sugerido = plano.marcador_de(e.valor, e.unidade)
            if sugerido is not None:
                desfechos["fora_de_marcador_do_plano"] += 1
                esperado, obtido = f"o marcador {sugerido}", f"{e.bruto} escrito à mão"
                instrucao = f"Use o marcador {sugerido} no lugar do número {e.bruto}."
            elif (e.valor, e.unidade) in na_tabela:
                desfechos["fora_do_plano_no_documento"] += 1
                esperado, obtido = "número de um claim do plano do nível", f"{e.bruto}, que está no documento"
                instrucao = f"O número {e.bruto} existe no documento, mas não é fato do plano deste nível: remova-o."
            else:
                desfechos["ausente_do_documento"] += 1
                esperado, obtido = "número presente na tabela do documento", f"{e.bruto}, ausente do documento"
                instrucao = f"O número {e.bruto} não existe no documento: remova-o."
            if not campo.aceita_numero:
                instrucao = f"Remova o número {e.bruto} d{ONDE[campo.campo]}: número só vale no título e no texto, por marcador."
            violacoes.append(_violacao(campo, e.inicio, e.fim, esperado, obtido, instrucao))

        # Datas: com dia contra os claims do plano; ano, trimestre e reunião contra os claims ou o ano do documento.
        for data, inicio, fim in numeros.datas_com_posicao(texto):
            datas_conferidas += 1
            if not plano.tem_data(data):
                desfechos["data_sem_lastro"] += 1
                violacoes.append(_violacao(
                    campo, inicio, fim, "data de um claim do plano do nível", data.bruto,
                    f"A data «{data.bruto}» não está nos claims do plano: remova-a ou escreva a data como o claim a traz.",
                ))
        for e in _componentes_sem_dia(texto):
            datas_conferidas += 1
            if e.valor not in plano.componentes:
                desfechos["data_sem_lastro"] += 1
                violacoes.append(_violacao(
                    campo, e.inicio, e.fim, "ano, trimestre ou reunião de um claim do plano, ou o ano do documento", e.bruto,
                    f"«{e.bruto}» não aparece nos claims do plano nem é o ano do documento: remova-o.",
                ))

    detalhes = NumericFidelityDetalhes(
        numeros_varridos=varridos, com_marcador=com_marcador, datas_conferidas=datas_conferidas,
        desfechos=DesfechosNumericos(**desfechos),
    )
    return resultado.montar(detalhes, violacoes)
