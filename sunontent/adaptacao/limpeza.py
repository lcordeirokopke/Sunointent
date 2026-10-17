"""E4.3 Limpeza do plano depois da última rodada do gate (docs/arquiteturas.md, E4.3). Determinístico.

Quando o gate ainda acusa problema depois da última rodada, o código corrige o plano para que ele passe no
gate, nesta ordem, e devolve a lista do que mudou (o `aviso` do plano, mostrado na matriz do dashboard):
1. ids inexistentes saem do núcleo, dos claims, dos descartes e da tese; o descarte cujo `relacionado` não
   existe vira `secundario` com origem `padrao`;
2. contraditório: o claim repetido nos claims fica uma vez; o claim que o pré-filtro tirou do nível sai dos
   claims e dos descartes do planejador; o claim usado e descartado pelo planejador fica usado; o claim em dois
   descartes do planejador fica no primeiro;
3. núcleo: sai do núcleo o claim que não é candidato nos 3 níveis; o claim do núcleo que falta num nível entra
   no fim da ordem de leitura, como `apoio`, com a prioridade seguinte;
4. analogia `nova` inválida (número, marcador ou só o termo) sai do plano;
5. jargão sem analogia no Iniciante: o claim com o termo sai do Iniciante como `exige_base_tecnica` (origem
   `padrao`); se o claim é do núcleo, FalhaTerminal;
6. acima do limite: ficam o núcleo e os claims de maior prioridade até o `max_claims`; os outros viram
   `secundario` com origem `padrao`;
7. motivo inválido: o descarte do planejador vira `secundario` com origem `padrao`;
8. tese: saem os ids repetidos e os que não estão nos claims do nível; sem id, vazia, com número ou, no
   Iniciante, com termo técnico, a tese fica vazia e sem ids (a E5 não recebe tese desse nível);
9. as prioridades são renumeradas de 1 a n, na ordem que tinham, e as analogias do Iniciante e as explicações do
   Intermediário ficam só para os termos dos claims que restaram.
Núcleo vazio é FalhaTerminal. No fim, o gate confere de novo; problema restante é FalhaTerminal.
"""

from sunontent.adaptacao import gate
from sunontent.adaptacao.complemento import (
    TIPOS_COM_ATRIBUICAO,
    analogias_do_iniciante,
    explicacoes_do_intermediario,
    termos_dos_claims,
)
from sunontent.adaptacao.prefiltro import Prefiltro
from sunontent.glossario import Glossario
from sunontent.levelspec import LevelSpec
from sunontent.retentativa import FalhaTerminal
from sunontent.schemas import NIVEIS, Claim, ClaimPlanejado, Descarte, PlanoEditorial, PlanoNivel, ProblemaPlano


def _secundario(claim_id: str) -> Descarte:
    return Descarte(claim_id=claim_id, motivo="secundario", origem="padrao")


