"use strict";

// Etapas da arquitetura (docs/arquiteturas.md). Uma etapa está implementada quando algum dos seus nós
// existe no grafo devolvido por /api/grafo, que é o próprio graph.construir_grafo().
const ETAPAS = [
  { id: "E0", nos: ["coleta", "referencia"], nome: "Coleta",
    descricao: "Baixa a publicação mais recente da fonte, valida o PDF e grava no Supabase e em data/sources/." },
  { id: "E1", nos: ["leitura"], nome: "Leitura",
    descricao: "Lê o PDF, confere o doc_sha256 e gera o texto limpo, os chunks, os metadados e a tabela de números." },
  { id: "E2", nos: ["extract"], nome: "Extractor & Anchor",
    descricao: "O LLM extrai os claims do documento e cada um é ancorado no texto da página de origem." },
  { id: "E3", nos: ["validate", "descarte"], nome: "Validador de Âncoras",
    descricao: "Confere âncoras e números de cada claim. Reprovado volta à E2; aprovado, o FactSheet é assinado. Reprovado depois da última rodada é descartado, com o motivo registrado na validação da rodada." },
  { id: "E4", nos: ["adapt"], nome: "Plano editorial",
    descricao: "Decide o núcleo e, para cada nível, o que entra, em que ordem, o que sai e por quê, e quais analogias usar. O código confere o plano." },
  { id: "E5", nos: ["synth"], celulas: true, nome: "Format Synthesizer",
    descricao: "Gera as 9 peças: texto, carrossel e roteiro de vídeo para cada nível." },
  { id: "E6", nos: ["evaluate"], celulas: true, nome: "Hybrid Evaluator",
    descricao: "Mede a fidelidade numérica, o tamanho por nível, a legibilidade, a densidade de termos, a regra de analogia, o entailment dos claims e o contrato do plano (M1 a M7)." },
  { id: "E7", nos: ["router"], celulas: true, nome: "Router",
    descricao: "Aprova a célula, devolve para nova tentativa ou descarta conforme as métricas." },
];
// Matriz de saída: as 9 células da E5 à E7, em paralelo (celula_id = <nivel>__<formato>).
const NIVEIS = [["iniciante", "Iniciante"], ["intermediario", "Intermediário"], ["avancado", "Avançado"]];
const FORMATOS = [["artigo", "Artigo"], ["carrossel", "Carrossel"], ["roteiro", "Roteiro"]];
const ESTADOS_FINAIS = ["APROVADA", "REPROVADA", "ERRO_INFRA"];
const REFERENCIA = { nome: "Referência",
  descricao: "Modo --dev: lê o documento congelado de tests/fixtures/referencia/, confere o doc_sha256 e o registra no Supabase." };

const ROTULO_ESTADO = {
  pendente: "pendente", rodando: "rodando", concluida: "concluída", falhou: "falhou",
  interrompida: "interrompida", "nao-implementada": "não implementada",
  concluido: "concluído", abortado: "abortado", interrompido: "interrompido",
  ocioso: "ocioso", parada: "parada",
};

const estado = {
  fonte: "",
  aba: "inicio",
  subaba: "terminal",
  grafo: { nos: [], arestas: [], rodadas_extracao: 3, rodadas_plano: 3, max_concorrencia: 3 },
  execucao: null,
  runSelecionado: null,
  historico: [],
  detalhes: {},
  resultadoRun: null,
  fonteEventos: null,
};

const $ = (seletor) => document.querySelector(seletor);

// Utilidades

