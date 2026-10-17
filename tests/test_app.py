"""Testes do dashboard (app/): classificação das linhas e API com processo e consulta simulados."""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import execucao, servidor, slide_svg
from sunontent import graph
from sunontent.retentativa import FalhaRetriavel

RUN_ID = "copom_20261006T150000Z_aaaaaa"


def esperar_fim(execucao_atual, limite=10.0):
    fim = time.monotonic() + limite
    while not execucao_atual.terminou and time.monotonic() < fim:
        time.sleep(0.05)
    assert execucao_atual.terminou


# Classificação das linhas do terminal


@pytest.mark.parametrize(
    "texto, esperado",
    [
        (f"run {RUN_ID} (dev)", {"tipo": "run", "run_id": RUN_ID, "modo": "dev"}),
        ("etapa leitura: iniciada", {"tipo": "etapa", "no": "leitura", "estado": "iniciada"}),
        ("etapa validate: concluída", {"tipo": "etapa", "no": "validate", "estado": "concluída"}),
        ("celula iniciante__artigo synth 1: iniciada", {
            "tipo": "celula", "celula_id": "iniciante__artigo", "no": "synth", "tentativa": "1", "estado": "iniciada"}),
        ("celula avancado__roteiro router 2: concluída", {
            "tipo": "celula", "celula_id": "avancado__roteiro", "no": "router", "tentativa": "2", "estado": "concluída"}),
        ("celula intermediario__carrossel: REPROVADA", {
            "tipo": "celula", "celula_id": "intermediario__carrossel", "estado": "REPROVADA"}),
        ("celula iniciante__artigo: AVALIANDO", {"tipo": "log"}),
        ("concluído: copom_ata_281 (f03423dc2562), 7 claims", {"tipo": "concluido", "resumo": "copom_ata_281 (f03423dc2562), 7 claims"}),
        ("abortado (terminal): E3: 1 claims", {"tipo": "abortado", "classe": "terminal", "mensagem": "E3: 1 claims"}),
        ("erro (terminal): SUPABASE_URL ausente", {"tipo": "erro", "classe": "terminal", "mensagem": "SUPABASE_URL ausente"}),
        ("copom: baixando ata 281", {"tipo": "log"}),
    ],
)
def test_classifica_as_linhas_do_main(texto, esperado):
    assert execucao.classificar(texto) == esperado


def test_comando_do_pipeline():
    assert execucao.montar_comando(None, False)[1:] == ["-m", "sunontent"]
    assert execucao.montar_comando("cvm", True)[1:] == ["-m", "sunontent", "cvm", "--dev"]
    with pytest.raises(ValueError):
        execucao.montar_comando("nasdaq", False)


# API


@pytest.fixture
def processo_simulado(monkeypatch):
    """Troca o pipeline por um script que escreve as linhas do __main__.py."""
    original = execucao.montar_comando

    def montar(fonte, dev):
        original(fonte, dev)  # mantém a validação da fonte
        script = (
            "import sys, time\n"
            f"print('run {RUN_ID} (dev)')\n"
            "print('etapa referencia: iniciada')\n"
            "print('aviso no stderr', file=sys.stderr)\n"
            "time.sleep(0.3)\n"
            "print('etapa referencia: concluída')\n"
            "print('concluído: copom_ata_281')\n"
        )
        return [sys.executable, "-c", script]

    monkeypatch.setattr(execucao, "montar_comando", montar)


