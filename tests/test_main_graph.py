"""Testes do manifest, do grafo e do __main__: repositório em memória e tmp_path."""

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from conftest import RepositorioMemoria, cliente_falso, resposta_plano
from sunontent import __main__ as principal
from sunontent import graph, manifesto, persistencia
from sunontent.retentativa import FalhaRetriavel, FalhaTerminal
from test_ingestao import cliente, listagem

pytestmark = pytest.mark.usefixtures("celulas_aprovadas")  # as 9 células aprovadas sem LLM; o fan-out está em test_fanout.py

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
PLANO = resposta_plano("C01")


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
        "copom", dev=dev, repositorio=repo,
        cliente_llm=llm or cliente_falso([RESPOSTA_COPOM, PLANO]), raiz=raiz,
        cliente_http=http, relogio=lambda: MOMENTO,
    )


def manifest_local(raiz):
    (arquivo,) = (raiz / "data" / "runs").glob("*/manifest.json")
    return json.loads(arquivo.read_text(encoding="utf-8"))


# Manifest


def versao_prompt(raiz, nome):
    cabecalho = (raiz / "prompts" / f"{nome}.j2").read_text(encoding="utf-8").splitlines()[0]
    return int(cabecalho.split("versao:")[1].split("#")[0])


def test_manifest_le_versoes_e_hashes_da_config(raiz):
    m = manifesto.montar(raiz, run_id="copom_20261006T150000Z_aaaaaa", timestamp=MOMENTO,
                         modo="normal", fonte="copom", k=2, max_concorrencia=3)
    assert m.level_specs == {
        n: yaml.safe_load((raiz / "config" / "levels" / f"{n}.yaml").read_text(encoding="utf-8"))["versao"]
        for n in ("iniciante", "intermediario", "avancado")}
    glossario = yaml.safe_load((raiz / "config" / "glossario.yaml").read_text(encoding="utf-8"))
    assert m.glossario_versao == glossario["versao"]
    assert m.prompts == {
        "adapter": versao_prompt(raiz, "adapter"), "extractor": versao_prompt(raiz, "extractor"),
        "synthesizer": versao_prompt(raiz, "synthesizer"), "judge": versao_prompt(raiz, "judge"),
    }
    assert set(m.arquivos_sha256) == {
        "config/models.yaml", "config/glossario.yaml", "prompts/extractor.j2", "prompts/adapter.j2",
        "prompts/synthesizer.j2", "prompts/judge.j2", "config/levels/iniciante.yaml", "config/levels/intermediario.yaml", "config/levels/avancado.yaml",
    }


def test_hash_ignora_fim_de_linha(raiz):
    def montar():
        return manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2, max_concorrencia=3)

    arquivo = raiz / "config" / "glossario.yaml"
    antes = montar().arquivos_sha256["config/glossario.yaml"]
    arquivo.write_bytes(arquivo.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert montar().arquivos_sha256["config/glossario.yaml"] == antes


def test_template_com_conteudo_exige_cabecalho_de_versao(raiz):
    (raiz / "prompts" / "adapter.j2").write_text("sem cabecalho", encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="prompts/adapter.j2"):
        manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2, max_concorrencia=3)


def test_template_com_cabecalho_entra_no_manifest(raiz):
    (raiz / "prompts" / "adapter.j2").write_text("{# versao: 3 #}\nolá", encoding="utf-8")
    m = manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2, max_concorrencia=3)
    assert m.prompts == {
        "adapter": 3, "extractor": versao_prompt(raiz, "extractor"), "synthesizer": versao_prompt(raiz, "synthesizer"),
        "judge": versao_prompt(raiz, "judge"),
    }
    assert "prompts/adapter.j2" in m.arquivos_sha256


