"""Testes do manifest, do grafo e do __main__: repositório em memória, checkpointer em memória e tmp_path."""

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from conftest import RepositorioMemoria, cliente_falso
from sunontent import __main__ as principal
from sunontent import graph, manifesto, persistencia
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal
from test_ingestao import cliente, listagem

RAIZ = Path(__file__).resolve().parent.parent
MOMENTO = datetime(2026, 10, 6, 15, 0, 0, tzinfo=timezone.utc)
REFERENCIA = RAIZ / "tests" / "fixtures" / "referencia"
PDF = (REFERENCIA / "copom.pdf").read_bytes()


def claim(literal, numeros=(), tipo="decisao", chunk_id="K19"):
    return {
        "id": "C01", "chunk_id": chunk_id, "texto": "O Copom reduziu a Selic para 13,75% a.a.",
        "literal_fonte": literal, "numeros": list(numeros), "tipo": tipo, "tags": ["indicador_chave"],
    }


DECISAO = claim("O Copom decidiu reduzir a taxa básica de juros para 13,75% a.a.", ["13,75%"])
RESPOSTA_COPOM = json.dumps({"claims": [DECISAO]}, ensure_ascii=False)


@pytest.fixture
def raiz(tmp_path):
    """Cópia de config/ e prompts/ e da referência do Copom, tudo em tmp_path."""
    shutil.copytree(RAIZ / "config", tmp_path / "config")
    shutil.copytree(RAIZ / "prompts", tmp_path / "prompts")
    pasta = tmp_path / "tests" / "fixtures" / "referencia"
    pasta.mkdir(parents=True)
    shutil.copy(REFERENCIA / "copom.pdf", pasta / "copom.pdf")
    shutil.copy(REFERENCIA / "copom.json", pasta / "copom.json")
    return tmp_path


@pytest.fixture
def repo():
    return RepositorioMemoria()


def rodar(raiz, repo, *, dev, http=None, llm=None):
    return principal.executar(
        "copom", dev=dev, repositorio=repo, checkpointer=InMemorySaver(),
        cliente_llm=llm or cliente_falso([RESPOSTA_COPOM]), raiz=raiz,
        cliente_http=http, relogio=lambda: MOMENTO,
    )


def manifest_local(raiz):
    (arquivo,) = (raiz / "data" / "runs").glob("*/manifest.json")
    return json.loads(arquivo.read_text(encoding="utf-8"))


# Manifest


def test_manifest_le_versoes_e_hashes_da_config(raiz):
    m = manifesto.montar(raiz, run_id="copom_20261006T150000Z_aaaaaa", timestamp=MOMENTO,
                         modo="normal", fonte="copom", k=2)
    assert m.level_specs == {"iniciante": 1, "intermediario": 1, "avancado": 1}
    assert m.glossario_versao == 1
    assert m.prompts == {"extractor": 2}  # templates vazios ficam de fora
    assert set(m.arquivos_sha256) == {
        "config/models.yaml", "config/glossario.yaml", "prompts/extractor.j2",
        "config/levels/iniciante.yaml", "config/levels/intermediario.yaml", "config/levels/avancado.yaml",
    }


def test_hash_ignora_fim_de_linha(raiz):
    def montar():
        return manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2)

    antes = montar().arquivos_sha256["config/glossario.yaml"]
    (raiz / "config" / "glossario.yaml").write_bytes(b"versao: 1\r\n")
    assert montar().arquivos_sha256["config/glossario.yaml"] == antes


def test_template_com_conteudo_exige_cabecalho_de_versao(raiz):
    (raiz / "prompts" / "adapter.j2").write_text("sem cabecalho", encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="prompts/adapter.j2"):
        manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2)


def test_template_com_cabecalho_entra_no_manifest(raiz):
    (raiz / "prompts" / "adapter.j2").write_text("{# versao: 3 #}\nolá", encoding="utf-8")
    m = manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2)
    assert m.prompts == {"adapter": 3, "extractor": 2}
    assert "prompts/adapter.j2" in m.arquivos_sha256


def test_yaml_vazio_aponta_o_arquivo(raiz):
    (raiz / "config" / "levels" / "avancado.yaml").write_text("", encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="config/levels/avancado.yaml"):
        manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2)


# Grafo


def test_entrada_escolhida_pelo_modo():
    class E:  # só precisa de manifest.modo
        def __init__(self, modo):
            self.manifest = type("M", (), {"modo": modo})()

    assert graph.escolher_entrada(E("dev")) == "referencia"
    assert graph.escolher_entrada(E("normal")) == "coleta"


# __main__: modo normal


