"""Os textos de uma peça, um por campo, para as métricas que varrem o texto (M1 e M5)."""

from dataclasses import dataclass

from sunontent.schemas import ArtigoPayload, CampoPeca, CarrosselPayload, Payload, RoteiroPayload


@dataclass(frozen=True)
class CampoTexto:
    campo: CampoPeca
    unidade_indice: int | None  # None no título e na linha fina
    texto: str

    @property
    def aceita_numero(self) -> bool:
        """Só o título e o texto das unidades levam número, e só por marcador."""
        return self.campo in ("titulo", "texto")


def campos_da_peca(payload: Payload) -> list[CampoTexto]:
    """Todos os campos de texto, na ordem de leitura: título e linha fina do artigo, o texto de cada unidade
    (seção, slide ou fala da cena) e o gancho visual de cada cena."""
    if isinstance(payload, ArtigoPayload):
        campos = [CampoTexto("titulo", None, payload.titulo), CampoTexto("linha_fina", None, payload.linha_fina)]
        return campos + [CampoTexto("texto", i, s.texto) for i, s in enumerate(payload.secoes)]
    if isinstance(payload, CarrosselPayload):
        return [CampoTexto("texto", i, s.texto) for i, s in enumerate(payload.slides)]
    campos = []
    for i, cena in enumerate(payload.cenas):
        campos.append(CampoTexto("texto", i, cena.fala))
        campos.append(CampoTexto("gancho_visual", i, cena.gancho_visual))
    return campos


def nome_da_unidade(payload: Payload) -> str:
    if isinstance(payload, ArtigoPayload):
        return "seção"
    return "slide" if isinstance(payload, CarrosselPayload) else "cena"