def test_yaml_vazio_aponta_o_arquivo(raiz):
    (raiz / "config" / "levels" / "avancado.yaml").write_text("", encoding="utf-8")
    with pytest.raises(FalhaTerminal, match="config/levels/avancado.yaml"):
        manifesto.montar(raiz, run_id="x", timestamp=MOMENTO, modo="normal", fonte="copom", k=2, max_concorrencia=3)


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
    assert local["K"] == graph.K and local["max_concorrencia"] == graph.MAX_CONCORRENCIA == 3
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
    persistencia.abortar_run(repo, raiz / "data", manifest, "terminal", "tarde demais", "adapt")
    assert run["status"] == "concluido" and "erro_classe" not in run


def test_varredura_aborta_so_o_run_antigo_em_andamento(raiz, repo):
    assert rodar(raiz, repo, dev=True) == 0
    (antigo_id, antigo), = repo.runs.items()
    antigo.update(status="em_andamento")
    agora = antigo["manifest"].timestamp + persistencia.IDADE_ORFAO + timedelta(minutes=1)
    novo = antigo["manifest"].model_copy(update={"run_id": "copom_20990101T000000Z_aaaaaa", "timestamp": agora})
    repo.criar_run(novo)
    assert persistencia.abortar_orfaos(repo, raiz / "data", agora) == [antigo_id]
    assert antigo["status"] == "abortado" and antigo["erro_classe"] == "terminal"
    assert antigo["erro_mensagem"] == persistencia.MENSAGEM_ORFAO
    assert manifest_local(raiz)["status"] == "abortado"
    assert repo.runs[novo.run_id]["status"] == "em_andamento"


def test_run_abortado_mantem_a_primeira_mensagem(raiz, repo):
    (raiz / "tests" / "fixtures" / "referencia" / "copom.pdf").unlink()
    rodar(raiz, repo, dev=True)
    (run,) = repo.runs.values()
    primeira = run["erro_mensagem"], run["etapa_erro"]
    assert primeira[1] == "referencia" and manifest_local(raiz)["etapa_erro"] == "referencia"
    persistencia.abortar_run(repo, raiz / "data", run["manifest"], "terminal", "outra", "adapt")
    assert (run["erro_mensagem"], run["etapa_erro"]) == primeira


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
    chamadas = []

    def falso(fonte, **kwargs):
        chamadas.append((fonte, kwargs["dev"]))
        return 1 if fonte == "cvm" else 0

    monkeypatch.setattr(principal, "executar", falso)
    monkeypatch.setattr(principal.llm, "criar_cliente", lambda raiz, ambiente: None)
    monkeypatch.setenv("GOOGLE_API_KEY_PRINC", "chave-de-teste")
    monkeypatch.setenv("GROQ_API_KEY", "chave-de-teste")
    monkeypatch.setattr(principal.persistencia, "criar_repositorio_supabase", lambda ambiente: repo)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://localhost/x")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert principal.main(["--dev"]) == 1  # a CVM falhou, mas as outras rodaram
    assert chamadas == [("copom", True), ("cvm", True), ("b3", True)]
    chamadas.clear()
    assert principal.main(["b3"]) == 0
    assert chamadas == [("b3", False)]


def preparar_main(monkeypatch, repo):
    monkeypatch.setenv("GOOGLE_API_KEY_PRINC", "chave-de-teste")
    monkeypatch.setenv("GROQ_API_KEY", "chave-de-teste")
    monkeypatch.setattr(principal.persistencia, "criar_repositorio_supabase", lambda ambiente: repo)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://localhost/x")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)


def test_falha_fora_do_grafo_nao_interrompe_as_fontes_seguintes(monkeypatch, repo, capsys):
    chamadas = []

    def falso(fonte, **kwargs):
        chamadas.append(fonte)
        if fonte == "cvm":
            raise FalhaTerminal("Supabase recusou a criação do run")
        return 0

    monkeypatch.setattr(principal, "executar", falso)
    monkeypatch.setattr(principal.llm, "criar_cliente", lambda raiz, ambiente: None)
    preparar_main(monkeypatch, repo)
    assert principal.main(["--dev"]) == 1
    assert chamadas == ["copom", "cvm", "b3"]
    assert "erro (terminal): cvm: Supabase recusou a criação do run" in capsys.readouterr().err.splitlines()


