"use strict";

// Início: balanço de todos os runs e biblioteca das peças gravadas no Supabase, com filtro de formato, nível e estado.
// Usa o que app.js e resultado.js definem (estado, NIVEIS, FORMATOS, selo, metrica, buscarSlide, urlDoSlide).

const ESTADOS_BIBLIOTECA = [
  ["APROVADA", "Aprovadas", ["APROVADA"]],
  ["TODAS", "Todas", null],
  ["REPROVADA", "Reprovadas", ["REPROVADA"]],
  ["ERRO_INFRA", "Erro de infra", ["ERRO_INFRA"]],
  ["ANDAMENTO", "Em andamento", ["PENDENTE", "AVALIANDO"]],
];
const ROTULO_CELULA = {
  APROVADA: "Aprovadas", REPROVADA: "Reprovadas", ERRO_INFRA: "Erro de infra", PENDENTE: "Pendentes", AVALIANDO: "Avaliando",
};

const biblioteca = {
  balanco: null,
  itens: null,
  erro: null,
  formato: "",
  nivel: "",
  estadoFiltro: "APROVADA",
  abertos: new Set(),
  pecas: new Map(),
  limite: 20,
};

const chaveDoItem = (i) => `${i.run_id}|${i.celula_id}`;

async function carregarBiblioteca() {
  try {
    [biblioteca.balanco, biblioteca.itens] = await Promise.all([api("/api/balanco"), api("/api/biblioteca")]);
    biblioteca.erro = null;
  } catch (erro) {
    biblioteca.erro = erro.message;
  }
  if (estado.aba === "inicio") renderBalancoEBiblioteca();
}

function renderBalancoEBiblioteca() {
  const balanco = $("#inicio-balanco");
  const lista = $("#inicio-biblioteca");
  if (!balanco || !lista) return;
  if (biblioteca.erro) {
    balanco.innerHTML = `<div class="vazio">Não foi possível ler o balanço: ${esc(biblioteca.erro)}</div>`;
    lista.innerHTML = "";
    return;
  }
  if (!biblioteca.balanco) {
    balanco.innerHTML = `<div class="vazio">Carregando o balanço...</div>`;
    lista.innerHTML = "";
    return;
  }
  balanco.innerHTML = htmlBalanco(biblioteca.balanco);
  lista.innerHTML = htmlBiblioteca();
  carregarSlidesDaBiblioteca();
}

// Balanço geral

