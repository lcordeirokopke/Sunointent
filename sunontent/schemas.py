"""Contrato de dados do pipeline."""

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Nivel = Literal["iniciante", "intermediario", "avancado"]
NIVEIS: tuple[str, ...] = get_args(Nivel)
Formato = Literal["artigo", "carrossel", "roteiro"]
FORMATOS: tuple[str, ...] = get_args(Formato)
EstadoCelula = Literal["PENDENTE", "GERANDO", "AVALIANDO", "APROVADA", "REPROVADA", "ERRO_INFRA"]
ESTADOS_FINAIS: tuple[str, ...] = ("APROVADA", "REPROVADA", "ERRO_INFRA")
# E4 (docs/arquiteturas.md, Plano editorial). O planejador só usa os três motivos específicos.
MotivoDescarte = Literal["redundante", "secundario", "exige_base_tecnica", "fora_do_foco", "metodologico"]
MotivoPlanejador = Literal["redundante", "exige_base_tecnica", "fora_do_foco"]
OrigemDescarte = Literal["prefiltro", "planejador", "padrao"]
Papel = Literal["principal", "apoio", "contexto"]
RegraPlano = Literal[
    "id_inexistente", "contraditorio", "nucleo_ausente", "acima_do_limite", "jargao_sem_analogia", "motivo_invalido",
    "tese_invalida", "analogia_invalida", "prioridade_invalida",
]
Fonte = Literal["copom", "cvm", "b3"]
Modo = Literal["normal", "dev"]
TipoDocumento = Literal["copom_ata", "cvm_fato_relevante", "b3_release"]
Unidade = Literal["pct", "pp", "BRL", "BRL_mi", "x", "contagem", "data"]
TipoClaim = Literal["decisao", "projecao", "condicionante", "resultado", "risco", "evento_corporativo"]
# Vocabulário fixo de tags do claim; a E4 prioriza por elas (docs/fluxos/extracao.md, Tags).
Tag = Literal["impacto_pratico", "metodologico", "contexto_macro", "indicador_chave", "governanca"]
NoLLM = Literal["extractor", "adapter", "synthesizer", "judge"]
DecisaoQuarentena = Literal["aceitar", "descartar"]
TIPO_DOCUMENTO_POR_FONTE: dict[str, TipoDocumento] = {
    "copom": "copom_ata",
    "cvm": "cvm_fato_relevante",
    "b3": "b3_release",
}
# Descrição do tipo de documento nos prompts do extractor e do planejador.
DESCRICAO_TIPO: dict[str, str] = {
    "copom_ata": "ata de reunião do Comitê de Política Monetária",
    "cvm_fato_relevante": "fato relevante publicado na CVM",
    "b3_release": "release de resultados trimestrais",
}


class DocumentoFonte(BaseModel):
    """Documento entregue à E1 pela E0 (modo normal) ou pela referência (modo --dev).

    Ver docs/fluxos/ingestao.md, Contrato de saída.
    """

    model_config = ConfigDict(frozen=True)

    caminho: Path
    doc_id: str = Field(min_length=1)
    doc_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    url_origem: str = Field(min_length=1)
    coletado_em: datetime
    coleta_id: int | None = None  # None no modo --dev

    @field_validator("coletado_em")
    @classmethod
    def _utc(cls, valor: datetime) -> datetime:
        if valor.tzinfo is None:
            raise ValueError("coletado_em precisa de fuso horário")
        return valor.astimezone(timezone.utc)


class Ancora(BaseModel):
    """Posição no texto limpo de uma página. A página é 1-indexada; offsets de caractere, 0-indexados, fim exclusivo."""

    model_config = ConfigDict(frozen=True)

    pagina: int = Field(ge=1)
    offset_inicio: int = Field(ge=0)
    offset_fim: int = Field(ge=0)

    @model_validator(mode="after")
    def _intervalo(self) -> "Ancora":
        if self.offset_fim < self.offset_inicio:
            raise ValueError("offset_fim menor que offset_inicio")
        return self


class Numero(BaseModel):
    """Número do fonte (docs/arquiteturas.md, Número e âncora). `valor` é Decimal, nunca float."""

    model_config = ConfigDict(frozen=True)

    valor: Decimal
    unidade: Unidade
    bruto: str = Field(min_length=1)
    ancora: Ancora


class Pagina(BaseModel):
    pagina: int = Field(ge=1)
    texto_limpo: str
    ocr: bool = False


def trecho_das_paginas(
    paginas: list[Pagina], pagina_inicio: int, offset_inicio: int, pagina_fim: int, offset_fim: int
) -> str:
    """Texto limpo do intervalo, na regra do `Chunk.texto`."""
    texto = {p.pagina: p.texto_limpo for p in paginas}
    if pagina_inicio == pagina_fim:
        return texto[pagina_inicio][offset_inicio:offset_fim]
    partes = [texto[pagina_inicio][offset_inicio:]]
    partes += [texto[n] for n in range(pagina_inicio + 1, pagina_fim)]
    partes.append(texto[pagina_fim][:offset_fim])
    return "\n".join(partes)


