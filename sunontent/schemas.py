"""Contrato de dados do pipeline."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Nivel = Literal["iniciante", "intermediario", "avancado"]
NIVEIS: tuple[str, ...] = get_args(Nivel)
Formato = Literal["artigo", "carrossel", "roteiro"]
FORMATOS: tuple[str, ...] = get_args(Formato)
EstadoCelula = Literal["PENDENTE", "GERANDO", "AVALIANDO", "APROVADA", "REPROVADA", "ERRO_INFRA"]
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


class CelulaState(BaseModel):
    """Estado de uma das 9 células (docs/arquiteturas.md, O objeto de estado). `peca` e `scorecard`
    entram com a E5 e a E6."""

    celula_id: str
    nivel: Nivel
    formato: Formato
    tentativa: int = Field(default=0, ge=0)
    estado: EstadoCelula = "PENDENTE"

    @model_validator(mode="after")
    def _id(self) -> "CelulaState":
        if self.celula_id != celula_id(self.nivel, self.formato):
            raise ValueError(f"celula_id {self.celula_id!r} não confere com {self.nivel} e {self.formato}")
        return self


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
    celulas: dict[str, CelulaState] = {}  # 9 entradas depois da E4, chaveadas por celula_id
