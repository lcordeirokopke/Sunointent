"use strict";

// Aba da matriz 3x3: estado, scorecard, violações por posição, rastreabilidade e plano do nível de cada célula, e os
// slides do carrossel em SVG. Usa o estado e as utilidades de app.js, que carrega depois deste arquivo.

const ROTULO_METRICA = {
  numeric_fidelity: "M1 · números", text_size: "M2 · tamanho", analogy_rule: "M5 · analogia",
  llm_judge: "M6 · juiz", plan_contract: "Contrato do plano",
};
const ORDEM_METRICAS = ["numeric_fidelity", "text_size", "analogy_rule", "plan_contract", "llm_judge"];

const matriz = { run: null, celula: null, tentativa: null, svgs: new Map() };

function celulasDe(detalhe) {
  return detalhe?.celulas ?? [];
}

function ultimaTentativa(celula) {
  return celula.tentativas[celula.tentativas.length - 1] ?? null;
}

function chipsMetricas(tentativa) {
  const metricas = Object.fromEntries((tentativa?.scorecard?.metricas ?? []).map((m) => [m.nome, m]));
  return ORDEM_METRICAS.map((nome) => {
    const m = metricas[nome];
    const classe = !m ? "ausente" : m.passou ? "" : "reprovou";
    const titulo = !m ? "não avaliada nesta tentativa" : m.passou ? "passou" : `${m.violacoes.length} violação(ões)`;
    return `<span class="metrica-chip ${classe}" title="${esc(titulo)}">${esc(ROTULO_METRICA[nome])}</span>`;
  }).join("");
}

function renderMatriz() {
  const sel = $("#sel-run-matriz");
  const atual = matriz.run ?? estado.historico[0]?.run_id ?? null;
  matriz.run = atual;
  sel.innerHTML = estado.historico.map((r) => `<option value="${esc(r.run_id)}" ${r.run_id === atual ? "selected" : ""}>
    ${esc(r.run_id)} · ${esc(ROTULO_ESTADO[r.status] ?? r.status)}</option>`).join("");
  const alvo = $("#matriz");
  if (!atual) {
    alvo.innerHTML = `<div class="vazio">Nenhum run no Supabase.</div>`;
    return;
  }
  const d = estado.detalhes[atual];
  if (!d) {
    alvo.innerHTML = `<div class="vazio">Carregando...</div>`;
    carregarDetalhe(atual);
    return;
  }
  const celulas = celulasDe(d);
  if (!celulas.length) {
    alvo.innerHTML = `<div class="vazio">O run não chegou a gravar as células (a E4 grava o plano e as 9 células).</div>`;
    return;
  }
  const porId = Object.fromEntries(celulas.map((c) => [c.celula_id, c]));
  const quadro = (nivel, formato) => {
    const c = porId[celulaId(nivel, formato)];
    if (!c) return `<td>${selo("pendente")}</td>`;
    const t = ultimaTentativa(c);
    return `<td><button type="button" class="matriz-celula" data-celula="${esc(c.celula_id)}"
        aria-pressed="${matriz.celula === c.celula_id}">
      ${selo(c.estado)}
      <span class="tentativa">${t ? `tentativa ${t.tentativa} · ${esc(t.scorecard?.veredito ?? "sem scorecard")}` : "sem peça"}</span>
      <span class="linha-metricas">${chipsMetricas(t)}</span></button></td>`;
  };
  const linhas = NIVEIS.map(([nivel, rotulo]) => `<tr><th>${rotulo}</th>${FORMATOS.map(([f]) => quadro(nivel, f)).join("")}</tr>`);
  alvo.innerHTML = `<div class="cartao"><h3>Matriz de saída</h3>
    <table class="matriz-grade"><thead><tr><th></th>${FORMATOS.map(([, rotulo]) => `<th>${rotulo}</th>`).join("")}</tr></thead>
    <tbody>${linhas.join("")}</tbody></table></div>
    ${matriz.celula && porId[matriz.celula] ? detalheCelula(porId[matriz.celula], d) : `<div class="vazio">Escolha uma célula para ver a peça, o scorecard e a rastreabilidade.</div>`}`;
  carregarSlides();
}