function esc(valor) {
  return String(valor ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtDuracao(ms) {
  if (ms == null || Number.isNaN(ms)) return "";
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1).replace(".", ",")} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${String(Math.floor(s % 60)).padStart(2, "0")} s`;
}

function fmtHora(iso) {
  return iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour12: false }) : "";
}

function fmtData(iso) {
  return iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" }) : "";
}

function fmtBRL(valor) {
  if (valor == null || valor === "") return "";
  return `R$ ${Number(valor).toLocaleString("pt-BR", { minimumFractionDigits: 4, maximumFractionDigits: 6 })}`;
}

function fmtInt(valor) {
  return valor == null ? "" : Number(valor).toLocaleString("pt-BR");
}

function selo(estadoItem, texto) {
  return `<span class="selo ${esc(estadoItem)}">${esc(texto ?? ROTULO_ESTADO[estadoItem] ?? estadoItem)}</span>`;
}

function metrica(rotulo, valor, mono = false) {
  return `<div class="metrica"><span class="rotulo">${esc(rotulo)}</span>`
    + `<span class="valor${mono ? " mono" : ""}">${valor === "" || valor == null ? "-" : valor}</span></div>`;
}

// <option> de cada run do histórico, com o selecionado marcado: os seletores de Resultado e Info. técnicas usam o mesmo run.
function opcoesDeRuns(atual) {
  return estado.historico.map((r) => `<option value="${esc(r.run_id)}" ${r.run_id === atual ? "selected" : ""}>
    ${esc(r.run_id)} · ${esc(ROTULO_ESTADO[r.status] ?? r.status)}</option>`).join("");
}

function avisar(texto, erro = false) {
  const el = $("#aviso");
  el.textContent = texto;
  el.className = `aviso${erro ? " erro" : ""}`;
  el.hidden = false;
  clearTimeout(avisar.tempo);
  avisar.tempo = setTimeout(() => { el.hidden = true; }, 4500);
}

async function api(caminho, opcoes = {}) {
  const resposta = await fetch(caminho, {
    headers: { "Content-Type": "application/json" }, ...opcoes,
  });
  const corpo = resposta.status === 204 ? null : await resposta.json().catch(() => null);
  if (!resposta.ok) throw new Error(corpo?.detail ?? `HTTP ${resposta.status}`);
  return corpo;
}

// Modelo da execução: os eventos do terminal viram runs e etapas

function novaExecucao(resumo) {
  return { ...resumo, eventos: [], runs: [], erro: null };
}

function runAtual() {
  const runs = estado.execucao?.runs ?? [];
  return runs[runs.length - 1] ?? null;
}

function aplicar(ev) {
  const ex = estado.execucao;
  ex.eventos.push(ev);
  let run = runAtual();
  const hora = Date.parse(ev.hora);

  switch (ev.tipo) {
    case "run": {
      run = { run_id: ev.run_id, modo: ev.modo, fonte: ev.run_id.split("_")[0], status: "rodando",
        etapas: {}, celulas: {}, atual: null, rodadas: 0, inicio: hora, fim: null, linhas: [], resumo: null, erro: null };
      ex.runs.push(run);
      estado.runSelecionado = run.run_id;
      break;
    }
    case "etapa": {
      if (!run) break;
      const et = run.etapas[ev.no] ??= { estado: "pendente", total: 0, inicioAtual: null, vezes: 0, linhas: [] };
      if (ev.estado === "iniciada") {
        et.estado = "rodando";
        et.inicioAtual = hora;
        et.vezes += 1;
        run.atual = ev.no;
        if (ev.no === "extract") run.rodadas += 1;
      } else {
        et.estado = "concluida";
        if (et.inicioAtual != null) et.total += hora - et.inicioAtual;
        et.inicioAtual = null;
        run.atual = null;
        carregarDetalhe(run.run_id);
      }
      et.linhas.push(ev);
      break;
    }
    case "celula": {
      if (!run) break;
      const c = run.celulas[ev.celula_id] ??= { estado: "pendente", tentativa: 0, nos: {} };
      if (ev.no) {
        const n = c.nos[ev.no] ??= { iniciadas: 0, concluidas: 0, ativo: false, primeiro: hora, ultimo: null };
        if (ev.estado === "iniciada") { n.iniciadas += 1; n.ativo = true; c.estado = "rodando"; }
        else { n.concluidas += 1; n.ativo = false; n.ultimo = hora; }
        c.tentativa = Number(ev.tentativa);
      } else {
        c.estado = ev.estado;
        carregarDetalhe(run.run_id);
      }
      break;
    }
    case "concluido":
      if (run) { run.status = "concluido"; run.resumo = ev.resumo; run.fim = hora; carregarDetalhe(run.run_id); }
      break;
    case "abortado":
      if (run) {
        run.status = "abortado";
        run.erro = { classe: ev.classe, mensagem: ev.mensagem };
        run.fim = hora;
        fecharEtapaAtual(run, "falhou", hora, ev);
        carregarDetalhe(run.run_id);
      }
      break;
    case "erro":
      ex.erro = { classe: ev.classe, mensagem: ev.mensagem };
      break;
    case "fim":
      ex.estado = ev.estado;
      ex.codigo = ev.codigo;
      for (const r of ex.runs) {
        if (r.status === "rodando") {
          r.status = "interrompido";
          r.fim = hora;
          fecharEtapaAtual(r, "interrompida", hora, null);
        }
      }
      carregarHistorico();
      break;
    default:
      if (run && run.status === "rodando" && run.atual) run.etapas[run.atual].linhas.push(ev);
  }
  if (run && ev.tipo !== "fim") run.linhas.push(ev);
  agendarRender();
}

function fecharEtapaAtual(run, novoEstado, hora, ev) {
  if (!run.atual) return;
  const et = run.etapas[run.atual];
  et.estado = novoEstado;
  if (et.inicioAtual != null) et.total += hora - et.inicioAtual;
  et.inicioAtual = null;
  if (ev) et.linhas.push(ev);
  run.atual = null;
}

function runSelecionado() {
  const runs = estado.execucao?.runs ?? [];
  return runs.find((r) => r.run_id === estado.runSelecionado) ?? runs[runs.length - 1] ?? null;
}

function implementada(etapa) {
  return etapa.nos.some((no) => estado.grafo.nos.includes(no));
}

function noDaEtapa(etapa, run) {
  if (etapa.id === "E0") return run?.modo === "dev" ? "referencia" : "coleta";
  // Etapa com mais de um nó (E3: validate e descarte): o estado é o do último nó que rodou.
  return [...etapa.nos].reverse().find((no) => run?.etapas[no]) ?? etapa.nos[0];
}

// E5 a E7: o nó roda uma vez por célula, em paralelo; o estado da etapa sai das 9 células.
function infoEtapaCelulas(etapa, run) {
  const no = etapa.nos[0];
  const celulas = Object.values(run?.celulas ?? {});
  const com = celulas.filter((c) => c.nos[no]);
  if (!com.length) return { estado: "pendente", no };
  const ativas = com.filter((c) => c.nos[no].ativo).length;
  const final = celulas.length >= NIVEIS.length * FORMATOS.length && celulas.every((c) => ESTADOS_FINAIS.includes(c.estado));
  let situacao = ativas || !final ? "rodando" : "concluida";
  if (situacao === "rodando" && run.status !== "rodando") situacao = run.status === "abortado" ? "falhou" : "interrompida";
  const inicio = Math.min(...com.map((c) => c.nos[no].primeiro));
  const fim = ativas ? Date.now() : Math.max(...com.map((c) => c.nos[no].ultimo ?? 0));
  return {
    estado: situacao, no, duracao: fim - inicio, vezes: com.reduce((soma, c) => soma + c.nos[no].iniciadas, 0),
    linhas: run.linhas.filter((ev) => ev.tipo === "celula" && ev.no === no),
  };
}

function infoEtapa(etapa, run) {
  if (!implementada(etapa)) return { estado: "nao-implementada", no: etapa.nos[0] };
  if (etapa.celulas) return infoEtapaCelulas(etapa, run);
  const no = noDaEtapa(etapa, run);
  const et = run?.etapas[no];
  if (!et) return { estado: "pendente", no };
  const ets = etapa.nos.map((n) => run.etapas[n]).filter(Boolean);
  const duracao = ets.reduce((soma, e) => soma + e.total + (e.inicioAtual != null ? Date.now() - e.inicioAtual : 0), 0);
  return { estado: et.estado, no, duracao, vezes: (run.etapas[etapa.nos[0]] ?? et).vezes, linhas: ets.flatMap((e) => e.linhas) };
}

function nomeEtapa(etapa, run) {
  return etapa.id === "E0" && run?.modo === "dev" ? REFERENCIA.nome : etapa.nome;
}

// Render

let renderAgendado = false;
function agendarRender() {
  if (renderAgendado) return;
  renderAgendado = true;
  requestAnimationFrame(() => { renderAgendado = false; render(); });
}

function render() {
  renderControles();
  if (estado.aba === "fluxo") renderFluxo();
}

function rodando() {
  return estado.execucao?.estado === "rodando";
}

function renderControles() {
  const ex = estado.execucao;
  const ativo = rodando();
  $("#btn-executar").disabled = ativo;
  $("#btn-dev").disabled = ativo;
  $("#btn-parar").disabled = !ativo;
  for (const b of document.querySelectorAll("#fontes button")) b.disabled = ativo;

  const geral = ex ? ex.estado : "ocioso";
  $("#estado-geral").dataset.estado = geral;
  $("#estado-geral-texto").textContent = ex
    ? `${ROTULO_ESTADO[geral] ?? geral}${ex.codigo != null ? ` (código ${ex.codigo})` : ""}`
    : "ocioso";

}

// Início: o resumo do último run gravado; as células aprovadas vêm do detalhe do run.
function renderInicio() {
  const alvo = $("#inicio-conteudo");
  const ultimo = estado.historico[0];
  if (!ultimo) {
    alvo.innerHTML = `<div class="vazio">Nenhum run no Supabase ainda.<br>Escolha a fonte na barra do topo e use <b>Executar run</b> para baixar a publicação mais recente
      ou <b>Modo dev</b> para usar o documento de referência. Ao iniciar, a página abre o Fluxo para você acompanhar as etapas ao vivo.</div>`;
    return;
  }
  const d = estado.detalhes[ultimo.run_id];
  if (!d) carregarDetalhe(ultimo.run_id);
  const celulas = d?.celulas ?? [];
  const aprovadas = celulas.filter((c) => c.estado === "APROVADA").length;
  alvo.innerHTML = `<div class="cartao"><div class="cabecalho-etapa"><div>
      <h3>Último run</h3><h2 class="mono">${esc(ultimo.run_id)}</h2>
      <p class="descricao">${esc(fmtData(ultimo.timestamp))}</p></div>
      <div>${selo(ultimo.modo, ultimo.modo)} ${selo(ultimo.status, ROTULO_ESTADO[ultimo.status] ?? ultimo.status)}</div></div>
    <div class="metricas" style="margin-top:12px">
      ${metrica("Fonte", esc(ultimo.fonte))}
      ${metrica("Documento", esc(ultimo.doc_id ?? ""), true)}
      ${metrica("Claims", fmtInt(ultimo.claims))}
      ${metrica("Claims descartados na E3", fmtInt(ultimo.descartados))}
      ${metrica("Células aprovadas", d ? `${aprovadas} de ${celulas.length || NIVEIS.length * FORMATOS.length}` : "")}
      ${metrica("Tokens", fmtInt(ultimo.custo_total_tokens))}
      ${metrica("Custo", fmtBRL(ultimo.custo_total_brl))}
    </div>
    <div class="inicio-atalhos">
      <button type="button" class="botao primario" data-ir="resultado" data-run="${esc(ultimo.run_id)}">Ver resultado</button>
      <button type="button" class="botao" data-ir="tecnicas" data-run="${esc(ultimo.run_id)}">Info. técnicas</button>
      <button type="button" class="botao" data-ir="fluxo">Ver fluxo</button>
    </div></div>
    <div id="inicio-balanco"></div>
    <div id="inicio-biblioteca"></div>`;
  renderBalancoEBiblioteca();
}

function renderFluxo() {
  const run = runSelecionado();
  renderRunsExecucao();
  renderPipeline(run);
  renderSubabas(run);
  renderSubpainel(run);
}

function renderRunsExecucao() {
  const el = $("#runs-execucao");
  const runs = estado.execucao?.runs ?? [];
  el.hidden = runs.length < 2;
  if (runs.length < 2) return;
  const sel = runSelecionado();
  el.innerHTML = runs.map((r) => `
    <button type="button" class="chip-run" data-run="${esc(r.run_id)}" data-estado="${esc(r.status)}"
      aria-pressed="${r === sel}"><span class="ponto"></span>${esc(r.run_id)}</button>`).join("");
}

function renderPipeline(run) {
  const html = ETAPAS.map((etapa) => {
    const info = infoEtapa(etapa, run);
    const ativa = estado.subaba === etapa.id;
    const nos = etapa.id === "E0" && !run ? "coleta | referencia" : info.no;
    let laco = "";
    if (etapa.id === "E3" && implementada(etapa) && run?.rodadas) {
      laco = `<span class="laco" title="Loop E2/E3">rodada ${run.rodadas}/${estado.grafo.rodadas_extracao}</span>`;
    }
    return `
      <button type="button" class="etapa" data-etapa="${etapa.id}" data-estado="${info.estado}"
        aria-current="${ativa}">
        <span class="codigo">${etapa.id}</span>
        <span class="nome">${esc(nomeEtapa(etapa, run))}</span>
        <span class="no">${esc(nos)}</span>
        ${etapa.celulas && info.estado !== "nao-implementada" ? miniGrade(run) : ""}
        <span class="rodape">
          ${selo(info.estado)}
          <span class="tempo">${info.duracao != null ? fmtDuracao(info.duracao) : ""}</span>
        </span>
        ${laco}
      </button>`;
  }).join("");
  $("#pipeline").innerHTML = html;
}

function celulaId(nivel, formato) {
  return `${nivel}__${formato}`;
}

// As 9 células em miniatura, nos cartões da E5 à E7: cada quadrado mostra o estado da célula.
function miniGrade(run) {
  const quadrados = NIVEIS.flatMap(([nivel]) => FORMATOS.map(([formato]) => {
    const c = run?.celulas[celulaId(nivel, formato)];
    return `<i data-estado="${esc(c?.estado ?? "pendente")}" title="${esc(celulaId(nivel, formato))}: ${esc(c?.estado ?? "pendente")}"></i>`;
  }));
  return `<span class="minigrade" aria-hidden="true">${quadrados.join("")}</span>`;
}

// Grade 3x3 do painel da etapa: linhas são os níveis, colunas os formatos; cada célula mostra o que ela fez no nó.
function gradeCelulas(etapa, run) {
  const no = etapa.nos[0];
  const celula = (nivel, formato) => {
    const c = run.celulas[celulaId(nivel, formato)];
    const passo = c?.nos[no];
    let corpo;
    if (!c) corpo = selo("pendente");
    else if (passo?.ativo) corpo = selo("rodando", `tentativa ${c.tentativa}`);
    else if (no === "router" && ESTADOS_FINAIS.includes(c.estado)) corpo = selo(c.estado);
    else if (passo?.concluidas) corpo = selo("concluida", `concluída${passo.concluidas > 1 ? ` ×${passo.concluidas}` : ""}`);
    else corpo = selo("pendente", ESTADOS_FINAIS.includes(c.estado) ? "não passou" : "pendente");
    const final = c && no !== "router" && ESTADOS_FINAIS.includes(c.estado) ? `<span class="final">${selo(c.estado)}</span>` : "";
    return `<td data-celula="${esc(celulaId(nivel, formato))}">${corpo}${final}</td>`;
  };
  const linhas = NIVEIS.map(([nivel, rotulo]) => `<tr><th>${rotulo}</th>${FORMATOS.map(([f]) => celula(nivel, f)).join("")}</tr>`);
  return `<div class="cartao"><h3>Células (até ${estado.grafo.max_concorrencia} em paralelo)</h3>
    <div class="tabela-rolagem"><table class="grade-celulas">
      <thead><tr><th></th>${FORMATOS.map(([, rotulo]) => `<th>${rotulo}</th>`).join("")}</tr></thead>
      <tbody>${linhas.join("")}</tbody></table></div></div>`;
}

function renderSubabas(run) {
  const abas = [`<button type="button" role="tab" class="subaba" data-subaba="terminal"
      aria-selected="${estado.subaba === "terminal"}">⌘ Terminal</button>`];
  for (const etapa of ETAPAS) {
    const info = infoEtapa(etapa, run);
    abas.push(`<button type="button" role="tab" class="subaba" data-subaba="${etapa.id}" data-estado="${info.estado}"
      aria-selected="${estado.subaba === etapa.id}" title="${esc(etapa.descricao)}">
      <span class="indicador"></span>${etapa.id} ${esc(nomeEtapa(etapa, run))}</button>`);
  }
  $("#subabas").innerHTML = abas.join("");
}

function linhasHtml(eventos) {
  if (!eventos.length) return `<div class="terminal-vazio">sem saída</div>`;
  return eventos.map((ev) => {
    const classes = ["linha", ev.canal, `t-${ev.tipo}`];
    if (ev.tipo === "etapa" || (ev.tipo === "celula" && ev.no)) classes.push(ev.estado === "iniciada" ? "iniciada" : "concluida");
    const texto = ev.tipo === "fim"
      ? `processo encerrado (${ROTULO_ESTADO[ev.estado] ?? ev.estado}, código ${ev.codigo})`
      : ev.texto;
    return `<div class="${classes.join(" ")}"><span class="hora">${fmtHora(ev.hora)}</span><span class="texto">${esc(texto)}</span></div>`;
  }).join("");
}

function terminalHtml(titulo, eventos) {
  return `
    <div class="terminal">
      <div class="terminal-barra"><span class="bolinhas"><i></i><i></i><i></i></span><span class="titulo" title="${esc(titulo)}">${esc(titulo)}</span></div>
      <div class="terminal-corpo" data-rolagem>${linhasHtml(eventos)}</div>
    </div>`;
}

function renderSubpainel(run) {
  const el = $("#subpainel");
  // Mantém a rolagem do terminal: segue o fim se já estava no fim.
  const anterior = el.querySelector("[data-rolagem]");
  const noFim = !anterior || anterior.scrollTop + anterior.clientHeight >= anterior.scrollHeight - 24;
  const topo = anterior?.scrollTop ?? 0;

  if (estado.subaba === "terminal") {
    el.innerHTML = painelTerminal();
  } else {
    const etapa = ETAPAS.find((e) => e.id === estado.subaba);
    el.innerHTML = painelEtapa(etapa, run);
  }

  const novo = el.querySelector("[data-rolagem]");
  if (novo) novo.scrollTop = noFim ? novo.scrollHeight : topo;
}

function painelTerminal() {
  const ex = estado.execucao;
  if (!ex) {
    const dica = estado.historico.length ? "" : `<br>Use <b>Executar run</b> para rodar o pipeline completo ou <b>Modo dev</b> para usar o documento de referência.`;
    return `<div class="vazio">Nenhuma execução nesta sessão do dashboard.${dica}</div>`;
  }
  const erro = ex.erro ? `
    <div class="cartao"><div class="cabecalho-etapa"><h2>Erro antes do run</h2>${selo("erro", ex.erro.classe)}</div>
    <p class="descricao">${esc(ex.erro.mensagem)}</p></div>` : "";
  return erro + terminalHtml(`${ex.comando}  ·  início ${fmtHora(ex.inicio)}`, ex.eventos);
}

function painelEtapa(etapa, run) {
  const info = infoEtapa(etapa, run);
  const descricao = etapa.id === "E0" && run?.modo === "dev" ? REFERENCIA.descricao : etapa.descricao;
  const cabecalho = `
    <div class="cartao">
      <div class="cabecalho-etapa">
        <div>
          <h2>${etapa.id} · ${esc(nomeEtapa(etapa, run))}</h2>
          <p class="descricao">${esc(descricao)}</p>
        </div>
        ${selo(info.estado)}
      </div>
    </div>`;

  if (info.estado === "nao-implementada") {
    return cabecalho + `<div class="vazio">Etapa ainda não implementada: o nó <code>${esc(etapa.nos[0])}</code>
      não existe no grafo de <code>graph.py</code>.</div>`;
  }
  if (!run) {
    return cabecalho + `<div class="vazio">Nenhum run em andamento. Os dados desta etapa aparecem quando um run passar por ela.</div>`;
  }

  const metricas = [
    metrica("Nó", `<code>${esc(info.no)}</code>`),
    metrica("Estado", esc(ROTULO_ESTADO[info.estado] ?? info.estado)),
    metrica("Duração", info.duracao != null ? fmtDuracao(info.duracao) : ""),
  ];
  if (etapa.id === "E2" || etapa.id === "E3") {
    metricas.push(metrica("Rodadas", `${run.rodadas || 0} de ${estado.grafo.rodadas_extracao}`));
  }
  if (etapa.id === "E4") {
    const plano = estado.detalhes[run.run_id]?.plano;
    metricas.push(metrica("Rodadas do plano", plano ? `${plano.rodadas} de ${estado.grafo.rodadas_plano}` : ""));
    if (plano) metricas.push(metrica("Aviso", plano.aviso ? selo("erro", "com aviso") : selo("ok", "sem aviso")));
  }
  if (etapa.celulas) metricas.push(metrica("Passagens pelo nó", fmtInt(info.vezes)));
  const execucao = `<div class="cartao"><h3>Execução</h3><div class="metricas">${metricas.join("")}</div></div>`;
  const dados = (etapa.celulas ? gradeCelulas(etapa, run) : "") + dadosEtapa(etapa, run);
  return cabecalho + execucao + dados + terminalHtml(`saída de ${info.no}`, info.linhas ?? []);
}

function dadosEtapa(etapa, run) {
  const d = estado.detalhes[run.run_id];
  if (!d) {
    const texto = run.status === "rodando"
      ? "Os dados gravados no Supabase aparecem quando a primeira etapa terminar."
      : "Carregando os dados do run...";
    return `<div class="vazio">${texto}</div>`;
  }
  const m = d.manifest;
  switch (etapa.id) {
    case "E0":
      return cartaoDocumentoFonte(m);
    case "E1":
      return cartaoDocumento(d.documento);
    case "E2":
      return cartaoSpans(spansDoNo(d.spans, "extractor"), m) + cartaoClaims(d.factsheet);
    case "E3":
      return cartaoValidacoes(d.validacoes, d.factsheet);
    case "E4":
      return cartaoPlano(d.plano, d.factsheet) + cartaoValidacoesPlano(d.validacoes_plano, d.plano)
        + cartaoSpans(spansDoNo(d.spans, "adapter"), m);
    default:
      return "";
  }
}

function cartaoDocumento(doc) {
  if (!doc) return `<div class="vazio">O run não chegou a gravar o documento processado.</div>`;
  return `<div class="cartao"><h3>Documento processado</h3><div class="metricas">
    ${metrica("Tipo", esc(doc.tipo_documento), true)}
    ${metrica("Emissor", esc(doc.emissor))}
    ${metrica("Data", esc(doc.data_documento))}
    ${metrica("Páginas", `${fmtInt(doc.paginas)}${doc.paginas_ocr ? ` (${doc.paginas_ocr} com OCR)` : ""}`)}
    ${metrica("Chunks", fmtInt(doc.chunks))}
    ${metrica("Números na tabela", fmtInt(doc.numeros))}
  </div></div>`;
}

// Spans de um nó: cada etapa mostra só as chamadas do seu agente.
function spansDoNo(spans, no) {
  return (spans ?? []).filter((s) => s.no === no);
}

function cartaoSpans(spans, manifest) {
  if (!spans?.length) return `<div class="vazio">Nenhuma chamada de LLM gravada neste run.</div>`;
  const linhas = spans.map((s, i) => `
    <tr>
      <td class="num">${i + 1}</td>
      <td><code>${esc(s.no)}</code></td>
      <td><code>${esc(s.modelo)}</code></td>
      <td class="num">${fmtInt(s.tokens_entrada)}</td>
      <td class="num">${fmtInt(s.tokens_saida)}</td>
      <td class="num">${fmtBRL(s.custo_brl)}</td>
      <td class="num">${fmtDuracao(s.duracao_ms)}</td>
      <td>${s.falha_classe ? `${selo("erro", s.falha_classe)} <span class="motivo">${esc(s.falha_mensagem)}</span>` : selo("ok", "ok")}</td>
    </tr>`).join("");
  return `<div class="cartao"><h3>Chamadas de LLM (spans)</h3>
    <div class="metricas" style="margin-bottom:12px">
      ${metrica("Tokens do run", fmtInt(manifest.custo_total_tokens))}
      ${metrica("Custo do run", fmtBRL(manifest.custo_total_brl))}
    </div>
    <div class="tabela-rolagem"><table>
      <thead><tr><th>#</th><th>Nó</th><th>Modelo</th><th>Entrada</th><th>Saída</th><th>Custo</th><th>Duração</th><th>Falha</th></tr></thead>
      <tbody>${linhas}</tbody>
    </table></div></div>`;
}

function cartaoValidacoes(validacoes, factsheet) {
  if (!validacoes?.length) return `<div class="vazio">Nenhuma validação gravada neste run.</div>`;
  const rodadas = validacoes.map((v) => {
    const reprovados = v.claims.filter((c) => !c.passou);
    const chips = v.claims.map((c) => `<span class="claim-mini${c.passou ? "" : " reprovado"}"
      title="${esc(c.motivo ?? "aprovado")}">${esc(c.claim_id)}</span>`).join("");
    const motivos = reprovados.map((c) => `<li><code>${esc(c.claim_id)}</code>: <span class="motivo">${esc(c.motivo)}</span></li>`).join("");
    return `<div>
      <div class="rodada"><span class="rotulo">Rodada ${v.rodada}</span>
        ${selo(reprovados.length ? "erro" : "ok", reprovados.length ? `${reprovados.length} reprovado(s)` : "todos aprovados")}
        <span class="claims-mini">${chips}</span></div>
      ${motivos ? `<ul>${motivos}</ul>` : ""}
    </div>`;
  }).join("");
  const assinado = factsheet?.assinado
    ? selo("ok", `FactSheet assinado · ${factsheet.claims.length} claims`)
    : selo("erro", "FactSheet não assinado");
  return `<div class="cartao"><div class="cabecalho-etapa"><h3>Validações por rodada</h3>${assinado}</div>
    <div class="rodadas">${rodadas}</div></div>`;
}

function cartaoClaims(factsheet) {
  if (!factsheet?.claims?.length) return "";
  const linhas = factsheet.claims.map((c) => `
    <tr>
      <td class="num">${esc(c.id)}</td>
      <td>${esc(c.texto)}<div class="literal">${esc(c.literal_fonte)}</div></td>
      <td>${selo("normal", c.tipo)}<div class="etiquetas" style="margin-top:4px">${(c.tags ?? []).map((t) => selo("", t)).join("")}</div></td>
      <td class="num">${(c.numeros ?? []).map((n) => esc(n.bruto)).join("<br>")}</td>
      <td class="num">p. ${esc(c.ancora?.pagina)}</td>
    </tr>`).join("");
  return `<div class="cartao"><h3>Claims do FactSheet</h3><div class="tabela-rolagem"><table>
    <thead><tr><th>ID</th><th>Claim e trecho da fonte</th><th>Tipo</th><th>Números</th><th>Âncora</th></tr></thead>
    <tbody>${linhas}</tbody></table></div></div>`;
}

// Plano editorial da E4 (sunontent/adaptacao/): núcleo, aviso (o que a limpeza do código mudou) e, por nível, a
// tese, os claims usados em ordem de leitura com a prioridade, as analogias e os descartes. O texto de cada claim vem do FactSheet do run.
const ROTULO_NIVEL = { iniciante: "Iniciante", intermediario: "Intermediário", avancado: "Avançado" };
const ROTULO_MOTIVO = {
  metodologico: "metodológico", secundario: "secundário", redundante: "redundante",
  exige_base_tecnica: "exige base técnica", fora_do_foco: "fora do foco",
};

function contextoPlano(plano, factsheet) {
  const texto = Object.fromEntries((factsheet?.claims ?? []).map((c) => [c.id, c.texto]));
  const nucleo = new Set(plano.nucleo);
  const chip = (id) => `<span class="claim-mini${nucleo.has(id) ? " nucleo" : ""}" title="${esc(texto[id] ?? "")}">${esc(id)}</span>`;
  return { texto, nucleo, chip };
}

function cartaoPlano(plano, factsheet) {
  if (!plano) return `<div class="vazio">O run não chegou a gravar o plano editorial.</div>`;
  const ctx = contextoPlano(plano, factsheet);
  const { texto, chip } = ctx;
  const resumo = `<div class="cartao"><div class="cabecalho-etapa"><h3>Plano editorial</h3>
      ${plano.aviso ? selo("erro", "com aviso") : selo("ok", "sem aviso")}</div>
    <div class="metricas">
      ${metrica("Núcleo", `<span class="claims-mini">${plano.nucleo.map(chip).join("")}</span>`)}
      ${metrica("Rodadas do gate", fmtInt(plano.rodadas))}
    </div>
    ${plano.aviso ? `<p class="motivo aviso-plano">${esc(plano.aviso)}</p>` : ""}
    <ul class="nucleo-textos">${plano.nucleo.map((id) => `<li><code>${esc(id)}</code> ${esc(texto[id] ?? "")}</li>`).join("")}</ul>
  </div>`;
  return resumo + plano.niveis.map((p) => blocoNivelPlano(p, ctx)).join("");
}

// Plano de um nível: tese, claims usados em ordem de leitura, analogias e descartes.
function blocoNivelPlano(p, { texto, nucleo, chip }) {
  const usados = p.claims.map((c, i) => `<tr>
    <td class="num">${i + 1}</td>
    <td class="num">${esc(c.claim_id)}${nucleo.has(c.claim_id) ? `<div style="margin-top:4px">${selo("normal", "núcleo")}</div>` : ""}</td>
    <td>${selo("", c.papel)}</td>
    <td class="num">${esc(c.prioridade ?? "")}</td>
    <td>${esc(texto[c.claim_id] ?? "")}</td>
    <td>${c.atribuir_fonte ? selo("normal", "atribui a fonte") : ""}</td>
  </tr>`).join("");
  const analogias = p.analogias.length ? `<h4>Analogias</h4><div class="tabela-rolagem"><table>
    <thead><tr><th>Termo</th><th>Explicação</th><th>Origem</th><th>Revisão</th></tr></thead>
    <tbody>${p.analogias.map((a) => `<tr>
      <td>${esc(a.termo)}</td>
      <td>${esc(a.explicacao)}</td>
      <td>${selo(a.origem === "nova" ? "dev" : "normal", a.origem)}${a.glossario_ref ? `<div class="motivo-neutro"><code>${esc(a.glossario_ref)}</code></div>` : ""}</td>
      <td>${a.revisada ? selo("ok", "revisada") : selo("", "não revisada")}</td>
    </tr>`).join("")}</tbody></table></div>` : "";
  const descartes = p.descartes.length ? `<details class="descartes"><summary>Descartes (${p.descartes.length})</summary>
    <div class="tabela-rolagem"><table>
    <thead><tr><th>ID</th><th>Motivo</th><th>Origem</th><th>Claim</th></tr></thead>
    <tbody>${p.descartes.map((d) => `<tr>
      <td class="num">${esc(d.claim_id)}</td>
      <td>${selo(d.origem === "planejador" ? "normal" : "", ROTULO_MOTIVO[d.motivo] ?? d.motivo)}${d.relacionado ? `<div class="motivo-neutro">igual a <code>${esc(d.relacionado)}</code></div>` : ""}</td>
      <td>${esc(d.origem)}</td>
      <td>${esc(texto[d.claim_id] ?? "")}</td>
    </tr>`).join("")}</tbody></table></div></details>` : "";
  return `<div class="cartao"><div class="cabecalho-etapa"><h3>${esc(ROTULO_NIVEL[p.nivel] ?? p.nivel)}</h3>
      <span class="rotulo">${p.claims.length} claims · ${p.analogias.length} analogias · ${p.descartes.length} descartes</span></div>
    <p class="tese"><span class="rotulo">Tese</span> ${p.tese ? esc(p.tese) : `<span class="motivo-neutro">sem tese (retirada pela limpeza do plano)</span>`}
      <span class="claims-mini">${p.tese_claim_ids.map(chip).join("")}</span></p>
    <div class="tabela-rolagem"><table>
      <thead><tr><th>#</th><th>ID</th><th>Papel</th><th title="1 é o mais importante; o carrossel e o roteiro usam os de maior prioridade">Prioridade</th><th>Claim</th><th>Fonte</th></tr></thead>
      <tbody>${usados}</tbody></table></div>
    ${analogias}
    ${descartes}
  </div>`;
}

// Rodadas do gate da E4: os problemas devolvidos ao planejador. A rodada que passou não tem linha no banco.
function cartaoValidacoesPlano(validacoes, plano) {
  const porRodada = Object.fromEntries((validacoes ?? []).map((v) => [v.rodada, v.problemas]));
  const total = Math.max(plano?.rodadas ?? 0, ...Object.keys(porRodada).map(Number));
  if (!total) return "";
  const rodadas = Array.from({ length: total }, (_, i) => {
    const problemas = porRodada[i + 1] ?? [];
    const lista = problemas.map((p) => `<li>${selo("erro", p.regra)} ${esc(p.nivel ?? "plano")}:
      <span class="motivo">${esc(p.detalhe)}</span></li>`).join("");
    return `<div>
      <div class="rodada"><span class="rotulo">Rodada ${i + 1}</span>
        ${selo(problemas.length ? "erro" : "ok", problemas.length ? `${problemas.length} problema(s)` : "passou no gate")}</div>
      ${lista ? `<ul>${lista}</ul>` : ""}
    </div>`;
  }).join("");
  return `<div class="cartao"><h3>Gate do plano por rodada</h3><div class="rodadas">${rodadas}</div></div>`;
}

// Histórico

function renderHistorico() {
  const alvo = $("#historico");
  if (!estado.historico.length) {
    alvo.innerHTML = `<div class="vazio">Nenhum run no Supabase.</div>`;
    return;
  }
  const linhas = estado.historico.map((r) => `
    <tr class="clicavel" data-run="${esc(r.run_id)}" title="Abrir o resultado">
      <td class="num">${esc(r.run_id)}</td>
      <td class="num">${esc(fmtData(r.timestamp))}</td>
      <td>${esc(r.fonte)}</td>
      <td>${selo(r.modo, r.modo)}</td>
      <td>${selo(r.status, ROTULO_ESTADO[r.status] ?? r.status)}</td>
      <td class="num">${esc(r.doc_id ?? "")}</td>
      <td class="num">${fmtInt(r.claims)}</td>
      <td class="num">${fmtInt(r.descartados)}</td>
      <td class="num">${fmtInt(r.custo_total_tokens)}</td>
      <td class="num">${fmtBRL(r.custo_total_brl)}</td>
    </tr>`).join("");
  alvo.innerHTML = `<div class="cartao"><div class="tabela-rolagem"><table>
    <thead><tr><th>Run</th><th>Início</th><th>Fonte</th><th>Modo</th><th>Status</th><th>Documento</th><th>Claims</th><th>Claims descartados na E3</th><th>Tokens</th><th>Custo</th></tr></thead>
    <tbody>${linhas}</tbody></table></div></div>`;
}

// Dados do servidor

async function carregarGrafo() {
  try {
    estado.grafo = await api("/api/grafo");
    $("#grafo-arestas").innerHTML = estado.grafo.arestas.map((a) => `
      <span class="aresta${a.condicional ? " condicional" : ""}" title="${a.condicional ? "aresta condicional" : "aresta fixa"}">
        ${esc(a.de)} → ${esc(a.para)}</span>`).join("");
  } catch (erro) {
    avisar(`Não foi possível ler o grafo: ${erro.message}`, true);
  }
  agendarRender();
}

async function carregarHistorico() {
  try {
    estado.historico = await api("/api/runs");
  } catch (erro) {
    avisar(`Não foi possível listar os runs: ${erro.message}`, true);
  }
  if (estado.aba === "inicio") renderInicio();
  if (estado.aba === "historico") renderHistorico();
  if (estado.aba === "resultado") renderResultado();
  if (estado.aba === "tecnicas") renderTecnicas();
}

// Durante o run, várias leituras do mesmo run podem estar em andamento; só a mais recente vale.
const ultimaLeitura = {};

async function carregarDetalhe(runId) {
  const pedido = (ultimaLeitura[runId] ?? 0) + 1;
  ultimaLeitura[runId] = pedido;
  try {
    const detalhe = await api(`/api/runs/${encodeURIComponent(runId)}`);
    if (ultimaLeitura[runId] !== pedido) return;
    estado.detalhes[runId] = detalhe;
  } catch (erro) {
    avisar(`Não foi possível ler o run ${runId}: ${erro.message}`, true);
    return;
  }
  agendarRender();
  if (estado.aba === "inicio" && estado.historico[0]?.run_id === runId) renderInicio();
  if (estado.aba === "resultado" && estado.resultadoRun === runId) renderResultado();
  if (estado.aba === "tecnicas" && estado.resultadoRun === runId) renderTecnicas();
}

function conectar(desde) {
  estado.fonteEventos?.close();
  const fonte = new EventSource(`/api/execucao/eventos?desde=${desde}`);
  estado.fonteEventos = fonte;
  fonte.onmessage = (mensagem) => {
    const ev = JSON.parse(mensagem.data);
    aplicar(ev);
    if (ev.tipo === "fim") fonte.close();
  };
  fonte.onerror = () => {
    if (!rodando()) fonte.close();
  };
}

async function carregarExecucao() {
  try {
    const atual = await api("/api/execucao");
    if (!atual) return;
    estado.execucao = novaExecucao({ ...atual, estado: "rodando", codigo: null });
    for (const ev of atual.eventos) aplicar(ev);
    estado.execucao.estado = atual.estado;
    estado.execucao.codigo = atual.codigo;
    if (atual.estado === "rodando") conectar(atual.eventos.length);
  } catch (erro) {
    avisar(`Não foi possível ler a execução atual: ${erro.message}`, true);
  }
  agendarRender();
}

async function iniciar(dev) {
  try {
    const resumo = await api("/api/execucao", {
      method: "POST",
      body: JSON.stringify({ fonte: estado.fonte || null, dev }),
    });
    estado.execucao = novaExecucao(resumo);
    estado.runSelecionado = null;
    estado.subaba = "terminal";
    trocarAba("fluxo");
    conectar(0);
  } catch (erro) {
    avisar(erro.message, true);
  }
  agendarRender();
}

async function parar() {
  if (!confirm("Interromper o processo? O run em andamento fica como em_andamento no Supabase.")) return;
  try {
    await api("/api/execucao/parar", { method: "POST" });
  } catch (erro) {
    avisar(erro.message, true);
  }
}

// Eventos da página

// Endereço da aba na URL: #inicio, #fluxo/E3, #resultado/<run_id>/<celula_id>, #tecnicas/<run_id>, #historico.
const ABAS = ["inicio", "resultado", "fluxo", "tecnicas", "historico"];

function gravarEndereco() {
  const extras = {
    fluxo: [estado.subaba],
    resultado: [estado.resultadoRun, vista.celula],
    tecnicas: [estado.resultadoRun],
  }[estado.aba] ?? [];
  const caminho = extras.filter(Boolean).map(encodeURIComponent).join("/");
  history.replaceState(null, "", `#${estado.aba}${caminho ? `/${caminho}` : ""}`);
}