class Chunk(BaseModel):
    """Trecho por seção lógica. `texto` é o texto limpo do intervalo: a página inicial a partir de
    offset_inicio, as páginas do meio inteiras e a final até offset_fim, unidas por quebra de linha."""

    chunk_id: str = Field(min_length=1)
    secao: str | None = None
    pagina_inicio: int = Field(ge=1)
    offset_inicio: int = Field(ge=0)
    pagina_fim: int = Field(ge=1)
    offset_fim: int = Field(ge=0)
    texto: str = Field(min_length=1)


class DocumentoProcessado(BaseModel):
    """Saída da E1: texto limpo por página, chunks posicionados, tabela de números e metadados."""

    doc_id: str = Field(min_length=1)
    doc_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tipo_documento: TipoDocumento
    emissor: str = Field(min_length=1)
    data_documento: date
    paginas: list[Pagina] = Field(min_length=1)
    chunks: list[Chunk] = Field(min_length=1)
    tabela_numeros: list[Numero]

    @model_validator(mode="after")
    def _coerente(self) -> "DocumentoProcessado":
        if [p.pagina for p in self.paginas] != list(range(1, len(self.paginas) + 1)):
            raise ValueError("as páginas precisam ser contíguas, a partir de 1")
        texto = {p.pagina: p.texto_limpo for p in self.paginas}
        ultimo = len(self.paginas)
        for numero in self.tabela_numeros:
            a = numero.ancora
            if a.pagina > ultimo or a.offset_fim > len(texto[a.pagina]):
                raise ValueError(f"âncora fora do texto: {numero.bruto!r} em {a}")
            if texto[a.pagina][a.offset_inicio : a.offset_fim] != numero.bruto:
                raise ValueError(f"bruto {numero.bruto!r} não confere com o texto na âncora {a}")
        ids = set()
        for chunk in self.chunks:
            if chunk.chunk_id in ids:
                raise ValueError(f"chunk_id repetido: {chunk.chunk_id}")
            ids.add(chunk.chunk_id)
            if not (1 <= chunk.pagina_inicio <= chunk.pagina_fim <= ultimo):
                raise ValueError(f"chunk {chunk.chunk_id} com páginas fora do documento")
            if chunk.pagina_inicio == chunk.pagina_fim and chunk.offset_fim < chunk.offset_inicio:
                raise ValueError(f"chunk {chunk.chunk_id} com intervalo invertido")
            if chunk.offset_inicio > len(texto[chunk.pagina_inicio]) or chunk.offset_fim > len(
                texto[chunk.pagina_fim]
            ):
                raise ValueError(f"chunk {chunk.chunk_id} fora do texto da página")
            if trecho_das_paginas(
                self.paginas, chunk.pagina_inicio, chunk.offset_inicio, chunk.pagina_fim, chunk.offset_fim
            ) != chunk.texto:
                raise ValueError(f"chunk {chunk.chunk_id} não confere com o texto das páginas")
        return self


class Claim(BaseModel):
    """Unidade atômica de fato (docs/arquiteturas.md, Claim). `literal_fonte` é o texto limpo na âncora."""

    id: str = Field(pattern=r"^C\d{2,}$")
    texto: str = Field(min_length=1)
    numeros: list[Numero]
    tipo: TipoClaim
    tags: list[Tag] = Field(min_length=1)
    ancora: Ancora
    literal_fonte: str = Field(min_length=1)


class FactSheet(BaseModel):
    """Saída da E2 (assinado=False) e da E3 (assinado=True). `tabela_numeros` é a do DocumentoProcessado."""

    doc_id: str = Field(min_length=1)
    tipo_documento: TipoDocumento
    emissor: str = Field(min_length=1)
    data_documento: date
    claims: list[Claim]
    tabela_numeros: list[Numero]
    assinado: bool = False

    def numeros_por_bruto(self, bruto: str) -> list[Numero]:
        """Todas as ocorrências de um bruto no documento, em ordem de posição."""
        return [n for n in self.tabela_numeros if n.bruto == bruto]


class ClaimProposto(BaseModel):
    """Claim como o extractor o devolve: cita o trecho e os números; o código resolve âncora e Numero."""

    id: str = Field(description="Identificador do claim, no formato C01, C02...")
    chunk_id: str = Field(description="chunk_id do trecho de onde o fato foi tirado, ex: K03")
    texto: str = Field(description="A afirmação normalizada, uma frase, um único fato")
    literal_fonte: str = Field(
        description="Trecho copiado exatamente do documento, de uma única página, que sustenta o fato"
    )
    numeros: list[str] = Field(
        description="Cada número do fato, escrito exatamente como aparece dentro de literal_fonte, ex: 13,75%"
    )
    tipo: TipoClaim
    tags: list[Tag] = Field(min_length=1, description="Uma ou mais tags do fato")