def test_modo_normal_conclui_e_grava_run_e_manifest(raiz, repo):
    codigo = rodar(raiz, repo, dev=False, http=cliente(listagem("copom"), fonte="copom"))
    assert codigo == 0
    (run_id, run), = repo.runs.items()
    assert re.fullmatch(r"copom_20261006T150000Z_[0-9a-f]{6}", run_id)
    assert run["status"] == "concluido" and run["coleta_id"] == 1
    local = manifest_local(raiz)
    assert local["status"] == "concluido" and local["modo"] == "normal"
    assert local["doc_id"] == "copom_ata_281" and local["coleta_id"] == 1
    assert local["K"] == graph.K
    assert (raiz / "data" / "sources" / "copom").exists()


def test_falha_da_coleta_aborta_o_run(raiz, repo):
    http = cliente({})  # tudo 404
    assert rodar(raiz, repo, dev=False, http=http) == 1
    (run,) = repo.runs.values()
    assert run["status"] == "abortado" and run["erro_classe"] == "terminal"
    assert "404" in run["erro_mensagem"]
    assert run.get("doc_sha256") is None  # campos de documento intocados
    local = manifest_local(raiz)
    assert local["status"] == "abortado"
    assert local["erro_classe"] == "terminal" and local["erro_mensagem"] == run["erro_mensagem"]
    assert not repo.documentos and not repo.coletas


def test_falha_terminal_levantada_pelo_no_aborta_com_classe_e_mensagem(raiz, repo, monkeypatch):
    def no_que_falha(state, config):
        raise FalhaTerminal("E0 falhou de propósito")

    monkeypatch.setattr(graph, "coleta", no_que_falha)
    assert rodar(raiz, repo, dev=False, http=cliente({})) == 1
    (run,) = repo.runs.values()
    assert (run["status"], run["erro_classe"], run["erro_mensagem"]) == (
        "abortado", "terminal", "E0 falhou de propósito")
    assert manifest_local(raiz)["erro_mensagem"] == "E0 falhou de propósito"