class ConsultaFalsa:
    """No lugar da ConsultaSupabase: devolve os runs prontos ou levanta a falha pedida."""

    def __init__(self, runs=(), falha=None, pecas=()):
        self.runs = {r["run_id"]: r for r in runs}
        self.falha = falha
        self.pecas = [dict(p) for p in pecas]
        self.pedidos_de_peca = []
        self.rodadas_extracao = []  # o `rodadas_extracao` de cada leitura de runs

    def peca(self, run_id, celula_id, tentativa=None):
        self.pedidos_de_peca.append((run_id, celula_id, tentativa))
        if self.falha:
            raise self.falha
        gravadas = [p for p in self.pecas if (p["run_id"], p["celula_id"]) == (run_id, celula_id)]
        if tentativa is not None:
            gravadas = [p for p in gravadas if p["tentativa"] == tentativa]
        return max(gravadas, key=lambda p: p["tentativa"], default=None)

    def listar_runs(self, *, rodadas_extracao):
        self.rodadas_extracao.append(rodadas_extracao)
        if self.falha:
            raise self.falha
        return list(self.runs.values())

    def balanco(self):
        if self.falha:
            raise self.falha
        return {"runs": [], "celulas": [], "metricas": []}

    def biblioteca(self):
        if self.falha:
            raise self.falha
        return [{"run_id": p["run_id"], "celula_id": p["celula_id"]} for p in self.pecas]

    def detalhar_run(self, run_id, *, rodadas_extracao):
        self.rodadas_extracao.append(rodadas_extracao)
        if self.falha:
            raise self.falha
        run = self.runs.get(run_id)
        return None if run is None else {"manifest": run, "documento": None, "factsheet": None,
                                         "validacoes": [], "descartados": run.get("descartados"), "spans": []}


def montar_cliente(tmp_path, consulta):
    gerenciador = execucao.Gerenciador(tmp_path)
    return TestClient(servidor.criar_app(tmp_path, gerenciador, consulta)), gerenciador


@pytest.fixture
def cliente(tmp_path):
    return montar_cliente(tmp_path, ConsultaFalsa())


def test_balanco_biblioteca_e_peca_da_celula(tmp_path):
    peca = {"run_id": "r1", "celula_id": "iniciante__artigo", "tentativa": 1, "formato": "artigo", "payload": {}}
    http, _ = montar_cliente(tmp_path, ConsultaFalsa(pecas=[peca]))
    assert http.get("/api/balanco").json() == {"runs": [], "celulas": [], "metricas": []}
    assert http.get("/api/biblioteca").json() == [{"run_id": "r1", "celula_id": "iniciante__artigo"}]
    assert http.get("/api/runs/r1/pecas/iniciante__artigo").json()["tentativa"] == 1
    assert http.get("/api/runs/r1/pecas/avancado__roteiro").status_code == 404


def test_grafo_vem_do_graph_py(cliente):
    http, _ = cliente
    corpo = http.get("/api/grafo").json()
    assert {"coleta", "referencia", "leitura", "extract", "validate"} <= set(corpo["nos"])
    assert {"de": "validate", "para": "extract", "condicional": True} in corpo["arestas"]


def test_e4_entra_no_fluxo_do_dashboard(cliente):
    """O nó adapt aparece no grafo, e o dashboard marca a E4 (nó adapt em app.js) como implementada."""
    http, _ = cliente
    corpo = http.get("/api/grafo").json()
    assert "adapt" in corpo["nos"]
    arestas = {(a["de"], a["para"]) for a in corpo["arestas"]}
    assert {("validate", "adapt"), ("descarte", "adapt")} <= arestas
    assert corpo["rodadas_plano"] == 3
    assert 'id: "E4", nos: ["adapt"]' in http.get("/static/app.js").text


def test_grafo_abre_o_subgrafo_da_celula(cliente):
    """Com xray, os nós da célula entram como nós do grafo, sem os pseudonós, e o fan-out liga a E4 ao synth."""
    http, _ = cliente
    corpo = http.get("/api/grafo").json()
    assert {"synth", "evaluate", "router", "fechamento"} <= set(corpo["nos"])
    assert not any(":" in no or no.startswith("__") for no in corpo["nos"])
    arestas = {(a["de"], a["para"]) for a in corpo["arestas"]}
    assert {("adapt", "synth"), ("synth", "evaluate"), ("evaluate", "router"), ("router", "synth"),
            ("router", "fechamento"), ("fechamento", "__end__")} <= arestas
    assert not any(":" in de or ":" in para for de, para in arestas)
    assert corpo["max_concorrencia"] == 3
    app_js = http.get("/static/app.js").text
    for etapa, no in (("E5", "synth"), ("E6", "evaluate"), ("E7", "router")):
        assert f'id: "{etapa}", nos: ["{no}"], celulas: true' in app_js