class SaidaExtractor(BaseModel):
    """Schema da resposta do extractor."""

    claims: list[ClaimProposto]


class ClaimQuarentena(BaseModel):
    """Claim ainda reprovado depois da última rodada do loop E2/E3, à espera de revisão humana
    (docs/fluxos/extracao.md, Quarentena). `motivo` é o da última recusa."""

    proposto: ClaimProposto
    motivo: str = Field(min_length=1)
    rodada: int = Field(ge=1)


class Span(BaseModel):
    """Uma chamada (tentativa) a um modelo. Linha de `llm_spans`."""

    span_id: str
    run_id: str
    no: NoLLM
    nivel: Nivel | None = None
    celula_id: str | None = None
    tentativa: int | None = None
    rodada: int | None = Field(default=None, ge=1)  # rodada do gate do plano; só no adapter
    modelo: str
    tokens_entrada: int = Field(ge=0)
    tokens_saida: int = Field(ge=0)
    custo_brl: Decimal = Field(ge=0)
    duracao_ms: int = Field(ge=0)
    falha_classe: Literal["retriavel", "terminal"] | None = None
    falha_mensagem: str | None = None


class RunManifest(BaseModel):
    """Versões e entrada de um run. Ver docs/arquiteturas.md, Run manifest."""

    run_id: str
    timestamp: datetime
    modo: Modo
    fonte: Fonte
    doc_id: str | None = None
    doc_sha256: str | None = None
    url_origem: str | None = None
    coletado_em: datetime | None = None
    coleta_id: int | None = None
    modelos: dict[str, str]
    prompts: dict[str, int]
    level_specs: dict[Nivel, int]
    glossario_versao: int = Field(ge=1)
    arquivos_sha256: dict[str, str]
    K: int = Field(ge=1)
    max_concorrencia: int = Field(ge=1)  # células em paralelo (graph.MAX_CONCORRENCIA)
    custo_total_tokens: int = 0
    custo_total_brl: Decimal = Decimal(0)


class ProblemaPlano(BaseModel):
    """Problema acusado pelo gate do plano (docs/arquiteturas.md, E4.3). Linha de `validacoes_plano`.
    `nivel` None vale para o plano inteiro; `detalhe` é a mensagem devolvida ao planejador."""

    regra: RegraPlano
    nivel: Nivel | None = None
    detalhe: str = Field(min_length=1)

    def texto(self) -> str:
        return f"[{self.regra}] {self.nivel or 'plano'}: {self.detalhe}"


class Descarte(BaseModel):
    """Claim que não entra no nível, com o motivo (docs/arquiteturas.md, Plano editorial)."""

    claim_id: str
    motivo: MotivoDescarte
    relacionado: str | None = None  # obrigatório em "redundante": o claim usado no nível que já diz o mesmo
    origem: OrigemDescarte


class ClaimPlanejado(BaseModel):
    claim_id: str
    papel: Papel
    prioridade: int = Field(ge=1)  # 1 a n no nível, sem repetir; 1 é o mais importante (corte por formato na E5.1)
    atribuir_fonte: bool  # preenchido pelo código: true em projecao, condicionante e risco


class Analogia(BaseModel):
    termo: str = Field(min_length=1)
    explicacao: str = Field(min_length=1)
    origem: Literal["glossario", "nova"]
    glossario_ref: str | None = None  # "<id do termo>#<n>": a n-ésima analogia do termo no glossário
    revisada: bool  # True só para analogia do glossário com status aprovada


class PlanoNivel(BaseModel):
    nivel: Nivel
    descartes: list[Descarte]
    claims: list[ClaimPlanejado]  # em ordem de leitura
    tese: str  # vazia só quando a limpeza do plano tirou uma tese inválida (registrado no aviso)
    tese_claim_ids: list[str]
    analogias: list[Analogia]  # uma por termo, a mesma nos 3 formatos do nível


class PlanoEditorial(BaseModel):
    """Saída da E4, agnóstica de formato. Sem texto de peça, exceto a explicação das analogias `nova`."""

    nucleo: list[str] = Field(min_length=1, max_length=3)
    niveis: list[PlanoNivel]  # exatamente 3, na ordem iniciante, intermediario e avancado
    rodadas: int = Field(ge=1, le=3)
    aviso: str | None = None  # o que a limpeza do código mudou depois da última rodada do gate

    @model_validator(mode="after")
    def _tres_niveis(self) -> "PlanoEditorial":
        if [p.nivel for p in self.niveis] != list(NIVEIS):
            raise ValueError(f"niveis precisa ter {', '.join(NIVEIS)}, nessa ordem")
        return self

    def nivel(self, nivel: str) -> PlanoNivel:
        return next(p for p in self.niveis if p.nivel == nivel)