function htmlBalanco(b) {
  const soma = (linhas, campo) => linhas.reduce((t, l) => t + Number(l[campo] ?? 0), 0);
  const totalRuns = soma(b.runs, "total");
  const porStatus = {};
  const porFonte = {};
  for (const l of b.runs) {
    porStatus[l.status] = (porStatus[l.status] ?? 0) + Number(l.total);
    porFonte[l.fonte] = (porFonte[l.fonte] ?? 0) + Number(l.total);
  }
  const brl = soma(b.runs, "brl");
  const celulas = Object.fromEntries(b.celulas.map((c) => [c.estado, c]));
  const totalCelulas = soma(b.celulas, "total");
  const comTentativa = b.celulas.filter((c) => ["APROVADA", "REPROVADA"].includes(c.estado));
  const tentativasMedia = comTentativa.length
    ? comTentativa.reduce((t, c) => t + Number(c.tentativas) * Number(c.total), 0) / soma(comTentativa, "total")
    : null;
  const lista = (obj, rotulos = {}) => Object.entries(obj).map(([k, v]) => `${esc(rotulos[k] ?? k)}: <b>${fmtInt(v)}</b>`).join(" · ") || "-";
  const metricas = b.metricas.length
    ? `<div class="tabela-rolagem"><table><thead><tr><th>Métrica</th><th>Avaliações</th><th>Aprovadas</th><th>Taxa</th></tr></thead><tbody>${
      b.metricas.map((m) => `<tr><td>${esc(ROTULO_METRICA[m.nome] ?? m.nome)}</td><td class="num">${fmtInt(m.total)}</td>
        <td class="num">${fmtInt(m.passou)}</td><td class="num">${fmtPct(m.passou, m.total)}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="motivo-neutro">Nenhuma métrica gravada.</p>`;
  return `<h2 class="secao-titulo">Balanço geral</h2>
    <div class="cartao"><h3>Runs e custo</h3>
      <div class="metricas">
        ${metrica("Runs", fmtInt(totalRuns))}
        ${metrica("Tokens", fmtInt(soma(b.runs, "tokens")))}
        ${metrica("Custo total", fmtBRL(brl))}
        ${metrica("Custo médio por run", totalRuns ? fmtBRL(brl / totalRuns) : "")}
      </div>
      <p class="balanco-linha">Por status: ${lista(porStatus, ROTULO_ESTADO)}</p>
      <p class="balanco-linha">Por fonte: ${lista(porFonte)}</p></div>
    <div class="cartao"><h3>Células</h3>
      <div class="metricas">
        ${metrica("Células", fmtInt(totalCelulas))}
        ${Object.keys(ROTULO_CELULA).map((e) => metrica(ROTULO_CELULA[e], fmtInt(celulas[e]?.total ?? 0))).join("")}
        ${metrica("Tentativas por célula", tentativasMedia == null ? "" : tentativasMedia.toFixed(2).replace(".", ","))}
      </div></div>
    <div class="cartao"><h3>Aprovação por métrica</h3>
      <p class="motivo-neutro">Na última tentativa de cada célula, em todos os runs.</p>${metricas}</div>`;
}

function fmtPct(parte, total) {
  return Number(total) ? `${((Number(parte) / Number(total)) * 100).toFixed(0)}%` : "-";
}

// Biblioteca de peças

function itensFiltrados() {
  const estados = ESTADOS_BIBLIOTECA.find(([id]) => id === biblioteca.estadoFiltro)?.[2];
  return (biblioteca.itens ?? []).filter((i) =>
    (!biblioteca.formato || i.formato === biblioteca.formato)
    && (!biblioteca.nivel || i.nivel === biblioteca.nivel)
    && (!estados || estados.includes(i.estado)));
}

function segmentado(rotulo, campo, opcoes) {
  return `<div class="campo"><span class="rotulo">${rotulo}</span><div class="segmentado" role="radiogroup" aria-label="${rotulo}">${
    [["", "Todos"], ...opcoes].map(([valor, texto]) => `<button type="button" role="radio" data-filtro="${campo}"
      data-valor="${esc(valor)}" aria-checked="${biblioteca[campo] === valor}">${esc(texto)}</button>`).join("")}</div></div>`;
}

function htmlBiblioteca() {
  const todos = itensFiltrados();
  const visiveis = todos.slice(0, biblioteca.limite);
  const nomeNivel = Object.fromEntries(NIVEIS);
  const nomeFormato = Object.fromEntries(FORMATOS);
  const cartoes = visiveis.map((i) => {
    const chave = chaveDoItem(i);
    const aberto = biblioteca.abertos.has(chave);
    return `<div class="cartao biblioteca-item">
      <button type="button" class="biblioteca-cabecalho" data-peca="${esc(chave)}" aria-expanded="${aberto}">
        <span class="biblioteca-titulo">${esc(i.titulo ?? "Sem texto")}</span>
        <span class="biblioteca-tags">
          <span class="tag">${esc(nomeFormato[i.formato] ?? i.formato)} ${esc((nomeNivel[i.nivel] ?? i.nivel).toLowerCase())}</span>
          ${selo(i.estado)}</span>
        <span class="biblioteca-meta">${esc(String(i.fonte).toUpperCase())} · ${esc(fmtData(i.timestamp))} ·
          <span class="mono">${esc(i.run_id)}</span></span>
      </button>
      ${aberto ? `<div class="biblioteca-corpo">${corpoDaPeca(i)}</div>` : ""}</div>`;
  }).join("");
  const mais = todos.length > visiveis.length
    ? `<button type="button" class="botao" id="btn-mais-pecas">Mostrar mais (${todos.length - visiveis.length} restantes)</button>` : "";
  return `<h2 class="secao-titulo">Peças salvas</h2>
    <div class="cartao biblioteca-filtros">
      ${segmentado("Formato", "formato", FORMATOS)}
      ${segmentado("Nível", "nivel", NIVEIS)}
      <div class="campo"><label class="rotulo" for="sel-estado-biblioteca">Estado</label>
        <select id="sel-estado-biblioteca">${ESTADOS_BIBLIOTECA.map(([id, texto]) =>
          `<option value="${id}" ${id === biblioteca.estadoFiltro ? "selected" : ""}>${texto}</option>`).join("")}</select></div>
    </div>
    <p class="motivo-neutro">${todos.length} de ${(biblioteca.itens ?? []).length} peças salvas, na última tentativa de cada célula.</p>
    ${cartoes || `<div class="vazio">Nenhuma peça com esses filtros.</div>`}${mais}`;
}

function corpoDaPeca(item) {
  const peca = biblioteca.pecas.get(chaveDoItem(item));
  if (!peca) return `<div class="vazio">Carregando...</div>`;
  if (peca.erro) return `<span class="motivo">Não foi possível ler a peça: ${esc(peca.erro)}</span>`;
  const p = peca.payload;
  const abrir = `<div class="inicio-atalhos"><button type="button" class="botao fantasma" data-ir="resultado"
    data-run="${esc(item.run_id)}">Abrir o run no Resultado</button></div>`;
  if (item.formato === "artigo") {
    return `<div class="leitura"><h4>${esc(p.titulo)}</h4><p class="linha-fina">${esc(p.linha_fina)}</p>
      ${p.secoes.map((s) => `<p>${esc(s.texto)}</p>`).join("")}</div>${abrir}`;
  }
  if (item.formato === "carrossel") {
    const blocos = p.slides.map((_, i) => {
      const url = urlDoSlide(item.run_id, { celula_id: item.celula_id }, { tentativa: peca.tentativa }, i + 1);
      const guardado = vista.svgs.get(url);
      return `<div class="slide-bloco" data-slide="${esc(url)}"><div class="quadro">${guardado?.svg ?? ""}</div>
        <div class="acoes"><span>Slide ${i + 1}</span></div></div>`;
    }).join("");
    return `<div class="slides-faixa">${blocos}</div>${abrir}`;
  }
  return `<div class="cenas">${p.cenas.map((c, i) => `<div class="cena">
    <span class="tempo">Cena ${i + 1}<br><span class="cena-hora">${esc(c.inicio_s)} a ${esc(c.fim_s)} s</span></span>
    <div><div>${esc(c.fala)}</div><div class="gancho">${esc(c.gancho_visual)}</div></div></div>`).join("")}</div>${abrir}`;
}

async function abrirPeca(chave) {
  if (biblioteca.abertos.has(chave)) {
    biblioteca.abertos.delete(chave);
    renderBalancoEBiblioteca();
    return;
  }
  biblioteca.abertos.add(chave);
  renderBalancoEBiblioteca();
  if (biblioteca.pecas.has(chave)) return;
  const [runId, celulaId] = chave.split("|");
  try {
    biblioteca.pecas.set(chave, await api(`/api/runs/${encodeURIComponent(runId)}/pecas/${encodeURIComponent(celulaId)}`));
  } catch (erro) {
    biblioteca.pecas.set(chave, { erro: erro.message });
  }
  if (estado.aba === "inicio") renderBalancoEBiblioteca();
}

async function carregarSlidesDaBiblioteca() {
  for (const bloco of document.querySelectorAll("#inicio-biblioteca .slide-bloco")) {
    const quadro = bloco.querySelector(".quadro");
    if (quadro.firstChild) continue;
    try {
      quadro.innerHTML = (await buscarSlide(bloco.dataset.slide)).svg;
    } catch (erro) {
      quadro.innerHTML = `<span class="motivo">Não foi possível gerar o slide: ${esc(erro.message)}</span>`;
    }
  }
}

document.addEventListener("click", (evento) => {
  const alvo = evento.target.closest("#inicio-biblioteca button");
  if (!alvo) return;
  if (alvo.dataset.filtro) {
    biblioteca[alvo.dataset.filtro] = alvo.dataset.valor;
    biblioteca.limite = 20;
    renderBalancoEBiblioteca();
  } else if (alvo.dataset.peca) {
    abrirPeca(alvo.dataset.peca);
  } else if (alvo.id === "btn-mais-pecas") {
    biblioteca.limite += 20;
    renderBalancoEBiblioteca();
  }
});

document.addEventListener("change", (evento) => {
  if (evento.target.id !== "sel-estado-biblioteca") return;
  biblioteca.estadoFiltro = evento.target.value;
  biblioteca.limite = 20;
  renderBalancoEBiblioteca();
});