def test_execucao_transmite_as_linhas_classificadas(cliente, processo_simulado):
    http, gerenciador = cliente
    assert http.post("/api/execucao", json={"fonte": "copom", "dev": True}).status_code == 201
    assert http.post("/api/execucao", json={"dev": False}).status_code == 409  # uma execução por vez
    esperar_fim(gerenciador.atual)
    corpo = http.get("/api/execucao").json()
    assert corpo["estado"] == "concluida" and corpo["codigo"] == 0
    tipos = [(e["tipo"], e["canal"]) for e in corpo["eventos"]]
    assert ("run", "stdout") in tipos and ("log", "stderr") in tipos
    assert tipos[-1] == ("fim", "sistema")
    with http.stream("GET", "/api/execucao/eventos?desde=0") as resposta:
        dados = [json.loads(linha[6:]) for linha in resposta.iter_lines() if linha.startswith("data: ")]
    assert [e["n"] for e in dados] == list(range(len(corpo["eventos"])))


def test_fonte_desconhecida_e_recusada(cliente):
    http, _ = cliente
    assert http.post("/api/execucao", json={"fonte": "nasdaq"}).status_code == 422


def test_parar_sem_execucao_e_recusado(cliente):
    http, _ = cliente
    assert http.post("/api/execucao/parar").status_code == 409
    assert http.get("/api/execucao").json() is None


def test_run_inexistente_responde_404(cliente):
    http, _ = cliente
    assert http.get(f"/api/runs/{RUN_ID}").status_code == 404
    assert http.get("/api/runs").json() == []


def test_runs_vem_da_consulta(tmp_path):
    run = {"run_id": RUN_ID, "status": "concluido", "descartados": 2}
    consulta = ConsultaFalsa([run])
    http, _ = montar_cliente(tmp_path, consulta)
    assert http.get("/api/runs").json() == [run]
    detalhe = http.get(f"/api/runs/{RUN_ID}").json()
    assert detalhe["manifest"] == run and detalhe["descartados"] == 2
    # Os descartados são os reprovados na última rodada E2/E3, a do graph.py.
    assert consulta.rodadas_extracao == [graph.RODADAS_EXTRACAO] * 2


def test_supabase_fora_do_ar_responde_503_com_a_mensagem(tmp_path):
    http, _ = montar_cliente(tmp_path, ConsultaFalsa(falha=FalhaRetriavel("conexão recusada")))
    for rota in ("/api/runs", f"/api/runs/{RUN_ID}"):
        resposta = http.get(rota)
        assert resposta.status_code == 503
        assert "conexão recusada" in resposta.json()["detail"]


# Slide do carrossel em SVG

CELULA = "iniciante__carrossel"
SLIDES = [
    {"papel": "gancho", "texto": "O Copom manteve a Selic em 15% ao ano & surpreendeu quem esperava um corte de juros",
     "claim_ids": []},
    {"papel": "corpo", "texto": "A decisão foi unânime entre os nove membros do comitê, que citaram a inflação ainda "
     "acima da meta e o mercado de trabalho aquecido como motivos para a cautela nas próximas reuniões.",
     "claim_ids": ["C01", "C02"]},
    {"papel": "conclusao", "texto": "Para quem tem dívida, o juro alto segue pesando no orçamento <por mais tempo>.",
     "claim_ids": []},
]
NS_SVG = "{http://www.w3.org/2000/svg}"


def peca_carrossel(tentativa=1, slides=SLIDES):
    return {"run_id": RUN_ID, "celula_id": CELULA, "tentativa": tentativa, "formato": "carrossel",
            "payload": {"slides": slides}, "claim_ids": ["C01", "C02"]}


def linhas_do_svg(svg: str) -> list[str]:
    texto = ET.fromstring(svg).find(f".//{NS_SVG}text[@id='texto']")
    return ["".join(t.itertext()) for t in texto.findall(f"{NS_SVG}tspan")]


@pytest.mark.parametrize("papel", sorted(slide_svg.CAIXAS))
def test_quebra_de_linha_nao_muda_o_texto_e_cabe_na_largura(papel):
    caixa = slide_svg.CAIXAS[papel]
    for slide in SLIDES:
        linhas = slide_svg.quebrar_linhas(slide["texto"], caixa)
        assert len(linhas) > 1
        assert " ".join(linhas) == slide["texto"]
        for linha in linhas:
            assert " " not in linha or slide_svg.largura_da_linha(linha, caixa) <= caixa.largura