class _Limpeza:
    def __init__(self, plano: PlanoEditorial, claims: dict[str, Claim], prefiltro: Prefiltro, specs, glossario):
        self.claims, self.prefiltro, self.specs, self.glossario = claims, prefiltro, specs, glossario
        self.rodadas = plano.rodadas
        self.nucleo = list(plano.nucleo)
        self.niveis = {p.nivel: p.model_copy(deep=True) for p in plano.niveis}
        self.novas: dict = {}  # analogias `nova` válidas do Iniciante, por id do termo
        self.mudancas: list[str] = []

    def mudou(self, regra: str, nivel: str | None, texto: str) -> None:
        self.mudancas.append(f"[{regra}] {nivel or 'plano'}: {texto}")

    def falhar(self, motivo: str, problemas: list[ProblemaPlano]) -> None:
        raise FalhaTerminal(
            f"E4: depois de {self.rodadas} rodadas e da limpeza do plano, {motivo}. "
            + "; ".join(p.texto() for p in problemas)
        )

    def usados(self, nivel: str) -> list[str]:
        return [c.claim_id for c in self.niveis[nivel].claims]

    # 1
    def ids_inexistentes(self) -> None:
        for c in [c for c in self.nucleo if c not in self.claims]:
            self.nucleo.remove(c)
            self.mudou("id_inexistente", None, f"{c} saiu do núcleo")
        for nivel, p in self.niveis.items():
            for c in p.claims:
                if c.claim_id not in self.claims:
                    self.mudou("id_inexistente", nivel, f"{c.claim_id} saiu dos claims")
            p.claims = [c for c in p.claims if c.claim_id in self.claims]
            descartes = []
            for d in p.descartes:
                if d.claim_id not in self.claims:
                    self.mudou("id_inexistente", nivel, f"{d.claim_id} saiu dos descartes")
                elif d.relacionado and d.relacionado not in self.claims:
                    self.mudou("id_inexistente", nivel, f"{d.claim_id} passou a secundario (relacionado {d.relacionado} não existe)")
                    descartes.append(_secundario(d.claim_id))
                else:
                    descartes.append(d)
            p.descartes = descartes
            for c in dict.fromkeys(c for c in p.tese_claim_ids if c not in self.claims):
                self.mudou("id_inexistente", nivel, f"{c} saiu da tese")
            p.tese_claim_ids = [c for c in p.tese_claim_ids if c in self.claims]

    # 2
    def contraditorio(self) -> None:
        for nivel, p in self.niveis.items():
            vistos, claims = set(), []
            for c in p.claims:
                if c.claim_id in vistos:
                    self.mudou("contraditorio", nivel, f"{c.claim_id} repetido saiu dos claims")
                    continue
                vistos.add(c.claim_id)
                claims.append(c)
            por_claim: dict[str, list[Descarte]] = {}
            for d in p.descartes:
                por_claim.setdefault(d.claim_id, []).append(d)
            descartes = []
            for claim_id, lista in por_claim.items():
                do_prefiltro = [d for d in lista if d.origem == "prefiltro"]
                if do_prefiltro:
                    if claim_id in vistos:
                        claims = [c for c in claims if c.claim_id != claim_id]
                        self.mudou("contraditorio", nivel, f"{claim_id} não é candidato e saiu dos claims")
                    if len(lista) > 1:
                        self.mudou("contraditorio", nivel, f"{claim_id} não é candidato e saiu dos descartes do planejador")
                    descartes.append(do_prefiltro[0])
                elif claim_id in vistos:
                    self.mudou("contraditorio", nivel, f"{claim_id} ficou nos claims e saiu dos descartes")
                else:
                    if len(lista) > 1:
                        self.mudou("contraditorio", nivel, f"{claim_id} ficou só no descarte {lista[0].motivo}")
                    descartes.append(lista[0])
            p.claims, p.descartes = claims, descartes

    def _incluir(self, nivel: str, claim_id: str) -> None:
        p = self.niveis[nivel]
        p.descartes = [d for d in p.descartes if d.claim_id != claim_id]
        p.claims.append(
            ClaimPlanejado(
                claim_id=claim_id, papel="apoio", prioridade=max((c.prioridade for c in p.claims), default=0) + 1,
                atribuir_fonte=self.claims[claim_id].tipo in TIPOS_COM_ATRIBUICAO,
            )
        )

    # 3
    def nucleo_ausente(self, problemas: list[ProblemaPlano]) -> None:
        for c in list(self.nucleo):
            if any(c not in self.prefiltro.candidatos[n] for n in NIVEIS):
                self.nucleo.remove(c)
                self.mudou("nucleo_ausente", None, f"{c} saiu do núcleo (não é candidato nos 3 níveis)")
        if not self.nucleo:
            self.falhar("o núcleo ficou vazio", problemas)
        for nivel in NIVEIS:
            for c in self.nucleo:
                if c not in self.usados(nivel):
                    self._incluir(nivel, c)
                    self.mudou("nucleo_ausente", nivel, f"{c}, do núcleo, entrou nos claims")

    # 4 e 5
    def analogias_e_jargao(self, problemas: list[ProblemaPlano]) -> None:
        p = self.niveis["iniciante"]
        novas = self.novas
        for a in p.analogias:
            if a.origem != "nova":
                continue
            termo_id = self.glossario.termo_da_forma(a.termo)
            motivo = gate.motivo_da_analogia_invalida(a.explicacao, self.glossario.termos[termo_id] if termo_id else None)
            if motivo:
                self.mudou("analogia_invalida", "iniciante", f"a analogia nova de {a.termo!r} saiu ({motivo})")
            elif termo_id:
                novas[termo_id] = a
        p.analogias = analogias_do_iniciante(self.glossario, self.claims, self.usados("iniciante"), novas)
        cobertos = {a.glossario_ref.split("#", 1)[0] if a.glossario_ref else self.glossario.termo_da_forma(a.termo)
                    for a in p.analogias}
        for claim_id in self.usados("iniciante"):
            faltam = [t for t in termos_dos_claims(self.glossario, self.claims, [claim_id], "iniciante") if t not in cobertos]
            if not faltam:
                continue
            nomes = ", ".join(repr(self.glossario.termos[t].termo) for t in faltam)
            if claim_id in self.nucleo:
                self.falhar(f"o claim {claim_id}, do núcleo, tem termo sem analogia no iniciante ({nomes})", problemas)
            p.claims = [c for c in p.claims if c.claim_id != claim_id]
            p.descartes.append(Descarte(claim_id=claim_id, motivo="exige_base_tecnica", origem="padrao"))
            self.mudou("jargao_sem_analogia", "iniciante", f"{claim_id} saiu como exige_base_tecnica ({nomes} sem analogia)")
        p.analogias = analogias_do_iniciante(self.glossario, self.claims, self.usados("iniciante"), novas)

    # 6
    def acima_do_limite(self) -> None:
        for nivel, p in self.niveis.items():
            maximo = self.specs[nivel].max_claims
            if len(p.claims) <= maximo:
                continue
            ordem = sorted(range(len(p.claims)), key=lambda i: (p.claims[i].claim_id not in self.nucleo,
                                                                p.claims[i].prioridade, i))
            ficam = set(ordem[:maximo])
            saem = [c.claim_id for i, c in enumerate(p.claims) if i not in ficam]
            p.claims = [c for i, c in enumerate(p.claims) if i in ficam]
            p.descartes += [_secundario(c) for c in saem]
            verbo = "saiu" if len(saem) == 1 else "saíram"
            self.mudou("acima_do_limite", nivel, f"{', '.join(saem)} {verbo} pela prioridade (máximo de {maximo})")

    # 7
    def motivo_invalido(self) -> None:
        for nivel, p in self.niveis.items():
            usados = set(self.usados(nivel))
            descartes = []
            for d in p.descartes:
                invalido = False
                if d.origem == "planejador" and d.motivo == "redundante":
                    invalido = (
                        d.relacionado not in usados
                        or bool(gate.faltam_no_relacionado(self.claims[d.claim_id], self.claims[d.relacionado]))
                    )
                elif d.origem == "planejador" and d.motivo == "exige_base_tecnica":
                    invalido = not termos_dos_claims(self.glossario, self.claims, [d.claim_id], nivel)
                if invalido:
                    self.mudou("motivo_invalido", nivel, f"{d.claim_id} passou de {d.motivo} a secundario")
                    descartes.append(_secundario(d.claim_id))
                else:
                    descartes.append(d)
            p.descartes = descartes

    # 8
    def tese(self) -> None:
        for nivel, p in self.niveis.items():
            usados = set(self.usados(nivel))
            fora = list(dict.fromkeys(c for c in p.tese_claim_ids if c not in usados))
            p.tese_claim_ids = list(dict.fromkeys(c for c in p.tese_claim_ids if c in usados))
            if fora:
                verbo = "saiu" if len(fora) == 1 else "saíram"
                self.mudou("tese_invalida", nivel, f"{', '.join(fora)} {verbo} da tese (fora dos claims do nível)")
            motivos = []
            if not p.tese.strip():
                motivos.append("vazia")
            if not p.tese_claim_ids:
                motivos.append("sem claim")
            if gate.numeros_da_tese(p.tese, self.glossario):
                motivos.append("com número")
            if nivel == "iniciante" and gate.jargao_da_tese(p.tese, self.glossario):
                motivos.append("com termo técnico")
            if motivos and (p.tese or p.tese_claim_ids):
                p.tese, p.tese_claim_ids = "", []
                self.mudou("tese_invalida", nivel, f"a tese saiu ({', '.join(motivos)})")

    # 9
    def prioridades(self) -> None:
        for nivel, p in self.niveis.items():
            ordem = sorted(range(len(p.claims)), key=lambda i: (p.claims[i].prioridade, i))
            nova = {i: posicao for posicao, i in enumerate(ordem, start=1)}
            if any(c.prioridade != nova[i] for i, c in enumerate(p.claims)):
                p.claims = [c.model_copy(update={"prioridade": nova[i]}) for i, c in enumerate(p.claims)]
                self.mudou("prioridade_invalida", nivel, "prioridades renumeradas de 1 a n")

    def analogias_finais(self) -> None:
        p = self.niveis["iniciante"]
        p.analogias = analogias_do_iniciante(self.glossario, self.claims, self.usados("iniciante"), self.novas)
        self.niveis["intermediario"].analogias = explicacoes_do_intermediario(
            self.glossario, self.claims, self.usados("intermediario")
        )

    def plano(self) -> PlanoEditorial:
        return PlanoEditorial(
            nucleo=self.nucleo,
            niveis=[PlanoNivel.model_validate(self.niveis[n].model_dump()) for n in NIVEIS],
            rodadas=self.rodadas,
            aviso="; ".join(self.mudancas) or None,
        )


def limpar(
    plano: PlanoEditorial,
    claims: dict[str, Claim],
    prefiltro: Prefiltro,
    specs: dict[str, LevelSpec],
    glossario: Glossario,
    problemas: list[ProblemaPlano],
) -> PlanoEditorial:
    """O plano corrigido, com `aviso` listando o que o código mudou. `problemas` são os da última rodada, citados
    na FalhaTerminal."""
    limpeza = _Limpeza(plano, claims, prefiltro, specs, glossario)
    limpeza.ids_inexistentes()
    limpeza.contraditorio()
    limpeza.nucleo_ausente(problemas)
    limpeza.analogias_e_jargao(problemas)
    limpeza.acima_do_limite()
    limpeza.motivo_invalido()
    limpeza.tese()
    limpeza.prioridades()
    limpeza.analogias_finais()
    limpo = limpeza.plano()
    restantes = gate.conferir(limpo, claims, prefiltro, specs, glossario, aceitar_tese_vazia=True)
    if restantes:
        limpeza.falhar("o plano ainda tem problemas", restantes)
    return limpo