def test_falha_ao_criar_o_cliente_llm_pula_so_a_fonte(monkeypatch, repo, capsys):
    chamadas, criados = [], []

    def criar_cliente(raiz, ambiente):
        criados.append(None)
        if len(criados) == 2:
            raise FalhaTerminal("config/models.yaml: YAML inválido")

    monkeypatch.setattr(principal, "executar", lambda fonte, **kwargs: chamadas.append(fonte) or 0)
    monkeypatch.setattr(principal.llm, "criar_cliente", criar_cliente)
    preparar_main(monkeypatch, repo)
    assert principal.main(["--dev"]) == 1
    assert chamadas == ["copom", "b3"]
    linha = "erro (terminal): cvm: config/models.yaml: YAML inválido"
    assert linha in capsys.readouterr().err.splitlines()


def test_falha_ao_gravar_o_manifest_inicial_aborta_o_run_sem_etapa(raiz, repo, monkeypatch, capsys):
    original = persistencia.gravar_manifest_local

    def gravar(raiz_dados, manifest, status, *args, **kwargs):
        if status == "em_andamento":
            raise FalhaTerminal("disco cheio")
        return original(raiz_dados, manifest, status, *args, **kwargs)

    monkeypatch.setattr(persistencia, "gravar_manifest_local", gravar)
    assert rodar(raiz, repo, dev=True) == 1
    (run,) = repo.runs.values()
    assert run["status"] == "abortado" and run["erro_classe"] == "terminal"
    assert run["erro_mensagem"] == "disco cheio" and run["etapa_erro"] is None
    local = manifest_local(raiz)
    assert local["status"] == "abortado" and local["etapa_erro"] is None
    assert "abortado (terminal): disco cheio" in capsys.readouterr().err


