"""Testes do dashboard (app/): classificação das linhas e API com processo e consulta simulados."""

import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from app import execucao, servidor
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

    def __init__(self, runs=(), falha=None):
        self.runs = {r["run_id"]: r for r in runs}
        self.falha = falha

    def listar_runs(self):
        if self.falha:
            raise self.falha
        return list(self.runs.values())

    def detalhar_run(self, run_id):
        if self.falha:
            raise self.falha
        run = self.runs.get(run_id)
        return None if run is None else {"manifest": run, "documento": None, "factsheet": None,
                                         "validacoes": [], "spans": []}


def montar_cliente(tmp_path, consulta):
    gerenciador = execucao.Gerenciador(tmp_path)
    return TestClient(servidor.criar_app(tmp_path, gerenciador, consulta)), gerenciador


@pytest.fixture
def cliente(tmp_path):
    return montar_cliente(tmp_path, ConsultaFalsa())


def test_grafo_vem_do_graph_py(cliente):
    http, _ = cliente
    corpo = http.get("/api/grafo").json()
    assert {"coleta", "referencia", "leitura", "extract", "validate"} <= set(corpo["nos"])
    assert {"de": "validate", "para": "extract", "condicional": True} in corpo["arestas"]


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
    run = {"run_id": RUN_ID, "status": "concluido"}
    http, _ = montar_cliente(tmp_path, ConsultaFalsa([run]))
    assert http.get("/api/runs").json() == [run]
    assert http.get(f"/api/runs/{RUN_ID}").json()["manifest"] == run


def test_supabase_fora_do_ar_responde_503_com_a_mensagem(tmp_path):
    http, _ = montar_cliente(tmp_path, ConsultaFalsa(falha=FalhaRetriavel("conexão recusada")))
    for rota in ("/api/runs", f"/api/runs/{RUN_ID}"):
        resposta = http.get(rota)
        assert resposta.status_code == 503
        assert "conexão recusada" in resposta.json()["detail"]
