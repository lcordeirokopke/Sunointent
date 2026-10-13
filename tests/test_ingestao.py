"""Testes de unidade da E0: HTTP simulado, repositório em memória e tmp_path no lugar de data/."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from conftest import FIXTURES, RepositorioMemoria, referencia
from sunontent import persistencia
from sunontent.ingestao import b3, comum, copom, cvm
from sunontent.nodes.coleta import coleta as no_coleta
from sunontent.retentativa import TENTATIVAS, FalhaRetriavel, FalhaTerminal

HTTP = FIXTURES / "http"
COLETADO_EM = datetime(2026, 10, 4, 14, 22, 5, tzinfo=timezone.utc)
# Corpo usado enquanto tests/fixtures/referencia/<fonte>.pdf não existe (pendência do congelamento).
SUBSTITUTO_PDF = b"%PDF-1.7\n% substituto ate o congelamento da referencia\n%%EOF\n"

URL_PDF = {
    "copom": "https://www.bcb.gov.br/content/copom/atascopom/Copom281-not20260916281.pdf",
    "cvm": (
        "https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?Tela=ext&descTipo=IPE"
        "&CodigoInstituicao=1&numProtocolo=1575717&numSequencia=1100423&numVersao=1"
    ),
    "b3": (
        "https://api.mziq.com/mzfilemanager/v2/d/5fd7b7d8-54a1-472d-8426-eb896ad8a3c4/"
        "b6643170-1686-52a1-2eda-a7817cf64c2d?origin=2"
    ),
}
DOC_ID = {
    "copom": "copom_ata_281",
    "cvm": "cvm_fato_relevante_16993_1575717",
    "b3": "b3_release_2026_2T",
}
SCRIPTS = {"copom": copom, "cvm": cvm, "b3": b3}
FONTES = list(SCRIPTS)


def corpo_pdf(fonte: str) -> bytes:
    ref = referencia(fonte)
    return ref[0] if ref else SUBSTITUTO_PDF


def ipe_zip() -> bytes:
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as arquivo:
        arquivo.writestr("ipe_cia_aberta_2026.csv", (HTTP / "cvm" / "ipe_cia_aberta.csv").read_bytes())
    return buffer.getvalue()


def pdf_ok(fonte: str):
    # A CVM serve o PDF como text/html: o Content-Type é ignorado.
    tipo = "text/html" if fonte == "cvm" else "application/pdf"
    return lambda req: httpx.Response(200, content=corpo_pdf(fonte), headers={"Content-Type": tipo})


def listagem(fonte: str) -> dict:
    """Respostas da listagem de cada fonte, chaveadas por URL sem query."""
    if fonte == "copom":
        return {
            copom.URL_ATAS: lambda req: httpx.Response(
                200, content=(HTTP / "copom" / "atas.json").read_bytes()),
            copom.URL_ATA_DETALHES: lambda req: httpx.Response(
                200, content=(HTTP / "copom" / "atas_detalhes.json").read_bytes()),
        }
    if fonte == "cvm":
        return {cvm.URL_IPE.format(ano=2026): lambda req: httpx.Response(200, content=ipe_zip())}
    return {b3.URL_ARQUIVOS: lambda req: httpx.Response(
        200, content=(HTTP / "b3" / "releases.json").read_bytes())}


def cliente(rotas: dict, pdf=None, fonte: str | None = None) -> httpx.Client:
    """Cliente HTTP simulado. `pdf` responde à URL do PDF da fonte."""
    rotas = dict(rotas)
    if fonte is not None:
        rotas[URL_PDF[fonte].split("?")[0]] = pdf or pdf_ok(fonte)
    chamadas = []

    def tratar(req: httpx.Request) -> httpx.Response:
        chave = str(req.url.copy_with(query=None))
        chamadas.append(chave)
        if chave not in rotas:
            return httpx.Response(404, text="não simulado")
        return rotas[chave](req)

    http = httpx.Client(transport=httpx.MockTransport(tratar), headers={"User-Agent": comum.USER_AGENT})
    http.chamadas = chamadas
    return http


def sequencia(*respostas):
    """Rota que devolve uma resposta (ou levanta uma exceção) por chamada."""
    fila = list(respostas)

    def rota(req):
        item = fila.pop(0) if len(fila) > 1 else fila[0]
        if isinstance(item, Exception):
            raise item
        return item(req) if callable(item) else item

    return rota


def coletar(fonte, repositorio, run_id, tmp_path, http):
    return SCRIPTS[fonte].coletar(
        run_id=run_id, repositorio=repositorio, raiz_dados=tmp_path,
        cliente_http=http, relogio=lambda: COLETADO_EM,
    )


def nada_gravado(repositorio: RepositorioMemoria, tmp_path: Path) -> bool:
    return (not repositorio.objetos and not repositorio.documentos and not repositorio.coletas
            and not (tmp_path / "sources").exists())


# Caminho feliz


@pytest.mark.parametrize("fonte", FONTES)
def test_coleta_grava_supabase_depois_copia_local(fonte, repositorio, run_id, tmp_path):
    corpo = corpo_pdf(fonte)
    sha = comum.sha256_hex(corpo)

    doc = coletar(fonte, repositorio, run_id, tmp_path, cliente(listagem(fonte), fonte=fonte))

    assert doc.doc_id == DOC_ID[fonte]
    assert doc.doc_sha256 == sha
    assert doc.url_origem == URL_PDF[fonte]
    assert doc.coletado_em == COLETADO_EM
    assert doc.coleta_id == 1
    assert doc.caminho == tmp_path / "sources" / fonte / f"{sha}.pdf"
    assert doc.caminho.read_bytes() == corpo

    assert repositorio.objetos == {f"{fonte}/{sha}.pdf": corpo}
    assert repositorio.documentos[sha]["tipo_documento"] == SCRIPTS[fonte].TIPO_DOCUMENTO
    assert repositorio.runs[run_id]["coleta_id"] == 1

    meta = tmp_path / "sources" / fonte / f"{sha}.20261004T142205Z.json"
    assert json.loads(meta.read_text(encoding="utf-8")) == {
        "doc_id": DOC_ID[fonte],
        "doc_sha256": sha,
        "url_origem": URL_PDF[fonte],
        "coletado_em": "2026-10-04T14:22:05Z",
    }
    assert sorted(p.name for p in (tmp_path / "sources" / fonte).iterdir()) == sorted(
        [f"{sha}.pdf", meta.name])


@pytest.mark.parametrize("fonte", FONTES)
def test_referencia_bate_doc_sha256_e_doc_id(fonte, repositorio, run_id, tmp_path):
    ref = referencia(fonte)
    if ref is None:
        pytest.skip(f"tests/fixtures/referencia/{fonte}.pdf e .json ainda não congelados")
    _, meta = ref
    doc = coletar(fonte, repositorio, run_id, tmp_path, cliente(listagem(fonte), fonte=fonte))
    assert doc.doc_sha256 == meta["doc_sha256"]
    assert doc.doc_id == meta["doc_id"]


@pytest.mark.parametrize("fonte", FONTES)
def test_user_agent_explicito(fonte, repositorio, run_id, tmp_path):
    vistos = []

    def registra(rota):
        def r(req):
            vistos.append(req.headers.get("User-Agent"))
            return rota(req)
        return r

    rotas = {url: registra(rota) for url, rota in listagem(fonte).items()}
    coletar(fonte, repositorio, run_id, tmp_path, cliente(rotas, pdf=registra(pdf_ok(fonte)), fonte=fonte))
    assert vistos and set(vistos) == {comum.USER_AGENT}


# Formato e tamanho: abortam sem gravar nada


@pytest.mark.parametrize("fonte", FONTES)
def test_pagina_de_erro_persistente_aborta_sem_gravar(fonte, repositorio, run_id, tmp_path, sem_espera):
    erro = (HTTP / fonte / "erro.html").read_bytes()
    pdf = lambda req: httpx.Response(200, content=erro, headers={"Content-Type": "application/pdf"})

    with pytest.raises(FalhaTerminal, match=f"{TENTATIVAS} tentativas esgotadas.*não é PDF"):
        coletar(fonte, repositorio, run_id, tmp_path, cliente(listagem(fonte), pdf=pdf, fonte=fonte))
    assert len(sem_espera) == TENTATIVAS - 1
    assert nada_gravado(repositorio, tmp_path)


@pytest.mark.parametrize("fonte", FONTES)
def test_pagina_de_erro_temporaria_e_repetida(fonte, repositorio, run_id, tmp_path, sem_espera, capsys):
    erro = (HTTP / fonte / "erro.html").read_bytes()
    pdf = sequencia(lambda req: httpx.Response(200, content=erro), pdf_ok(fonte))

    doc = coletar(fonte, repositorio, run_id, tmp_path, cliente(listagem(fonte), pdf=pdf, fonte=fonte))
    assert doc.doc_id == DOC_ID[fonte]
    assert len(sem_espera) == 1
    assert "tentativa 1 de 4 falhou" in capsys.readouterr().err


def test_progresso_aparece_no_terminal(repositorio, run_id, tmp_path, capsys):
    def blocos():  # como na rede: o corpo chega em pedaços
        yield b"%PDF-"
        for _ in range(8):
            yield b"0" * (256 * 1024)

    pdf = lambda req: httpx.Response(200, content=blocos())
    coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), pdf=pdf, fonte="copom"))
    saida = capsys.readouterr().out
    for trecho in (
        "copom: localizando a ata mais recente",
        f"copom: baixando copom_ata_281 de {URL_PDF['copom']}",
        "copom: 1 MB baixados",
        "copom: 2 MB baixados",
        "copom: 2.0 MB baixados em",
        "copom: gravando no Supabase",
        "copom: gravando a cópia local em",
    ):
        assert trecho in saida


def test_content_type_e_ignorado_so_os_primeiros_bytes_contam(repositorio, run_id, tmp_path):
    pdf = lambda req: httpx.Response(200, content=corpo_pdf("copom"), headers={"Content-Type": "text/html"})
    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), pdf=pdf, fonte="copom"))
    assert doc.caminho.exists()


def test_content_length_acima_do_limite_aborta_sem_gravar(repositorio, run_id, tmp_path):
    grande = str(comum.LIMITE_BYTES + 1)
    pdf = lambda req: httpx.Response(200, content=b"%PDF-", headers={"Content-Length": grande})

    with pytest.raises(FalhaTerminal, match=f"{grande} bytes .* excede o limite de {comum.LIMITE_BYTES}"):
        coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), pdf=pdf, fonte="copom"))
    assert nada_gravado(repositorio, tmp_path)


def test_corpo_acima_do_limite_sem_content_length_aborta_sem_gravar(repositorio, run_id, tmp_path):
    bloco = b"0" * (8 * 1024 * 1024)

    def partes():
        yield b"%PDF-"
        for _ in range(8):
            yield bloco

    pdf = lambda req: httpx.Response(200, content=partes())

    with pytest.raises(FalhaTerminal, match=f"excede o limite de {comum.LIMITE_BYTES}"):
        coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), pdf=pdf, fonte="copom"))
    assert nada_gravado(repositorio, tmp_path)


def test_documento_no_limite_exato_e_aceito(repositorio, run_id, tmp_path):
    corpo = b"%PDF-" + b"0" * (comum.LIMITE_BYTES - 5)
    pdf = lambda req: httpx.Response(200, content=corpo)
    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), pdf=pdf, fonte="copom"))
    assert doc.caminho.stat().st_size == comum.LIMITE_BYTES


# Erros da fonte: retriáveis e terminais

RETRIAVEIS = [
    pytest.param(httpx.ConnectTimeout("tempo esgotado"), id="timeout"),
    pytest.param(httpx.ConnectError("conexão recusada"), id="conexao"),
    pytest.param(lambda req: httpx.Response(503), id="http-503"),
    pytest.param(lambda req: httpx.Response(429), id="http-429"),
]


@pytest.mark.parametrize("falha", RETRIAVEIS)
def test_falha_retriavel_no_download_e_repetida(falha, repositorio, run_id, tmp_path, sem_espera):
    pdf = sequencia(falha, falha, pdf_ok("copom"))
    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), pdf=pdf, fonte="copom"))
    assert doc.caminho.exists()
    assert len(sem_espera) == 2
    assert sem_espera[0] < sem_espera[1]  # backoff exponencial


@pytest.mark.parametrize("falha", RETRIAVEIS)
def test_falha_retriavel_esgotada_vira_terminal_sem_gravar(falha, repositorio, run_id, tmp_path, sem_espera):
    http = cliente(listagem("copom"), pdf=sequencia(falha), fonte="copom")
    with pytest.raises(FalhaTerminal, match=f"{TENTATIVAS} tentativas esgotadas"):
        coletar("copom", repositorio, run_id, tmp_path, http)
    assert len(sem_espera) == TENTATIVAS - 1
    assert nada_gravado(repositorio, tmp_path)


def test_falha_retriavel_na_listagem_tambem_e_repetida(repositorio, run_id, tmp_path):
    rotas = listagem("copom")
    rotas[copom.URL_ATAS] = sequencia(lambda req: httpx.Response(502), rotas[copom.URL_ATAS])
    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(rotas, fonte="copom"))
    assert doc.doc_id == "copom_ata_281"


@pytest.mark.parametrize("fonte", FONTES)
def test_404_no_pdf_e_terminal_sem_repetir(fonte, repositorio, run_id, tmp_path, sem_espera):
    http = cliente(listagem(fonte), pdf=lambda req: httpx.Response(404), fonte=fonte)
    with pytest.raises(FalhaTerminal, match=rf"{fonte}: publicação não encontrada \(HTTP 404\)"):
        coletar(fonte, repositorio, run_id, tmp_path, http)
    assert sem_espera == []
    assert nada_gravado(repositorio, tmp_path)


def test_copom_sem_ata_na_listagem_e_terminal(repositorio, run_id, tmp_path):
    rotas = listagem("copom")
    rotas[copom.URL_ATAS] = lambda req: httpx.Response(200, json={"conteudo": []})
    with pytest.raises(FalhaTerminal, match="ata mais recente não localizada"):
        coletar("copom", repositorio, run_id, tmp_path, cliente(rotas, fonte="copom"))
    assert nada_gravado(repositorio, tmp_path)


def test_b3_sem_release_na_listagem_e_terminal(repositorio, run_id, tmp_path):
    rotas = {b3.URL_ARQUIVOS: lambda req: httpx.Response(200, json={"success": True, "data": {"document_metas": []}})}
    with pytest.raises(FalhaTerminal, match="release de resultados não localizado"):
        coletar("b3", repositorio, run_id, tmp_path, cliente(rotas, fonte="b3"))


def test_b3_envia_categoria_e_idioma_na_listagem(repositorio, run_id, tmp_path):
    corpos = []
    rota = listagem("b3")[b3.URL_ARQUIVOS]

    def registra(req):
        corpos.append((req.method, json.loads(req.content)))
        return rota(req)

    coletar("b3", repositorio, run_id, tmp_path, cliente({b3.URL_ARQUIVOS: registra}, fonte="b3"))
    assert corpos == [("POST", {"categoryInternalNames": [b3.CATEGORIA], "language": "pt_BR", "published": True})]


def test_cvm_usa_ano_anterior_se_o_arquivo_do_ano_nao_existe(repositorio, run_id, tmp_path):
    rotas = {
        cvm.URL_IPE.format(ano=2026): lambda req: httpx.Response(404),
        cvm.URL_IPE.format(ano=2025): lambda req: httpx.Response(200, content=ipe_zip()),
    }
    doc = coletar("cvm", repositorio, run_id, tmp_path, cliente(rotas, fonte="cvm"))
    assert doc.doc_id == DOC_ID["cvm"]


def test_cvm_desempata_pelo_numero_do_protocolo():
    linhas = [
        {"Categoria": "Fato Relevante", "Codigo_CVM": "1", "Data_Entrega": "2026-10-02",
         "Link_Download": "https://x/?numProtocolo=200&numVersao=1"},
        {"Categoria": "Fato Relevante", "Codigo_CVM": "2", "Data_Entrega": "2026-10-02",
         "Link_Download": "https://x/?numProtocolo=900&numVersao=1"},
        {"Categoria": "Comunicado ao Mercado", "Codigo_CVM": "3", "Data_Entrega": "2026-10-03",
         "Link_Download": "https://x/?numProtocolo=999&numVersao=1"},
        {"Categoria": "Fato Relevante", "Codigo_CVM": "4", "Data_Entrega": "2026-10-01",
         "Link_Download": "https://x/?numProtocolo=1000&numVersao=1"},
    ]
    assert cvm.mais_recente(linhas)[:2] == ("2", 900)


# Supabase: reaproveitamento, retentativa e resposta perdida


def test_segunda_coleta_do_mesmo_documento_reaproveita(repositorio, run_id, tmp_path):
    coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    pdf = tmp_path / "sources" / "copom" / f"{comum.sha256_hex(corpo_pdf('copom'))}.pdf"
    modificado = pdf.stat().st_mtime_ns

    outro_run = "copom_20261005T090000Z_abcdef"
    repositorio.registrar_run(outro_run)
    doc = copom.coletar(
        run_id=outro_run, repositorio=repositorio, raiz_dados=tmp_path,
        cliente_http=cliente(listagem("copom"), fonte="copom"),
        relogio=lambda: datetime(2026, 10, 5, 9, 0, 1, tzinfo=timezone.utc),
    )

    assert repositorio.chamadas["enviar_pdf"] == 1  # hash já em documentos: não envia de novo
    assert len(repositorio.documentos) == 1
    assert [c.coleta_id for c in repositorio.coletas] == [1, 2]
    assert doc.coleta_id == 2
    assert pdf.stat().st_mtime_ns == modificado
    assert len(list(pdf.parent.glob("*.json"))) == 2


def test_objeto_ja_no_bucket_sem_linha_em_documentos_e_reaproveitado(repositorio, run_id, tmp_path):
    corpo = corpo_pdf("copom")
    caminho = f"copom/{comum.sha256_hex(corpo)}.pdf"
    repositorio.objetos[caminho] = corpo  # processo morreu entre o bucket e a transação

    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    assert doc.coleta_id == 1
    assert repositorio.objetos == {caminho: corpo}


def test_resposta_da_transacao_perdida_rele_e_nao_repete(repositorio, run_id, tmp_path):
    repositorio.perder_resposta_da_transacao = 1
    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    assert repositorio.chamadas["registrar_coleta"] == 1
    assert repositorio.chamadas["coleta_do_run"] == 1
    assert len(repositorio.coletas) == 1
    assert doc.coleta_id == 1


def test_transacao_que_nao_gravou_e_repetida(repositorio, run_id, tmp_path):
    repositorio.falhas["registrar_coleta"] = [FalhaRetriavel("timeout antes do commit")]
    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    assert repositorio.chamadas["registrar_coleta"] == 2
    assert repositorio.chamadas["coleta_do_run"] == 1
    assert len(repositorio.coletas) == 1
    assert doc.coleta_id == 1


def test_falha_retriavel_no_bucket_e_repetida(repositorio, run_id, tmp_path):
    repositorio.falhas["enviar_pdf"] = [FalhaRetriavel("HTTP 503"), FalhaRetriavel("HTTP 503")]
    doc = coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    assert repositorio.chamadas["enviar_pdf"] == 3
    assert doc.caminho.exists()


def test_gravacao_recusada_pelo_banco_aborta_sem_copia_local(repositorio, run_id, tmp_path):
    repositorio.falhas["registrar_coleta"] = [FalhaTerminal("violação de constraint")]
    with pytest.raises(FalhaTerminal, match="violação de constraint"):
        coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    assert repositorio.chamadas["registrar_coleta"] == 1
    assert not repositorio.coletas
    assert not (tmp_path / "sources").exists()


def test_run_ja_vinculado_e_terminal(repositorio, run_id, tmp_path):
    coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    with pytest.raises(FalhaTerminal, match="já tem coleta vinculada"):
        coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))


# Cópia local


def test_arquivo_local_em_uso_pede_para_fechar(repositorio, run_id, tmp_path, monkeypatch):
    def em_uso(origem, destino):
        raise PermissionError(13, "O arquivo já está sendo usado por outro processo")

    monkeypatch.setattr(persistencia.os, "replace", em_uso)
    with pytest.raises(FalhaTerminal, match="Feche o arquivo .*copom.*\\.pdf"):
        coletar("copom", repositorio, run_id, tmp_path, cliente(listagem("copom"), fonte="copom"))
    # O Supabase já tem documento e coleta; nenhum temporário fica para trás.
    assert len(repositorio.coletas) == 1
    assert list((tmp_path / "sources" / "copom").iterdir()) == []


# RepositorioSupabase: classificação dos erros do Storage, sem rede


class _StorageFalso:
    def __init__(self, erro):
        self.erro = erro
        self.opcoes = None

    @property
    def storage(self):
        return self

    def from_(self, bucket):
        assert bucket == "documentos"
        return self

    def upload(self, caminho, dados, opcoes):
        self.opcoes = opcoes
        if self.erro:
            raise self.erro


def test_storage_envia_como_pdf_sem_upsert():
    falso = _StorageFalso(None)
    persistencia.RepositorioSupabase(falso, "postgresql://localhost/x").enviar_pdf("copom/a.pdf", b"%PDF-")
    assert falso.opcoes == {"content-type": "application/pdf"}


@pytest.mark.parametrize(
    "status,codigo,mensagem,esperado",
    [
        ("409", "Duplicate", "The resource already exists", None),
        (400, "Duplicate", "Asset Already Exists", None),
        (503, "ServiceUnavailable", "indisponível", FalhaRetriavel),
        (413, "Payload too large", "The object exceeded the maximum allowed size", FalhaTerminal),
        (403, "Unauthorized", "new row violates row-level security policy", FalhaTerminal),
    ],
)
def test_storage_classifica_erros(status, codigo, mensagem, esperado):
    from storage3.exceptions import StorageApiError

    repo = persistencia.RepositorioSupabase(_StorageFalso(StorageApiError(mensagem, codigo, status)), "x")
    if esperado is None:
        repo.enviar_pdf("copom/a.pdf", b"%PDF-")
    else:
        with pytest.raises(esperado):
            repo.enviar_pdf("copom/a.pdf", b"%PDF-")


def test_storage_timeout_e_retriavel():
    repo = persistencia.RepositorioSupabase(_StorageFalso(httpx.ReadTimeout("lento")), "x")
    with pytest.raises(FalhaRetriavel):
        repo.enviar_pdf("copom/a.pdf", b"%PDF-")


# Nó da E0


def test_no_coleta_coloca_documento_fonte_no_estado(repositorio, run_id, tmp_path):
    estado = SimpleNamespace(run_id=run_id, manifest=SimpleNamespace(fonte="copom"))
    config = {"configurable": {
        "repositorio": repositorio, "raiz_dados": tmp_path,
        "cliente_http": cliente(listagem("copom"), fonte="copom"),
    }}
    saida = no_coleta(estado, config)
    assert list(saida) == ["documento_fonte"]
    assert saida["documento_fonte"].doc_id == "copom_ata_281"