def test_sem_chave_do_gemini_para_antes_de_criar_o_run(monkeypatch, repo):
    monkeypatch.setattr(principal.persistencia, "criar_repositorio_supabase", lambda ambiente: repo)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://localhost/x")
    monkeypatch.delenv("GOOGLE_API_KEY_PRINC", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY_SEC", "reserva")  # a reserva sozinha não basta
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert principal.main(["copom", "--dev"]) == 1
    assert not repo.runs


# E2 e E3 no grafo


def test_modo_dev_assina_e_grava_o_factsheet(raiz, repo):
    llm = cliente_falso([RESPOSTA_COPOM, PLANO])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    (run_id, run), = repo.runs.items()
    factsheet = repo.factsheet_gravado(run_id)
    assert factsheet.assinado and [c.id for c in factsheet.claims] == ["C01"]
    (c,) = factsheet.claims
    assert c.ancora.pagina == 5 and [n.bruto for n in c.numeros] == ["13,75%"]
    assert repo.validacoes == {(run_id, 1, "C01"): {"passou": True, "motivo": None}}
    span, span_plano = repo.spans.values()
    assert span.no == "extractor" and span.falha_classe is None and span_plano.no == "adapter"
    assert run["custo_total_tokens"] == 2400 and run["custo_total_brl"] == span.custo_brl + span_plano.custo_brl
    pasta = raiz / "data" / "runs" / run_id
    local = json.loads((pasta / "factsheet.json").read_text(encoding="utf-8"))
    assert local["assinado"] and "tabela_numeros" not in local
    assert (pasta / "validacoes" / "1.json").exists()
    assert (pasta / "spans" / f"{span.span_id}.json").exists()
    manifest = manifest_local(raiz)
    assert manifest["custo_total_tokens"] == 2400
    assert manifest["custo_total_brl"] == str(span.custo_brl + span_plano.custo_brl)


def test_claim_recusado_volta_para_o_extractor_e_e_corrigido(raiz, repo):
    parafraseado = claim("O Copom cortou a Selic para 13,75% ao ano.", ["13,75%"])
    llm = cliente_falso([json.dumps({"claims": [parafraseado]}), RESPOSTA_COPOM, PLANO])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    (run_id,) = repo.runs
    assert not repo.validacoes[(run_id, 1, "C01")]["passou"]
    assert "não foi encontrado" in repo.validacoes[(run_id, 1, "C01")]["motivo"]
    assert repo.validacoes[(run_id, 2, "C01")]["passou"]
    segundo_prompt = llm._provider.prompts[1]
    assert "# Correção" in segundo_prompt and "O Copom cortou a Selic" in segundo_prompt
    factsheet = repo.factsheet_gravado(next(iter(repo.factsheets)))
    assert factsheet.claims[0].literal_fonte.startswith("O Copom decidiu reduzir")


ERRADO = claim("O Copom decidiu reduzir a taxa básica de juros", ["13,75%"])  # o número está fora do trecho
VOTACAO = {
    **claim("Votaram por essa decisão os seguintes membros do Comitê", tipo="decisao"),
    "id": "C02", "texto": "A decisão foi unânime.", "tags": ["governanca"],
}


def test_claim_que_nao_converge_e_descartado_e_o_run_conclui(raiz, repo, capsys):
    correcao = json.dumps({"claims": [ERRADO]}, ensure_ascii=False)
    llm = cliente_falso([json.dumps({"claims": [ERRADO, VOTACAO]}, ensure_ascii=False), correcao, correcao,
                         resposta_plano("C02")])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    (run_id, run), = repo.runs.items()
    assert run["status"] == "concluido"
    assert {chave[1] for chave in repo.validacoes} == {1, 2, 3}
    factsheet = repo.factsheet_gravado(next(iter(repo.factsheets)))
    assert factsheet.assinado and [c.id for c in factsheet.claims] == ["C02"]
    # O motivo do descarte é a linha da última rodada em validacoes_ancora.
    ultima = repo.validacoes[(run_id, 3, "C01")]
    assert not ultima["passou"] and "13,75%" in ultima["motivo"]
    saida = capsys.readouterr().out
    assert "1 claims, 1 descartado," in saida
    assert etapas(saida)[-6:] == ["etapa descarte: iniciada", "etapa descarte: concluída",
                                  "etapa adapt: iniciada", "etapa adapt: concluída",
                                  "etapa fechamento: iniciada", "etapa fechamento: concluída"]


def test_sem_claim_valido_aborta(raiz, repo):
    errado = json.dumps({"claims": [ERRADO]}, ensure_ascii=False)
    assert rodar(raiz, repo, dev=True, llm=cliente_falso([errado] * 3)) == 1
    (run_id, run), = repo.runs.items()
    assert run["status"] == "abortado" and run["erro_classe"] == "terminal"
    assert "nenhum claim válido" in run["erro_mensagem"] and "1 claims descartados. C01:" in run["erro_mensagem"]
    assert run["etapa_erro"] == "descarte"
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
    llm = cliente_falso([json.dumps({"claims": [parafraseado]}), RESPOSTA_COPOM, PLANO])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    assert etapas(capsys.readouterr().out) == [
        f"etapa {no}: {estado}"
        for no in ("referencia", "leitura", "extract", "validate", "extract", "validate", "adapt", "fechamento")
        for estado in ("iniciada", "concluída")
    ]


def test_etapa_que_falha_fica_sem_a_linha_de_fim(raiz, repo, capsys):
    assert rodar(raiz, repo, dev=False, http=cliente({})) == 1
    assert etapas(capsys.readouterr().out) == ["etapa coleta: iniciada"]


# E4 no grafo


def plano_com(nucleo, claims):
    return resposta_plano(*claims, nucleo=nucleo)


def test_e4_grava_o_plano_e_as_9_celulas_pendentes(raiz, repo, capsys):
    assert rodar(raiz, repo, dev=True) == 0
    (run_id, run), = repo.runs.items()
    assert run["status"] == "concluido"
    plano = repo.plano_gravado(run_id)
    assert plano.nucleo == ["C01"] and plano.rodadas == 1 and plano.aviso is None
    assert [p.nivel for p in plano.niveis] == ["iniciante", "intermediario", "avancado"]
    analogias = plano.nivel("iniciante").analogias
    assert {a.glossario_ref.split("#")[0] for a in analogias} == {"copom", "selic"}
    assert all(a.origem == "glossario" and not a.revisada for a in analogias)
    assert plano.nivel("avancado").analogias == []
    assert sorted(c for r, c in repo.celulas if r == run_id) == sorted(
        f"{n}__{f}" for n in ("iniciante", "intermediario", "avancado") for f in ("artigo", "carrossel", "roteiro"))
    assert {(c.estado, c.tentativa) for c in repo.celulas.values()} == {("PENDENTE", 0)}
    assert not repo.validacoes_plano  # a rodada que passa não grava linha
    pasta = raiz / "data" / "runs" / run_id
    rodada_1 = json.loads((pasta / "validacoes_plano" / "1.json").read_text(encoding="utf-8"))
    assert rodada_1 == {"rodada": 1, "problemas": [], "saida": json.loads(PLANO)}
    assert repo.rodadas_plano == {(run_id, 1): json.loads(PLANO)}
    assert json.loads((pasta / "plano.json").read_text(encoding="utf-8"))["nucleo"] == ["C01"]
    assert (pasta / "celulas" / "avancado__roteiro" / "estado.json").exists()
    (span_plano,) = [s for s in repo.spans.values() if s.no == "adapter"]
    assert span_plano.nivel is None and span_plano.celula_id is None and span_plano.rodada == 1
    saida = capsys.readouterr().out
    assert etapas(saida)[-4:-2] == ["etapa adapt: iniciada", "etapa adapt: concluída"]
    assert "1 claims, plano em 1 rodada, 9 células (9 APROVADA), 2400 tokens" in saida


def test_e4_devolve_so_os_problemas_ao_planejador(raiz, repo, capsys):
    com_problema = plano_com(["C01"], ["C01", "C99"])
    llm = cliente_falso([RESPOSTA_COPOM, com_problema, PLANO])
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    (run_id,) = repo.runs
    assert repo.plano_gravado(run_id).rodadas == 2 and repo.plano_gravado(run_id).aviso is None
    assert sorted(repo.rodadas_plano) == [(run_id, 1), (run_id, 2)]
    assert [s.rodada for s in repo.spans.values() if s.no == "adapter"] == [1, 2]
    linhas = sorted(repo.validacoes_plano.items())
    assert [(chave[1], p.regra, p.nivel) for chave, p in linhas] == [
        (1, "id_inexistente", "iniciante"), (1, "id_inexistente", "intermediario"), (1, "id_inexistente", "avancado")]
    correcao = llm._provider.prompts[2]
    problemas = correcao.split("## Problemas", 1)[1].split("## Plano enviado", 1)[0]
    assert problemas.strip().splitlines() == [
        f"- [id_inexistente] {n}: C99 nos claims não existe no FactSheet"
        for n in ("iniciante", "intermediario", "avancado")]
    assert llm._provider.raciocinios == [None, None, "low"]
    assert "plano em 2 rodadas," in capsys.readouterr().out


def test_e4_com_problema_depois_da_3a_rodada_segue_com_aviso(raiz, repo, capsys):
    com_problema = plano_com(["C01"], ["C01", "C99"])
    llm = cliente_falso([RESPOSTA_COPOM] + [com_problema] * 3)
    assert rodar(raiz, repo, dev=True, llm=llm) == 0
    (run_id,) = repo.runs
    plano = repo.plano_gravado(run_id)
    assert plano.rodadas == 3
    assert plano.aviso == "; ".join(f"[id_inexistente] {n}: C99 saiu dos claims"
                                    for n in ("iniciante", "intermediario", "avancado"))
    assert all([c.claim_id for c in p.claims] == ["C01"] for p in plano.niveis)  # o C99 foi retirado
    assert {chave[1] for chave in repo.validacoes_plano} == {1, 2, 3}
    assert len(repo.celulas) == 9
    assert "plano em 3 rodadas (com aviso)," in capsys.readouterr().out


def test_e4_nucleo_vazio_depois_da_retirada_aborta_e_grava_as_rodadas(raiz, repo):
    sem_nucleo = plano_com(["C99"], ["C01"])
    assert rodar(raiz, repo, dev=True, llm=cliente_falso([RESPOSTA_COPOM] + [sem_nucleo] * 3)) == 1
    (run_id, run), = repo.runs.items()
    assert run["status"] == "abortado" and run["erro_classe"] == "terminal" and run["etapa_erro"] == "adapt"
    assert "o núcleo ficou vazio" in run["erro_mensagem"]
    assert manifest_local(raiz)["etapa_erro"] == "adapt"
    assert {chave[1] for chave in repo.validacoes_plano} == {1, 2, 3}
    assert not repo.planos and not repo.celulas


@pytest.mark.parametrize("arquivo, conteudo, mensagem", [
    ("config/levels/iniciante.yaml", "nivel: iniciante\nversao: 2\nfoco: x\ntags_priorizadas: []\n"
     "tags_descartaveis: [governanca]\nformatos: {}\n", "iniciante.yaml: LevelSpec inválido"),
    ("config/glossario.yaml", "versao: 1\ntermos:\n  - id: x\n", "config/glossario.yaml: glossário inválido"),
])
def test_e4_config_invalida_aborta_antes_do_modelo(raiz, repo, arquivo, conteudo, mensagem):
    (raiz / arquivo).write_text(conteudo, encoding="utf-8")
    llm = cliente_falso([RESPOSTA_COPOM])
    assert rodar(raiz, repo, dev=True, llm=llm) == 1
    (run,) = repo.runs.values()
    assert run["erro_classe"] == "terminal" and mensagem in run["erro_mensagem"]
    assert len(llm._provider.prompts) == 1  # só o extractor
    assert repo.factsheets and not repo.planos


def test_e4_falha_do_modelo_aborta_o_run(raiz, repo):
    llm = cliente_falso([RESPOSTA_COPOM, '{"nucleo": []}', '{"nucleo": []}', '{"nucleo": []}'])
    assert rodar(raiz, repo, dev=True, llm=llm) == 1
    (run,) = repo.runs.values()
    assert "adapter (modelo-falso): 3 respostas fora do schema" in run["erro_mensagem"]


def _estado_e_config(raiz, repo, llm):
    """Estado de um run concluído até a E3, para chamar o nó adapt direto."""
    from sunontent.nodes.adapt import adapt
    from sunontent.schemas import PipelineState

    assert rodar(raiz, repo, dev=True) == 0
    (run_id,) = repo.runs
    manifest = repo.runs[run_id]["manifest"]
    estado = PipelineState(run_id=run_id, manifest=manifest, factsheet=repo.factsheet_gravado(run_id))
    config = {"configurable": {"repositorio": repo, "raiz": raiz, "raiz_dados": raiz / "data", "llm": llm}}
    return adapt, estado, config, run_id


def test_e4_sem_candidato_nos_3_niveis_aborta_antes_do_modelo(raiz, repo):
    llm = cliente_falso([])
    adapt, estado, config, run_id = _estado_e_config(raiz, repo, llm)
    del repo.planos[run_id]
    so_metodologico = estado.factsheet.model_copy(update={"claims": [
        c.model_copy(update={"tags": ["metodologico"]}) for c in estado.factsheet.claims]})
    with pytest.raises(FalhaTerminal, match="nenhum claim é candidato nos 3 níveis"):
        adapt(estado.model_copy(update={"factsheet": so_metodologico}), config, rodadas=3)
    assert llm._provider.prompts == []


def test_e4_apaga_as_rodadas_antigas_do_run(raiz, repo):
    from sunontent.schemas import ProblemaPlano

    llm = cliente_falso([PLANO])
    adapt, estado, config, run_id = _estado_e_config(raiz, repo, llm)
    del repo.planos[run_id]
    repo.celulas.clear()
    repo.validacoes_plano[(run_id, 2, 1)] = ProblemaPlano(regra="contraditorio", nivel="avancado", detalhe="antiga")
    saida = adapt(estado, config, rodadas=3)
    assert saida["rodada_plano"] == 1
    assert not repo.validacoes_plano and repo.plano_gravado(run_id).rodadas == 1
    assert len(llm._provider.prompts) == 1