# Peça final, saída do redator (E5) e avaliação (E6). Docs: docs/arquiteturas.md, Peça final e Scorecard e violação.

Metrica = Literal["numeric_fidelity", "text_size", "analogy_rule", "llm_judge", "plan_contract"]
METRICAS: tuple[str, ...] = get_args(Metrica)
# Onde, dentro da unidade da peça, está o trecho: `texto` é o texto da seção, do slide ou a fala da cena.
CampoPeca = Literal["titulo", "linha_fina", "texto", "gancho_visual"]
# Campos que podem ter número com marcador (docs/arquiteturas.md, Peça final): o texto das unidades e o título.
CampoNumero = Literal["texto", "titulo"]
MotivoReprovacao = Literal["k_esgotado", "nao_convergencia"]
ClasseFalha = Literal["retriavel", "terminal"]
VereditoScorecard = Literal["APROVADA", "REPROVADA", "ERRO_INFRA"]

# Marcador de número no texto do redator: {{C27.1}} é o número 1 do claim C27.
PADRAO_MARCADOR = re.compile(r"\{\{(C\d+)\.(\d+)\}\}")
PADRAO_ID_CLAIM = re.compile(r"^C\d+$")


def _sem_marcador(texto: str, campo: str) -> str:
    if PADRAO_MARCADOR.search(texto):
        raise ValueError(f"{campo} não aceita número com marcador: o número só vale no texto das unidades e no título")
    return texto


class Secao(BaseModel):
    texto: str = Field(min_length=1, description="o texto da seção; todo número que não seja data é um marcador {{Cnn.k}}")
    claim_ids: list[str] = Field(min_length=1, description="os claims do plano que a seção usa; nunca vazio")


class ArtigoPayload(BaseModel):
    titulo: str = Field(min_length=1, description="o título; aceita número só por marcador {{Cnn.k}}")
    linha_fina: str = Field(min_length=1, description="a linha fina, sem número com marcador")
    secoes: list[Secao] = Field(min_length=1)

    @field_validator("linha_fina")
    @classmethod
    def _linha_fina_sem_marcador(cls, valor: str) -> str:
        return _sem_marcador(valor, "linha_fina")

    def textos(self) -> list[str]:
        return [s.texto for s in self.secoes]

    def claim_ids_por_unidade(self) -> list[list[str]]:
        return [s.claim_ids for s in self.secoes]

    def claim_ids(self) -> list[str]:
        return _uniao(self.claim_ids_por_unidade())


class Slide(BaseModel):
    papel: Literal["gancho", "corpo", "conclusao"]
    texto: str = Field(min_length=1)
    claim_ids: list[str] = Field(description="vazio só no gancho e na conclusão; o slide de corpo usa pelo menos um")


class CarrosselPayload(BaseModel):
    slides: list[Slide] = Field(min_length=3, description="um gancho no início, slides de corpo e uma conclusão no fim")

    @model_validator(mode="after")
    def _estrutura(self) -> "CarrosselPayload":
        papeis = [s.papel for s in self.slides]
        if papeis[0] != "gancho" or papeis[-1] != "conclusao" or any(p != "corpo" for p in papeis[1:-1]):
            raise ValueError(
                "o carrossel tem exatamente um gancho no início, slides de corpo e uma conclusão no fim "
                f"(encontrado: {', '.join(papeis)})"
            )
        sem_claim = [i for i, s in enumerate(self.slides) if s.papel == "corpo" and not s.claim_ids]
        if sem_claim:
            raise ValueError(f"o slide de corpo usa pelo menos um claim (slides {', '.join(map(str, sem_claim))})")
        return self

    def textos(self) -> list[str]:
        return [s.texto for s in self.slides]

    def claim_ids_por_unidade(self) -> list[list[str]]:
        return [s.claim_ids for s in self.slides]

    def claim_ids(self) -> list[str]:
        return _uniao(self.claim_ids_por_unidade())


class Cena(BaseModel):
    inicio_s: int = Field(ge=0)
    fim_s: int = Field(ge=1)
    fala: str = Field(min_length=1)
    gancho_visual: str = Field(min_length=1, description="o que aparece na tela, sem número com marcador")
    claim_ids: list[str] = Field(description="vazio só na abertura e no fechamento; a cena de claim usa pelo menos um")

    @field_validator("gancho_visual")
    @classmethod
    def _gancho_visual_sem_marcador(cls, valor: str) -> str:
        return _sem_marcador(valor, "gancho_visual")