// Texto com as marcas: números do marcador (sublinhados, com o claim e a página) e violações (destacadas).
function marcar(texto, marcas) {
  const cortes = new Set([0, texto.length]);
  for (const m of marcas) {
    cortes.add(Math.max(0, m.inicio));
    cortes.add(Math.min(texto.length, m.fim));
  }
  const pontos = [...cortes].sort((a, b) => a - b);
  let html = "";
  for (let i = 0; i < pontos.length - 1; i++) {
    const [a, b] = [pontos[i], pontos[i + 1]];
    if (a === b) continue;
    let trecho = esc(texto.slice(a, b));
    for (const m of marcas.filter((x) => x.inicio <= a && x.fim >= b)) {
      trecho = `<mark class="${m.classe}" title="${esc(m.titulo)}">${trecho}</mark>`;
    }
    html += trecho;
  }
  return html;
}

function paginaDoClaim(factsheet, claimId, ordem) {
  const claim = factsheet?.claims?.find((c) => c.id === claimId);
  return claim?.numeros?.[ordem - 1]?.ancora?.pagina ?? claim?.ancora?.pagina ?? null;
}

function linkPagina(detalhe, pagina) {
  const url = detalhe.manifest.url_origem;
  return url && pagina ? `${url}#page=${pagina}` : null;
}

// Cada claim da unidade com a página da âncora no PDF (e o link, quando o run tem a URL de origem).
function rastroDosClaims(ids, d) {
  if (!ids.length) return `<span class="item">sem claim</span>`;
  return ids.map((id) => {
    const claim = d.factsheet?.claims?.find((c) => c.id === id);
    const pagina = claim?.ancora?.pagina ?? null;
    const link = linkPagina(d, pagina);
    const rotulo = `${esc(id)}${pagina ? ` · p. ${pagina}` : ""}`;
    return `<span class="item" title="${esc(claim?.texto ?? "claim fora do FactSheet")}">${
      link ? `<a href="${esc(link)}" target="_blank" rel="noopener">${rotulo}</a>` : rotulo}</span>`;
  }).join("");
}

// As unidades da peça, por formato: o artigo tem título e linha fina fora das seções.
function unidadesDaPeca(formato, payload) {
  if (formato === "artigo") {
    return [
      { cabecalho: true, rotulo: "Título", indice: null, campos: [["titulo", payload.titulo]], claim_ids: [] },
      { cabecalho: true, rotulo: "Linha fina", indice: null, campos: [["linha_fina", payload.linha_fina]], claim_ids: [] },
      ...payload.secoes.map((s, i) => ({ rotulo: `Seção ${i + 1}`, indice: i, campos: [["texto", s.texto]], claim_ids: s.claim_ids })),
    ];
  }
  if (formato === "carrossel") {
    return payload.slides.map((s, i) => ({ rotulo: `Slide ${i + 1} · ${s.papel}`, indice: i, campos: [["texto", s.texto]], claim_ids: s.claim_ids }));
  }
  return payload.cenas.map((c, i) => ({
    rotulo: `Cena ${i + 1} · ${c.inicio_s}–${c.fim_s} s`, indice: i,
    campos: [["texto", c.fala], ["gancho_visual", c.gancho_visual]], claim_ids: c.claim_ids,
  }));
}

function marcasDoCampo(tentativa, d, indice, campo) {
  const mesmo = (o) => (o.unidade_indice ?? null) === indice && o.campo === campo;
  const numeros = tentativa.numeros_usados.filter(mesmo).map((n) => {
    const pagina = paginaDoClaim(d.factsheet, n.claim_id, n.ordem);
    return { inicio: n.inicio, fim: n.fim, classe: "numero", titulo: `${n.claim_id}.${n.ordem}${pagina ? ` · p. ${pagina}` : ""}` };
  });
  const violacoes = (tentativa.scorecard?.metricas ?? []).flatMap((m) => m.violacoes)
    .filter((v) => mesmo(v) && v.inicio != null)
    .map((v) => ({ inicio: v.inicio, fim: v.fim, classe: "violacao", titulo: `${v.metrica}: ${v.instrucao_corretiva}` }));
  return [...numeros, ...violacoes];
}