function lerEndereco() {
  const [aba, extra, extra2] = location.hash.slice(1).split("/");
  if (!ABAS.includes(aba)) { trocarAba("inicio"); return; }
  if (aba === "fluxo" && extra && (extra === "terminal" || ETAPAS.some((e) => e.id === extra))) estado.subaba = extra;
  if ((aba === "resultado" || aba === "tecnicas") && extra) estado.resultadoRun = decodeURIComponent(extra);
  if (aba === "resultado" && extra2) vista.celula = decodeURIComponent(extra2);
  trocarAba(aba);
}

function trocarAba(aba) {
  estado.aba = aba;
  for (const b of document.querySelectorAll(".aba")) b.setAttribute("aria-selected", String(b.dataset.aba === aba));
  for (const nome of ABAS) $(`#painel-${nome}`).hidden = nome !== aba;
  if (aba === "inicio") { renderInicio(); carregarHistorico(); carregarBiblioteca(); }
  if (aba === "resultado") { renderResultado(); carregarHistorico(); }
  if (aba === "tecnicas") { renderTecnicas(); carregarHistorico(); }
  if (aba === "historico") { renderHistorico(); carregarHistorico(); }
  if (aba === "fluxo") agendarRender();
  gravarEndereco();
}

document.addEventListener("click", (evento) => {
  const alvo = evento.target.closest("button, tr[data-run]");
  if (!alvo) return;
  if (alvo.dataset.ir) {
    if (alvo.dataset.run) estado.resultadoRun = alvo.dataset.run;
    trocarAba(alvo.dataset.ir);
  } else if (alvo.matches("#fontes button")) {
    estado.fonte = alvo.dataset.fonte;
    for (const b of document.querySelectorAll("#fontes button")) b.setAttribute("aria-checked", String(b === alvo));
    renderControles();
  } else if (alvo.id === "btn-executar") {
    iniciar(false);
  } else if (alvo.id === "btn-dev") {
    iniciar(true);
  } else if (alvo.id === "btn-parar") {
    parar();
  } else if (alvo.id === "btn-atualizar") {
    carregarHistorico();
  } else if (alvo.matches(".aba")) {
    trocarAba(alvo.dataset.aba);
  } else if (alvo.matches(".subaba")) {
    estado.subaba = alvo.dataset.subaba;
    gravarEndereco();
    agendarRender();
  } else if (alvo.matches(".etapa")) {
    estado.subaba = alvo.dataset.etapa;
    gravarEndereco();
    agendarRender();
  } else if (alvo.matches(".chip-run")) {
    estado.runSelecionado = alvo.dataset.run;
    agendarRender();
  } else if (alvo.matches("tr[data-run]")) {
    estado.resultadoRun = alvo.dataset.run;
    trocarAba("resultado");
  }
});

// Atualiza a duração da etapa em andamento.
setInterval(() => { if (rodando() && estado.aba === "fluxo") agendarRender(); }, 1000);

lerEndereco();
carregarGrafo();
carregarHistorico();
carregarExecucao();