class RoteiroPayload(BaseModel):
    cenas: list[Cena] = Field(min_length=3, description="abertura, uma cena por claim e fechamento, em ordem")

    @model_validator(mode="after")
    def _estrutura(self) -> "RoteiroPayload":
        if self.cenas[0].inicio_s != 0:
            raise ValueError(f"a primeira cena começa em 0 s (encontrado: {self.cenas[0].inicio_s})")
        for i, cena in enumerate(self.cenas):
            if cena.fim_s <= cena.inicio_s:
                raise ValueError(f"a cena {i} termina em {cena.fim_s} s, antes de durar mais de 0 s")
            if i and cena.inicio_s != self.cenas[i - 1].fim_s:
                raise ValueError(
                    f"as cenas são contíguas: a cena {i} começa em {cena.inicio_s} s e a anterior termina em "
                    f"{self.cenas[i - 1].fim_s} s"
                )
        # A abertura é a primeira cena e o fechamento é a última; as do meio são de claim.
        sem_claim = [i for i, c in enumerate(self.cenas[1:-1], start=1) if not c.claim_ids]
        if sem_claim:
            raise ValueError(f"a cena de claim usa pelo menos um claim (cenas {', '.join(map(str, sem_claim))})")
        return self

    def textos(self) -> list[str]:
        return [c.fala for c in self.cenas]

    def claim_ids_por_unidade(self) -> list[list[str]]:
        return [c.claim_ids for c in self.cenas]

    def claim_ids(self) -> list[str]:
        return _uniao(self.claim_ids_por_unidade())


def _uniao(listas: list[list[str]]) -> list[str]:
    vistos: dict[str, None] = {}
    for lista in listas:
        for item in lista:
            vistos.setdefault(item, None)
    return list(vistos)


# Saída do redator: o payload do formato com os marcadores no texto, antes da montagem (E5.2). Schema próprio por
# formato, separado da `Peca`; a montagem troca os marcadores e entrega o payload final.


class SaidaArtigo(ArtigoPayload):
    """Artigo como o redator o escreve: título e texto das seções com marcadores {{Cnn.k}}."""


class SaidaCarrossel(CarrosselPayload):
    """Carrossel como o redator o escreve: texto dos slides com marcadores {{Cnn.k}}."""


class SaidaRoteiro(RoteiroPayload):
    """Roteiro como o redator o escreve: fala das cenas com marcadores {{Cnn.k}}."""


class NumeroUsado(BaseModel):
    """De onde veio um número da peça: o marcador, o claim e, pelo claim, a âncora no PDF."""

    campo: CampoNumero = "texto"
    unidade_indice: int | None = Field(default=None, ge=0)  # posição da seção, slide ou cena; None no título
    inicio: int = Field(ge=0)  # offset do número no campo, depois da troca do marcador
    fim: int
    claim_id: str  # o marcador {{C27.1}} é o número 1 do claim C27
    ordem: int = Field(ge=1)  # a posição do número na lista do claim

    @model_validator(mode="after")
    def _posicao(self) -> "NumeroUsado":
        if self.fim <= self.inicio:
            raise ValueError(f"fim ({self.fim}) precisa ser maior que inicio ({self.inicio})")
        if (self.campo == "titulo") != (self.unidade_indice is None):
            raise ValueError("unidade_indice é None no título e obrigatório no texto das unidades")
        return self


Payload = ArtigoPayload | CarrosselPayload | RoteiroPayload
_PAYLOAD_DO_FORMATO: dict[str, type[BaseModel]] = {
    "artigo": ArtigoPayload, "carrossel": CarrosselPayload, "roteiro": RoteiroPayload,
}


class Peca(BaseModel):
    celula_id: str
    nivel: Nivel
    formato: Formato
    payload: Payload  # texto final, com os números já no lugar dos marcadores
    claim_ids: list[str]  # união dos claim_ids das unidades
    numeros_usados: list[NumeroUsado]
    tentativa: int = Field(ge=1)

    @model_validator(mode="after")
    def _coerente(self) -> "Peca":
        if self.celula_id != celula_id(self.nivel, self.formato):
            raise ValueError(f"celula_id {self.celula_id!r} não confere com {self.nivel} e {self.formato}")
        if type(self.payload) is not _PAYLOAD_DO_FORMATO[self.formato]:
            raise ValueError(f"o payload de {type(self.payload).__name__} não é de uma peça {self.formato}")
        if set(self.claim_ids) != set(self.payload.claim_ids()):
            raise ValueError("claim_ids precisa ser a união dos claim_ids das unidades do payload")
        unidades = len(self.payload.textos())
        fora = [n.unidade_indice for n in self.numeros_usados if n.unidade_indice is not None and n.unidade_indice >= unidades]
        if fora:
            raise ValueError(f"numeros_usados aponta para unidade inexistente (índices {', '.join(map(str, fora))})")
        return self