function blocoPeca(celula, tentativa, d) {
  const unidades = unidadesDaPeca(celula.formato, tentativa.payload).map((u) => {
    const campos = u.campos.map(([campo, texto]) => `${u.campos.length > 1 ? `<div class="rotulo-unidade">${esc(campo)}</div>` : ""}
      <div class="peca-texto">${marcar(texto, marcasDoCampo(tentativa, d, u.indice, campo))}</div>`).join("");
    return `<div class="peca-unidade"><div class="rotulo-unidade">${esc(u.rotulo)}</div>${campos}
      ${u.cabecalho ? "" : `<div class="rastro">${rastroDosClaims(u.claim_ids, d)}</div>`}</div>`;
  }).join("");
  return `<div class="cartao"><h3>Peça · tentativa ${tentativa.tentativa}</h3>
    <p class="motivo-neutro">Números do marcador sublinhados (claim e página no balão); violações destacadas em vermelho.</p>
    ${unidades}</div>`;
}

function blocoScorecard(tentativa) {
  const sc = tentativa.scorecard;
  if (!sc) return `<div class="vazio">Esta tentativa não tem scorecard.</div>`;
  const metricas = [...sc.metricas].sort((a, b) => ORDEM_METRICAS.indexOf(a.nome) - ORDEM_METRICAS.indexOf(b.nome));
  const linhas = metricas.map((m) => `<tr>
      <td>${esc(ROTULO_METRICA[m.nome] ?? m.nome)}</td>
      <td class="num">${esc(typeof m.valor === "boolean" ? (m.valor ? "sim" : "não") : m.valor)}</td>
      <td>${esc(m.faixa_esperada)}</td>
      <td>${selo(m.passou ? "ok" : "erro", m.passou ? "passou" : "reprovou")}</td></tr>`).join("");
  const violacoes = metricas.flatMap((m) => m.violacoes).map((v) => `<div class="violacao-item">
      ${selo("erro", ROTULO_METRICA[v.metrica] ?? v.metrica)}
      <b>${esc(v.trecho)}</b>
      <span class="motivo-neutro">${v.unidade_indice != null ? `unidade ${v.unidade_indice + 1} · ` : ""}${esc(v.campo ?? "")}${v.inicio != null ? ` [${v.inicio}–${v.fim}]` : ""}</span>
      <div>esperado: ${esc(v.esperado)} · obtido: ${esc(v.obtido)}</div>
      <div class="instrucao">${esc(v.instrucao_corretiva)}</div></div>`).join("");
  return `<div class="cartao"><div class="cabecalho-etapa"><h3>Scorecard · tentativa ${tentativa.tentativa}</h3>
      ${selo(sc.veredito)}</div>
    <div class="metricas" style="margin-bottom:12px">
      ${metrica("Tokens do juiz", fmtInt(sc.custo_tokens))}
      ${metrica("Duração", fmtDuracao(sc.duracao_ms))}
    </div>
    <div class="tabela-rolagem"><table><thead><tr><th>Métrica</th><th>Valor</th><th>Faixa esperada</th><th></th></tr></thead>
    <tbody>${linhas}</tbody></table></div>
    ${violacoes ? `<h4>Violações</h4>${violacoes}` : ""}</div>`;
}

function nomeDoSlide(celula, tentativa, n) {
  return `${celula.celula_id}_t${tentativa.tentativa}_slide${n}.svg`;
}

function urlDoSlide(runId, celula, tentativa, n, baixar = false) {
  return `/api/runs/${encodeURIComponent(runId)}/carrossel/${encodeURIComponent(celula.celula_id)}/${n}.svg`
    + `?tentativa=${tentativa.tentativa}${baixar ? "&baixar=true" : ""}`;
}