def test_erro_inesperado_fecha_o_run_e_propaga(raiz, repo, monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(graph, "coleta", quebra)
    with pytest.raises(RuntimeError):
        rodar(raiz, repo, dev=False, http=cliente({}))
    (run,) = repo.runs.values()
    assert (run["status"], run["erro_classe"], run["erro_mensagem"]) == (
        "abortado", "terminal", "RuntimeError: bug")


# __main__: modo --dev


def test_modo_dev_usa_a_referencia_sem_baixar_nem_criar_coleta(raiz, repo):
    assert rodar(raiz, repo, dev=True) == 0
    (run,) = repo.runs.values()
    assert run["status"] == "concluido" and run["coleta_id"] is None
    assert run["doc_id"] == "copom_ata_281"
    assert not repo.coletas
    assert len(repo.documentos) == 1 and len(repo.objetos) == 1
    assert manifest_local(raiz)["modo"] == "dev"
    assert not (raiz / "data" / "sources").exists()


def test_modo_dev_sem_referencia_para_com_mensagem_clara(raiz, repo):
    (raiz / "tests" / "fixtures" / "referencia" / "copom.pdf").unlink()
    assert rodar(raiz, repo, dev=True) == 1
    (run,) = repo.runs.values()
    assert run["status"] == "abortado" and "copom.pdf" in run["erro_mensagem"]
    assert not repo.objetos


def test_modo_dev_com_hash_diferente_para_com_hashes(raiz, repo):
    (raiz / "tests" / "fixtures" / "referencia" / "copom.pdf").write_bytes(PDF + b"x")
    assert rodar(raiz, repo, dev=True) == 1
    (run,) = repo.runs.values()
    esperado = hashlib.sha256(PDF).hexdigest()
    assert esperado in run["erro_mensagem"]
    assert hashlib.sha256(PDF + b"x").hexdigest() in run["erro_mensagem"]
    assert not repo.objetos


# Falhas antes e depois do run existir


def test_config_invalida_nao_cria_linha_em_runs(raiz, repo):
    (raiz / "config" / "models.yaml").write_text("", encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="models.yaml"):
        rodar(raiz, repo, dev=True)
    assert not repo.runs and not (raiz / "data").exists()


def test_supabase_fora_no_aborto_deixa_o_erro_so_no_manifest_local(raiz, repo, capsys):
    (raiz / "tests" / "fixtures" / "referencia" / "copom.pdf").unlink()  # o --dev vai abortar
    repo.falhas["abortar_run"] = [FalhaRetriavel("fora")] * 10
    assert rodar(raiz, repo, dev=True) == 1  # sem nova exceção escondendo a original
    (run,) = repo.runs.values()
    assert run["status"] == "em_andamento" and "erro_classe" not in run
    local = manifest_local(raiz)
    assert local["status"] == "em_andamento" and local["erro_classe"] == "terminal"
    assert "copom.pdf" in local["erro_mensagem"] and "não gravado no Supabase" in local["erro_mensagem"]
    assert "copom.pdf" in capsys.readouterr().err


def test_run_concluido_nao_e_sobrescrito_pelo_aborto(raiz, repo):
    assert rodar(raiz, repo, dev=True) == 0
    (run_id, run), = repo.runs.items()
    manifest = run["manifest"]
    persistencia.abortar_run(repo, raiz / "data", manifest, "terminal", "tarde demais")
    assert run["status"] == "concluido" and "erro_classe" not in run


def test_run_abortado_mantem_a_primeira_mensagem(raiz, repo):
    (raiz / "tests" / "fixtures" / "referencia" / "copom.pdf").unlink()
    rodar(raiz, repo, dev=True)
    (run,) = repo.runs.values()
    primeira = run["erro_mensagem"]
    persistencia.abortar_run(repo, raiz / "data", run["manifest"], "terminal", "outra")
    assert run["erro_mensagem"] == primeira


def test_credenciais_ausentes_param_antes_de_tudo():
    with pytest.raises(FalhaTerminal, match="SUPABASE_URL.*SUPABASE_SERVICE_ROLE_KEY.*SUPABASE_DB_URL"):
        principal.persistencia.conferir_credenciais({})


def test_cli_recusa_fonte_desconhecida():
    with pytest.raises(SystemExit):
        principal._ler_parametros(["xyz"])
    assert principal._ler_parametros(["cvm", "--dev"]).dev is True


# E1 no grafo


def test_modo_dev_grava_o_documento_processado(raiz, repo):
    assert rodar(raiz, repo, dev=True) == 0
    (run_id,) = repo.runs
    assert repo.paginas and repo.chunks and repo.numeros
    assert {chave[0] for chave in repo.paginas} == {run_id}
    documento = json.loads((raiz / "data" / "runs" / run_id / "documento.json").read_text(encoding="utf-8"))
    assert documento["tipo_documento"] == "copom_ata" and documento["data_documento"] == "2026-09-16"


def test_modo_normal_tambem_passa_pela_e1(raiz, repo):
    assert rodar(raiz, repo, dev=False, http=cliente(listagem("copom"), fonte="copom")) == 0
    assert repo.paginas and repo.numeros


def test_e1_com_hash_diferente_aborta_o_run_sem_gravar_nada(raiz, repo, monkeypatch):
    original = graph.referencia

    def referencia_com_hash_errado(state, config):
        resultado = original(state, config)
        resultado["documento_fonte"] = resultado["documento_fonte"].model_copy(update={"doc_sha256": "0" * 64})
        return resultado

    monkeypatch.setattr(graph, "referencia", referencia_com_hash_errado)
    assert rodar(raiz, repo, dev=True) == 1
    (run,) = repo.runs.values()
    assert run["status"] == "abortado" and run["erro_classe"] == "terminal"
    assert "SHA-256" in run["erro_mensagem"] and "Apague" in run["erro_mensagem"]
    assert not repo.paginas and not repo.numeros
    assert not list((raiz / "data" / "runs").glob("*/documento.json"))


# __main__: fonte opcional


def test_sem_fonte_o_cli_roda_as_tres(monkeypatch, repo):
    from contextlib import nullcontext

    chamadas = []

    def falso(fonte, **kwargs):
        chamadas.append((fonte, kwargs["dev"]))
        return 1 if fonte == "cvm" else 0

    monkeypatch.setattr(principal, "executar", falso)
    monkeypatch.setattr(principal.llm, "criar_cliente", lambda raiz, ambiente: None)
    monkeypatch.setenv("GOOGLE_API_KEY", "chave-de-teste")
    monkeypatch.setattr(principal.persistencia, "criar_repositorio_supabase", lambda ambiente: repo)
    monkeypatch.setattr(principal.persistencia, "checkpointer_postgres", lambda url: nullcontext(None))
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://localhost/x")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert principal.main(["--dev"]) == 1  # a CVM falhou, mas as outras rodaram
    assert chamadas == [("copom", True), ("cvm", True), ("b3", True)]
    chamadas.clear()
    assert principal.main(["b3"]) == 0
    assert chamadas == [("b3", False)]


def test_sem_chave_do_gemini_para_antes_de_criar_o_run(monkeypatch, repo):
    from contextlib import nullcontext

    monkeypatch.setattr(principal.persistencia, "criar_repositorio_supabase", lambda ambiente: repo)
    monkeypatch.setattr(principal.persistencia, "checkpointer_postgres", lambda url: nullcontext(None))
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://localhost/x")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert principal.main(["copom", "--dev"]) == 1
    assert not repo.runs


# E2 e E3 no grafo


def test_modo_dev_assina_e_grava_o_factsheet(raiz, repo):
    llm = cliente_falso([RESPOSTA_COPOM])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    (run_id, run), = repo.runs.items()
    (factsheet,) = repo.factsheets.values()
    assert factsheet.assinado and [c.id for c in factsheet.claims] == ["C01"]
    (c,) = factsheet.claims
    assert c.ancora.pagina == 5 and [n.bruto for n in c.numeros] == ["13,75%"]
    assert repo.validacoes == {(run_id, 1, "C01"): {"passou": True, "motivo": None}}
    (span,) = repo.spans.values()
    assert span.no == "extractor" and span.falha_classe is None
    assert run["custo_total_tokens"] == 1200 and run["custo_total_brl"] == span.custo_brl
    pasta = raiz / "data" / "runs" / run_id
    local = json.loads((pasta / "factsheet.json").read_text(encoding="utf-8"))
    assert local["assinado"] and "tabela_numeros" not in local
    assert (pasta / "validacoes" / "1.json").exists()
    assert (pasta / "spans" / f"{span.span_id}.json").exists()
    manifest = manifest_local(raiz)
    assert manifest["custo_total_tokens"] == 1200 and manifest["custo_total_brl"] == str(span.custo_brl)


def test_claim_recusado_volta_para_o_extractor_e_e_corrigido(raiz, repo):
    parafraseado = claim("O Copom cortou a Selic para 13,75% ao ano.", ["13,75%"])
    llm = cliente_falso([json.dumps({"claims": [parafraseado]}), RESPOSTA_COPOM])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    (run_id,) = repo.runs
    assert not repo.validacoes[(run_id, 1, "C01")]["passou"]
    assert "não foi encontrado" in repo.validacoes[(run_id, 1, "C01")]["motivo"]
    assert repo.validacoes[(run_id, 2, "C01")]["passou"]
    segundo_prompt = llm._provider.prompts[1]
    assert "# Correção" in segundo_prompt and "O Copom cortou a Selic" in segundo_prompt
    (factsheet,) = repo.factsheets.values()
    assert factsheet.claims[0].literal_fonte.startswith("O Copom decidiu reduzir")


def test_claim_que_nao_converge_aborta_o_run_depois_de_3_rodadas(raiz, repo):
    errado = json.dumps({"claims": [claim("O Copom decidiu reduzir a taxa básica de juros", ["13,75%"])]})
    llm = cliente_falso([errado] * 3)
    assert rodar(raiz, repo, dev=True, llm=llm) == 1
    (run_id, run), = repo.runs.items()
    assert run["status"] == "abortado" and run["erro_classe"] == "terminal"
    assert "3 rodadas" in run["erro_mensagem"] and "C01" in run["erro_mensagem"] and "13,75%" in run["erro_mensagem"]
    assert {chave[1] for chave in repo.validacoes} == {1, 2, 3}
    assert not repo.factsheets
    assert len(repo.spans) == 3
    assert manifest_local(raiz)["custo_total_tokens"] == 3600


def test_extractor_sem_claims_aborta_o_run(raiz, repo):
    assert rodar(raiz, repo, dev=True, llm=cliente_falso(['{"claims": []}'])) == 1
    (run,) = repo.runs.values()
    assert "nenhum claim" in run["erro_mensagem"]


# __main__: linhas de etapa no terminal


def etapas(saida):
    return [linha for linha in saida.splitlines() if linha.startswith("etapa ")]


def test_run_escreve_inicio_e_fim_de_cada_etapa(raiz, repo, capsys):
    parafraseado = claim("O Copom cortou a Selic para 13,75% ao ano.", ["13,75%"])
    llm = cliente_falso([json.dumps({"claims": [parafraseado]}), RESPOSTA_COPOM])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    assert etapas(capsys.readouterr().out) == [
        f"etapa {no}: {estado}"
        for no in ("referencia", "leitura", "extract", "validate", "extract", "validate")
        for estado in ("iniciada", "concluída")
    ]


def test_etapa_que_falha_fica_sem_a_linha_de_fim(raiz, repo, capsys):
    assert rodar(raiz, repo, dev=False, http=cliente({})) == 1
    assert etapas(capsys.readouterr().out) == ["etapa coleta: iniciada"]