class Violacao(BaseModel):
    metrica: Metrica
    trecho: str  # o texto exato que violou
    esperado: str
    obtido: str
    instrucao_corretiva: str  # o que fazer, em linguagem imperativa
    # Onde está o trecho: a unidade (None no título e na linha fina), o campo e o intervalo no campo.
    unidade_indice: int | None = Field(default=None, ge=0)
    campo: CampoPeca | None = None
    inicio: int | None = Field(default=None, ge=0)
    fim: int | None = None

    @model_validator(mode="after")
    def _intervalo(self) -> "Violacao":
        if (self.inicio is None) != (self.fim is None):
            raise ValueError("inicio e fim vêm juntos")
        if self.inicio is not None and self.fim <= self.inicio:
            raise ValueError(f"fim ({self.fim}) precisa ser maior que inicio ({self.inicio})")
        return self


# `detalhes` de cada métrica (metricas.detalhes, jsonb). Cada modelo leva o nome da métrica em `metrica`, que
# distingue os cinco quando o jsonb é lido de volta.


class DesfechosNumericos(BaseModel):
    """Contagem dos desfechos da M1 (docs/arquiteturas.md, E6, M1)."""

    fora_de_marcador_do_plano: int = Field(default=0, ge=0)  # mesmo valor e unidade de um número de claim do plano
    fora_do_plano_no_documento: int = Field(default=0, ge=0)  # existe na tabela de números, mas em nenhum claim do plano
    ausente_do_documento: int = Field(default=0, ge=0)  # alucinação
    marcador_invalido: int = Field(default=0, ge=0)  # marcador inexistente ou de claim fora do plano
    formato_nao_previsto: int = Field(default=0, ge=0)  # número que o parser não lê (US$ 5, 1.234.5)
    data_sem_lastro: int = Field(default=0, ge=0)  # data, ano ou trimestre que nenhum claim do plano nem a data do documento cobre


class NumericFidelityDetalhes(BaseModel):
    metrica: Literal["numeric_fidelity"] = "numeric_fidelity"
    numeros_varridos: int = Field(ge=0)  # números que não são data, no texto final da peça
    com_marcador: int = Field(ge=0)  # os que coincidem com uma posição de `numeros_usados`
    datas_conferidas: int = Field(ge=0)
    desfechos: DesfechosNumericos


class MedidaUnidade(BaseModel):
    unidade_indice: int | None = Field(default=None, ge=0)  # None no total do roteiro
    papel: str | None = None  # gancho, corpo ou conclusao no carrossel
    medida: int = Field(ge=0)
    limite_min: int | None = None
    limite_max: int
    dentro: bool


class TextSizeDetalhes(BaseModel):
    metrica: Literal["text_size"] = "text_size"
    grandeza: Literal["palavras", "caracteres"]
    limite: str  # o limite renderizado do LevelSpec (InstrucoesRedator.faixa_esperada)
    unidades: list[MedidaUnidade]  # no roteiro, a última é o total de palavras (unidade_indice None)


class TermoAnalogia(BaseModel):
    termo: str
    glossario_id: str
    unidade_indice: int | None = Field(default=None, ge=0)  # onde o termo aparece primeiro; None no título e na linha fina
    analogia_no_plano: bool
    marcador_na_unidade: bool


class AnalogyRuleDetalhes(BaseModel):
    metrica: Literal["analogy_rule"] = "analogy_rule"
    aplicavel: bool  # a regra vale só no Iniciante
    termos: list[TermoAnalogia]  # os termos do glossário acima de iniciante encontrados na peça


class ChecagemContrato(BaseModel):
    nome: Literal["quantidade_de_slides", "soma_das_cenas", "claim_ids_no_plano", "cobertura_do_formato"]
    esperado: str
    obtido: str
    passou: bool


class PlanContractDetalhes(BaseModel):
    metrica: Literal["plan_contract"] = "plan_contract"
    checagens: list[ChecagemContrato]


class VeredictoAnalogia(BaseModel):
    termo: str
    justificativa: str  # antes do booleano, na ordem em que o juiz escreve
    correta: bool


class VeredictoUnidade(BaseModel):
    unidade_indice: int = Field(ge=0)
    justificativa: str
    implicada: bool
    trecho_problematico: str | None = None


class LlmJudgeDetalhes(BaseModel):
    metrica: Literal["llm_judge"] = "llm_judge"
    analogias: list[VeredictoAnalogia]
    unidades: list[VeredictoUnidade]
    nivel_percebido: Nivel  # só informativo: alimenta a matriz de confusão de níveis


Detalhes = Annotated[
    NumericFidelityDetalhes | TextSizeDetalhes | AnalogyRuleDetalhes | PlanContractDetalhes | LlmJudgeDetalhes,
    Field(discriminator="metrica"),
]