function blocoSlides(celula, tentativa) {
  const total = tentativa.payload.slides.length;
  const blocos = Array.from({ length: total }, (_, i) => {
    const n = i + 1;
    const url = urlDoSlide(matriz.run, celula, tentativa, n);
    const guardado = matriz.svgs.get(url);
    return `<div class="slide-bloco" data-slide="${esc(url)}">
      <div class="quadro">${guardado?.svg ?? ""}</div>
      <div class="estouro" ${guardado?.estouro ? "" : "hidden"}>O texto não cabe na altura da caixa: o limite de caracteres do LevelSpec e a caixa do template não batem.</div>
      <div class="acoes"><span>Slide ${n}</span>
        <a class="botao fantasma" href="${esc(urlDoSlide(matriz.run, celula, tentativa, n, true))}"
          download="${esc(nomeDoSlide(celula, tentativa, n))}">↓ baixar SVG</a></div></div>`;
  }).join("");
  return `<div class="cartao"><h3>Carrossel em SVG · tentativa ${tentativa.tentativa}</h3>
    <p class="motivo-neutro">Gerado na hora pelo servidor a partir da peça gravada; o texto é editável no Inkscape ou no Illustrator
    (instale a fonte Inter). Nada é gravado.</p>${blocos}</div>`;
}

// Busca o SVG de cada slide ainda sem cache e o põe na página; o que a prévia mostra é o mesmo arquivo do download.
async function carregarSlides() {
  for (const bloco of document.querySelectorAll("#matriz .slide-bloco")) {
    const url = bloco.dataset.slide;
    const quadro = bloco.querySelector(".quadro");
    if (matriz.svgs.has(url)) continue;
    try {
      const resposta = await fetch(url);
      if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
      const guardado = { svg: await resposta.text(), estouro: resposta.headers.get("X-Estouro-Altura") === "true" };
      matriz.svgs.set(url, guardado);
      quadro.innerHTML = guardado.svg;
      bloco.querySelector(".estouro").hidden = !guardado.estouro;
    } catch (erro) {
      quadro.innerHTML = `<span class="motivo">Não foi possível gerar o slide: ${esc(erro.message)}</span>`;
    }
  }
}

function detalheCelula(celula, d) {
  const motivo = celula.estado === "REPROVADA" ? `<span class="motivo-neutro">motivo: ${esc(celula.motivo_reprovacao)}</span>`
    : celula.estado === "ERRO_INFRA" ? `<span class="motivo">${esc(celula.erro_classe)}: ${esc(celula.erro_mensagem)}</span>` : "";
  const cabecalho = `<div class="cartao"><div class="cabecalho-etapa"><div>
      <h2 class="mono">${esc(celula.celula_id)}</h2>
      <p class="descricao">${celula.tentativas.length} tentativa(s) gravada(s)</p></div>
      <div>${selo(celula.estado)} ${motivo}</div></div>`;
  const tentativa = celula.tentativas.find((t) => t.tentativa === matriz.tentativa) ?? ultimaTentativa(celula);
  if (!tentativa) return `${cabecalho}</div><div class="vazio">A célula não chegou a gravar uma peça.</div>`;
  const botoes = celula.tentativas.map((t) => `<button type="button" class="botao fantasma btn-tentativa"
      data-tentativa="${t.tentativa}" aria-pressed="${t === tentativa}">Tentativa ${t.tentativa}${t.scorecard ? ` · ${esc(t.scorecard.veredito)}` : ""}</button>`).join("");
  const planoNivel = d.plano?.niveis?.find((p) => p.nivel === celula.nivel);
  return `${cabecalho}<div class="barra-tentativas">${botoes}</div></div>
    ${blocoScorecard(tentativa)}
    ${blocoPeca(celula, tentativa, d)}
    ${celula.formato === "carrossel" ? blocoSlides(celula, tentativa) : ""}
    ${planoNivel ? blocoNivelPlano(planoNivel, contextoPlano(d.plano, d.factsheet)) : ""}`;
}

document.addEventListener("click", (evento) => {
  const celula = evento.target.closest(".matriz-celula");
  if (celula) {
    matriz.celula = celula.dataset.celula;
    matriz.tentativa = null;
    renderMatriz();
    gravarEndereco();
    return;
  }
  const tentativa = evento.target.closest(".btn-tentativa");
  if (tentativa) {
    matriz.tentativa = Number(tentativa.dataset.tentativa);
    renderMatriz();
  }
});

document.querySelector("#sel-run-matriz").addEventListener("change", (evento) => {
  matriz.run = evento.target.value;
  matriz.celula = null;
  matriz.tentativa = null;
  gravarEndereco();
  renderMatriz();
});