def test_palavra_maior_que_a_caixa_fica_sozinha_na_linha():
    caixa = slide_svg.CAIXAS["corpo"]
    longa = "A" * 80
    assert slide_svg.quebrar_linhas(f"antes {longa} depois", caixa) == ["antes", longa, "depois"]
    assert slide_svg.largura_da_linha(longa, caixa) > caixa.largura


def test_espacos_repetidos_e_quebras_de_linha_valem_um_espaco():
    assert slide_svg.quebrar_linhas("a  b\nc", slide_svg.CAIXAS["corpo"]) == ["a b c"]
    assert slide_svg.quebrar_linhas("   ", slide_svg.CAIXAS["corpo"]) == []


def montar(slide, posicao=2):
    return slide_svg.montar_slide(
        texto=slide["texto"], papel=slide["papel"], claim_ids=slide["claim_ids"], celula_id=CELULA, tentativa=3,
        posicao=posicao, total=len(SLIDES),
    )


def test_svg_tem_o_texto_em_tspans_e_os_metadados():
    slide = SLIDES[1]
    desenho = montar(slide)
    raiz_svg = ET.fromstring(desenho.svg)
    assert (raiz_svg.get("width"), raiz_svg.get("height"), raiz_svg.get("viewBox")) == ("1080", "1350", "0 0 1080 1350")
    assert linhas_do_svg(desenho.svg) == desenho.linhas
    assert " ".join(linhas_do_svg(desenho.svg)) == slide["texto"]
    assert raiz_svg.find(f"{NS_SVG}metadata")[0].attrib == {
        "celula_id": CELULA, "tentativa": "3", "posicao": "2", "total": "3", "papel": "corpo",
        "claim_ids": "C01 C02", "estouro_altura": "false",
    }
    assert not desenho.estouro_altura


def test_svg_escapa_o_texto_sem_mudar_as_palavras():
    for slide in (SLIDES[0], SLIDES[2]):
        assert " ".join(linhas_do_svg(montar(slide).svg)) == slide["texto"]


def test_texto_que_nao_cabe_na_altura_da_caixa_sai_com_aviso():
    desenho = montar({"papel": "gancho", "texto": " ".join(["palavra"] * 200), "claim_ids": []}, 1)
    assert desenho.estouro_altura
    assert ET.fromstring(desenho.svg).find(f"{NS_SVG}metadata")[0].get("estouro_altura") == "true"


def test_nome_do_arquivo_do_slide():
    assert slide_svg.nome_do_arquivo(CELULA, 2, 4) == "iniciante__carrossel_t2_slide4.svg"


def cliente_com_pecas(tmp_path, *pecas):
    consulta = ConsultaFalsa(pecas=pecas)
    http, _ = montar_cliente(tmp_path, consulta)
    return http, consulta


def test_rota_devolve_o_svg_do_slide_da_ultima_tentativa(tmp_path):
    http, consulta = cliente_com_pecas(tmp_path, peca_carrossel(1), peca_carrossel(2))
    resposta = http.get(f"/api/runs/{RUN_ID}/carrossel/{CELULA}/2.svg")
    assert resposta.status_code == 200 and resposta.headers["content-type"].startswith("image/svg+xml")
    assert resposta.headers["x-estouro-altura"] == "false"
    assert resposta.headers["content-disposition"] == 'inline; filename="iniciante__carrossel_t2_slide2.svg"'
    assert " ".join(linhas_do_svg(resposta.text)) == SLIDES[1]["texto"]
    meta = ET.fromstring(resposta.text).find(f"{NS_SVG}metadata")[0]
    assert (meta.get("celula_id"), meta.get("tentativa"), meta.get("posicao"), meta.get("claim_ids")) == (
        CELULA, "2", "2", "C01 C02")
    assert consulta.pedidos_de_peca == [(RUN_ID, CELULA, None)]