class MetricaResultado(BaseModel):
    nome: Metrica
    valor: float | bool  # número de violações (unidades fora do limite, na text_size); true ou false na llm_judge
    faixa_esperada: str
    passou: bool
    violacoes: list[Violacao]
    detalhes: Detalhes

    @model_validator(mode="after")
    def _coerente(self) -> "MetricaResultado":
        if self.detalhes.metrica != self.nome:
            raise ValueError(f"detalhes de {self.detalhes.metrica} na métrica {self.nome}")
        if self.passou == bool(self.violacoes):
            raise ValueError("passou é verdadeiro se, e somente se, a métrica não tem violações")
        trocadas = sorted({v.metrica for v in self.violacoes if v.metrica != self.nome})
        if trocadas:
            raise ValueError(f"violação de {', '.join(trocadas)} na métrica {self.nome}")
        return self


class Scorecard(BaseModel):
    """Resultado de uma tentativa. O veredito é da tentativa; o estado final da célula fica em `celulas`."""

    celula_id: str
    tentativa: int = Field(ge=1)
    metricas: list[MetricaResultado]
    veredito: VereditoScorecard
    custo_tokens: int = Field(ge=0)
    duracao_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _veredito(self) -> "Scorecard":
        nomes = [m.nome for m in self.metricas]
        if len(set(nomes)) != len(nomes):
            raise ValueError("uma métrica aparece mais de uma vez no scorecard")
        todas_passaram = all(m.passou for m in self.metricas)
        if self.veredito == "APROVADA" and not todas_passaram:
            raise ValueError("APROVADA exige todas as métricas dentro da faixa")
        if self.veredito == "REPROVADA" and todas_passaram:
            raise ValueError("REPROVADA exige pelo menos uma métrica fora da faixa")
        return self


class RelatorioCorretivo(BaseModel):
    """O que o redator recebe no retry (docs/arquiteturas.md, O que se reinjeta), montado pela E7 a partir do
    scorecard da tentativa anterior e renderizado por prompts/synthesizer.j2."""

    certos: list[str]  # o que passou e não deve mudar, com o valor: "text_size: 0 unidades fora do limite"
    violacoes: list[Violacao]  # até 5 por métrica, na ordem do scorecard
    restantes: dict[Metrica, int] = {}  # quantas violações de cada métrica ficaram de fora das 5


class CelulaState(BaseModel):
    """Estado de uma das 9 células (docs/arquiteturas.md, O objeto de estado)."""

    celula_id: str
    nivel: Nivel
    formato: Formato
    peca: Peca | None = None
    scorecard: Scorecard | None = None
    tentativa: int = Field(default=0, ge=0)  # número da última peça gerada; 0 = nenhuma
    estado: EstadoCelula = "PENDENTE"
    motivo_reprovacao: MotivoReprovacao | None = None  # só em REPROVADA; a tentativa que explica é `tentativa`
    erro_classe: ClasseFalha | None = None  # só em ERRO_INFRA
    erro_mensagem: str | None = None
    # Calculado na montagem da E5.3 sobre a `peca` e entregue à E6, que o junta ao scorecard.
    plan_contract: MetricaResultado | None = None

    @model_validator(mode="after")
    def _id(self) -> "CelulaState":
        if self.celula_id != celula_id(self.nivel, self.formato):
            raise ValueError(f"celula_id {self.celula_id!r} não confere com {self.nivel} e {self.formato}")
        for objeto in (self.peca, self.scorecard):
            if objeto is not None and objeto.celula_id != self.celula_id:
                raise ValueError(f"{type(objeto).__name__} de {objeto.celula_id} na célula {self.celula_id}")
        if (self.estado == "REPROVADA") != (self.motivo_reprovacao is not None):
            raise ValueError("motivo_reprovacao é obrigatório em REPROVADA e proibido nos outros estados")
        erro = (self.erro_classe, self.erro_mensagem)
        if self.estado == "ERRO_INFRA" and None in erro:
            raise ValueError("ERRO_INFRA exige erro_classe e erro_mensagem")
        if self.estado != "ERRO_INFRA" and erro != (None, None):
            raise ValueError("erro_classe e erro_mensagem só valem em ERRO_INFRA")
        if self.plan_contract is not None and self.plan_contract.nome != "plan_contract":
            raise ValueError(f"plan_contract com o resultado de {self.plan_contract.nome}")
        return self


class FalhaCelula(BaseModel):
    """Falha (retentativa.Falha) que a E5 ou a E6 pegou na tentativa; a E7 a grava em `celulas` como ERRO_INFRA."""

    classe: ClasseFalha
    mensagem: str


class CelulaGrafoState(BaseModel):
    """Estado de uma célula nos nós da E5 à E7. O plano e o FactSheet são lidos do Supabase, não do estado."""

    run_id: str
    celula: CelulaState
    relatorio: RelatorioCorretivo | None = None  # o relatório da tentativa anterior, no retry
    falha: FalhaCelula | None = None
    # Da tentativa em curso, para o scorecard: tokens das chamadas de LLM (o redator e o juiz) e o instante
    # (time.monotonic) do início da E5.
    tokens_tentativa: int = Field(default=0, ge=0)
    inicio_tentativa: float | None = None


