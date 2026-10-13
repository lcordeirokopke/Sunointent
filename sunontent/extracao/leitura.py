"""E1: do DocumentoFonte ao DocumentoProcessado. Determinístico, sem LLM.

Ordem: leitura do PDF (hash conferido) -> normalização -> descarte da versão em inglês do fim do
documento -> chunks -> tabela de números -> metadados.
Todo erro aqui aborta o run: o texto limpo é a referência dos offsets e não se corrige adiante.
"""

from pydantic import ValidationError

from sunontent import numeros
from sunontent.extracao import chunking, metadados, normalizacao, pdf, tabelas
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import (
    TIPO_DOCUMENTO_POR_FONTE,
    Ancora,
    DocumentoFonte,
    DocumentoProcessado,
    Numero,
    Pagina,
)


def processar(documento: DocumentoFonte, fonte: str) -> DocumentoProcessado:
    tipo = TIPO_DOCUMENTO_POR_FONTE[fonte]
    paginas = normalizacao.normalizar(pdf.ler_documento(documento))
    mantidas = normalizacao.sem_versao_em_ingles(paginas)
    if len(mantidas) < len(paginas):
        primeira, ultima = len(mantidas) + 1, len(paginas)
        descartadas = f"página {ultima} descartada" if primeira == ultima else f"páginas {primeira} a {ultima} descartadas"
        print(f"E1: {descartadas}: versão em inglês", flush=True)
        paginas = mantidas
    chunks = chunking.fatiar(paginas, tipo)
    if not chunks:
        raise FalhaTerminal(f"E1: {documento.caminho} não tem texto depois da normalização")
    emissor, data_documento = metadados.extrair(tipo, paginas)
    try:
        return DocumentoProcessado(
            doc_id=documento.doc_id,
            doc_sha256=documento.doc_sha256,
            tipo_documento=tipo,
            emissor=emissor,
            data_documento=data_documento,
            paginas=paginas,
            chunks=chunks,
            tabela_numeros=tabela_de_numeros(paginas),
        )
    except ValidationError as erro:
        raise FalhaTerminal(f"E1: documento processado inconsistente: {erro}") from erro


def tabela_de_numeros(paginas: list[Pagina]) -> list[Numero]:
    """Todos os números do texto limpo, por página, em ordem de posição, com âncora. Célula de tabela
    recebe a unidade do cabeçalho ou do rótulo da linha (tabelas.py)."""
    tabela = []
    for pagina in paginas:
        try:
            achados = tabelas.aplicar(pagina.texto_limpo, numeros.varrer(pagina.texto_limpo))
        except numeros.NumeroNaoPrevisto as erro:
            raise FalhaTerminal(f"E1: página {pagina.pagina}: {erro}") from erro
        tabela += [
            Numero(
                valor=achado.valor,
                unidade=achado.unidade,
                bruto=achado.bruto,
                ancora=Ancora(pagina=pagina.pagina, offset_inicio=achado.inicio, offset_fim=achado.fim),
            )
            for achado in achados
        ]
    return tabela