def test_rota_baixa_com_o_nome_do_arquivo_e_aceita_a_tentativa(tmp_path):
    http, consulta = cliente_com_pecas(tmp_path, peca_carrossel(1), peca_carrossel(2))
    baixado = http.get(f"/api/runs/{RUN_ID}/carrossel/{CELULA}/3.svg?tentativa=1&baixar=true")
    assert baixado.headers["content-disposition"] == 'attachment; filename="iniciante__carrossel_t1_slide3.svg"'
    assert consulta.pedidos_de_peca == [(RUN_ID, CELULA, 1)]
    assert baixado.text == http.get(f"/api/runs/{RUN_ID}/carrossel/{CELULA}/3.svg?tentativa=1").text


def test_rota_responde_404_para_o_que_nao_existe(tmp_path):
    artigo = {**peca_carrossel(), "celula_id": "iniciante__artigo", "formato": "artigo"}
    http, _ = cliente_com_pecas(tmp_path, peca_carrossel(), artigo)
    for caminho in (
        f"{CELULA}/4.svg",  # o carrossel tem 3 slides
        f"{CELULA}/0.svg",
        f"{CELULA}/um.svg",
        f"{CELULA}/2.png",
        "avancado__carrossel/1.svg",  # célula sem peça
        "iniciante__artigo/1.svg",  # não é carrossel
        f"{CELULA}/1.svg?tentativa=9",
    ):
        assert http.get(f"/api/runs/{RUN_ID}/carrossel/{caminho}").status_code == 404, caminho


def test_rota_do_slide_responde_503_com_o_banco_fora_do_ar(tmp_path):
    http, _ = montar_cliente(tmp_path, ConsultaFalsa(falha=FalhaRetriavel("conexão recusada")))
    resposta = http.get(f"/api/runs/{RUN_ID}/carrossel/{CELULA}/1.svg")
    assert resposta.status_code == 503 and "conexão recusada" in resposta.json()["detail"]


def test_pagina_carrega_a_fonte_e_as_abas_da_sidebar(cliente):
    http, _ = cliente
    assert "Inter-Regular.ttf" in http.get("/static/estilo.css").text
    for arquivo in ("Inter-Regular.ttf", "Inter-Bold.ttf", "Inter-OFL.txt"):
        assert http.get(f"/static/fontes/{arquivo}").status_code == 200
    pagina = http.get("/").text
    abas = ["inicio", "resultado", "fluxo", "tecnicas", "historico"]
    posicoes = [pagina.index(f'data-aba="{aba}"') for aba in abas]
    assert posicoes == sorted(posicoes)
    assert 'data-aba="matriz"' not in pagina
    for script in ("resultado", "tecnicas", "app"):
        assert f"/static/{script}.js" in pagina
        assert http.get(f"/static/{script}.js").status_code == 200


# Claims descartados na E3


def test_dashboard_mostra_os_descartados_sem_revisao(cliente):
    """Sem aba, script nem rota de revisão; Início, Resultado e Histórico mostram os descartados."""
    http, _ = cliente
    pagina = http.get("/").text
    assert "revisao.js" not in pagina and http.get("/static/revisao.js").status_code == 404
    assert set(re.findall(r'data-aba="(\w+)"', pagina)) == {"inicio", "resultado", "fluxo", "tecnicas", "historico"}
    assert 'metrica("Claims descartados na E3", fmtInt(d.descartados))' in http.get("/static/resultado.js").text
    app_js = http.get("/static/app.js").text
    assert "fmtInt(ultimo.descartados)" in app_js and "fmtInt(r.descartados)" in app_js
    assert 'id: "E3", nos: ["validate", "descarte"]' in app_js
    assert 'const ABAS = ["inicio", "resultado", "fluxo", "tecnicas", "historico"];' in app_js
    # As únicas rotas que mudam algo são as que iniciam e param o pipeline; nenhuma grava no banco.
    rotas = {(m, r.path) for r in http.app.routes for m in getattr(r, "methods", ())}
    assert {(m, caminho) for m, caminho in rotas if m != "GET" and m != "HEAD"} == {
        ("POST", "/api/execucao"), ("POST", "/api/execucao/parar")
    }
    assert {caminho for _, caminho in rotas if caminho.startswith("/api/runs/{run_id}/")} == {
        "/api/runs/{run_id}/carrossel/{celula_id}/{arquivo}", "/api/runs/{run_id}/pecas/{celula_id}"
    }