def celula_id(nivel: str, formato: str) -> str:
    return f"{nivel}__{formato}"


# Saída do planejador (E4.2): schema próprio, separado do PlanoEditorial. O motivo do descarte só aceita
# os três motivos específicos e não há `origem`, `atribuir_fonte` nem analogia do glossário: o código
# completa o plano no complemento (docs/arquiteturas.md, Plano editorial).


class DescarteDoPlanejador(BaseModel):
    claim_id: str = Field(description="id do claim descartado neste nível, ex: C07")
    motivo: MotivoPlanejador = Field(
        description="redundante: outro claim usado no nível já diz o mesmo; exige_base_tecnica: o claim depende "
        "de um termo técnico acima do nível; fora_do_foco: não serve ao foco do nível"
    )
    relacionado: str | None = Field(
        default=None,
        description="só em redundante: o id do claim usado neste nível que já diz o mesmo, com todos os números e "
        "datas do descartado",
    )


class ClaimDoPlanejador(BaseModel):
    claim_id: str = Field(description="id do claim usado neste nível")
    papel: Papel = Field(description="principal, apoio ou contexto")
    prioridade: int = Field(ge=1, description="1 a n no nível, sem repetir; 1 é o mais importante")


class AnalogiaDoPlanejador(BaseModel):
    termo: str = Field(min_length=1, description="o termo exatamente como aparece na lista de termos sem analogia")
    explicacao: str = Field(
        min_length=1, description="a analogia cotidiana que explica o termo, começando com 'É como', sem números"
    )


class NivelDoPlanejador(BaseModel):
    nivel: Nivel
    descartes: list[DescarteDoPlanejador] = Field(
        description="candidatos que não entram neste nível por um motivo específico"
    )
    claims: list[ClaimDoPlanejador] = Field(description="claims usados neste nível, em ordem de leitura")
    tese: str = Field(description="uma frase que parafraseia os claims de tese_claim_ids, sem números além de datas")
    tese_claim_ids: list[str] = Field(description="os claims usados neste nível que a tese parafraseia")
    analogias: list[AnalogiaDoPlanejador] = Field(
        description="só no iniciante: analogias novas para os termos da lista que estão nos claims usados; "
        "vazia nos outros níveis"
    )


class SaidaPlanejador(BaseModel):
    """Schema da resposta do planejador, com os campos na ordem em que o modelo os escreve."""

    nucleo: list[str] = Field(
        min_length=1, max_length=3,
        description="1 a 3 claims que dizem sobre o que é o documento, escolhidos entre os candidatos nos 3 níveis",
    )
    niveis: list[NivelDoPlanejador] = Field(description="exatamente 3: iniciante, intermediario e avancado")

    @model_validator(mode="after")
    def _tres_niveis(self) -> "SaidaPlanejador":
        if [p.nivel for p in self.niveis] != list(NIVEIS):
            raise ValueError(f"niveis precisa ter {', '.join(NIVEIS)}, nessa ordem")
        com_analogia = [p.nivel for p in self.niveis if p.nivel != "iniciante" and p.analogias]
        if com_analogia:
            raise ValueError(f"analogias só no iniciante (encontradas em {', '.join(com_analogia)})")
        return self


def juntar_celulas(atual: dict[str, "CelulaState"], novas: dict[str, "CelulaState"]) -> dict[str, "CelulaState"]:
    return {**atual, **novas}


class PipelineState(BaseModel):
    """Estado do grafo. Cresce a cada etapa implementada (docs/arquiteturas.md, O objeto de estado)."""

    run_id: str
    manifest: RunManifest
    documento_fonte: DocumentoFonte | None = None  # E0 ou nó de referência
    documento: DocumentoProcessado | None = None  # E1
    factsheet: FactSheet | None = None  # E2 (não assinado) e E3 (assinado)
    # Loop E2/E3 (docs/fluxos/extracao.md): a rodada atual, os claims como o extractor os propôs,
    # os que a E2 não conseguiu ancorar e os que a E3 devolveu para correção, com o motivo.
    rodada: int = 0
    propostas: dict[str, ClaimProposto] = {}
    pendencias: dict[str, str] = {}
    correcoes: dict[str, str] = {}
    # E4: rodada do gate e plano; o plano só entra aqui depois do gate.
    rodada_plano: int = 0
    plano: PlanoEditorial | None = None
    # 9 entradas depois da E4, chaveadas por celula_id. As células terminam em paralelo, cada uma devolve só a sua
    # entrada, e o reducer junta.
    celulas: Annotated[dict[str, CelulaState], juntar_celulas] = {}
    contagem_celulas: dict[str, int] = {}  # fechamento: quantas células em cada estado final
