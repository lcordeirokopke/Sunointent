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
  { id: "E3", nos: ["validate"], nome: "Validador de Âncoras",
    descricao: "Confere âncoras e números de cada claim. Reprovado volta à E2; aprovado, o FactSheet é assinado." },
  { id: "E4", nos: ["adapt"], nome: "Audience Adapter",
    descricao: "Adapta o FactSheet para os 3 níveis: iniciante, intermediário e avançado." },
  { id: "E5", nos: ["synth"], nome: "Format Synthesizer",
    descricao: "Gera as 9 peças: texto, carrossel e roteiro de vídeo para cada nível." },
  { id: "E6", nos: ["evaluate"], nome: "Hybrid Evaluator",
    descricao: "Mede legibilidade, densidade de jargão e fidelidade factual (M1 a M6)." },
  { id: "E7", nos: ["router"], nome: "Router",
    descricao: "Aprova a célula, devolve para nova tentativa ou descarta conforme as métricas." },
  { id: "E8", nos: ["revisao"], nome: "Revisão humana",
    descricao: "O humano vê as 9 peças com rastreabilidade e métricas e decide publicar ou descartar." },
];
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
  aba: "fluxo",
  subaba: "terminal",
  grafo: { nos: [], arestas: [], rodadas_extracao: 3 },
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
        etapas: {}, atual: null, rodadas: 0, inicio: hora, fim: null, linhas: [], resumo: null, erro: null };
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
  return etapa.nos[0];
}

function infoEtapa(etapa, run) {
  if (!implementada(etapa)) return { estado: "nao-implementada", no: etapa.nos[0] };
  const no = noDaEtapa(etapa, run);
  const et = run?.etapas[no];
  if (!et) return { estado: "pendente", no };
  const duracao = et.total + (et.inicioAtual != null ? Date.now() - et.inicioAtual : 0);
  return { estado: et.estado, no, duracao, vezes: et.vezes, linhas: et.linhas };
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

  $("#comando").textContent = ativo
    ? `$ ${ex.comando}`
    : `$ python -m sunontent${estado.fonte ? ` ${estado.fonte}` : ""}   ·   modo dev: python -m sunontent${estado.fonte ? ` ${estado.fonte}` : ""} --dev`;
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
        <span class="rodape">
          ${selo(info.estado)}
          <span class="tempo">${info.duracao != null ? fmtDuracao(info.duracao) : ""}</span>
        </span>
        ${laco}
      </button>`;
  }).join("");
  $("#pipeline").innerHTML = html;
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
    if (ev.tipo === "etapa") classes.push(ev.estado === "iniciada" ? "iniciada" : "concluida");
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
    return `<div class="vazio">Nenhuma execução nesta sessão do dashboard.<br>
      Use <b>Executar run</b> para rodar o pipeline completo ou <b>Modo dev</b> para usar o documento de referência.</div>`;
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
  const execucao = `<div class="cartao"><h3>Execução</h3><div class="metricas">${metricas.join("")}</div></div>`;
  const dados = dadosEtapa(etapa, run);
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
      return `<div class="cartao"><h3>Documento fonte</h3><div class="metricas">
        ${metrica("Fonte", esc(m.fonte))}
        ${metrica("Modo", selo(m.modo, m.modo))}
        ${metrica("doc_id", esc(m.doc_id), true)}
        ${metrica("doc_sha256", m.doc_sha256 ? `<span title="${esc(m.doc_sha256)}">${esc(m.doc_sha256.slice(0, 16))}…</span>` : "", true)}
        ${metrica("Coletado em", esc(fmtData(m.coletado_em)))}
        ${metrica("coleta_id", esc(m.coleta_id ?? "sem coleta (dev)"), true)}
        ${metrica("Origem", m.url_origem ? `<a href="${esc(m.url_origem)}" target="_blank" rel="noopener">abrir PDF ↗</a>` : "")}
      </div></div>`;
    case "E1":
      return cartaoDocumento(d.documento);
    case "E2":
      return cartaoSpans(d.spans, m) + cartaoClaims(d.factsheet);
    case "E3":
      return cartaoValidacoes(d.validacoes, d.factsheet);
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

// Resultado

function renderResultado() {
  const sel = $("#sel-run");
  const atual = estado.resultadoRun ?? estado.historico[0]?.run_id ?? null;
  estado.resultadoRun = atual;
  sel.innerHTML = estado.historico.map((r) => `<option value="${esc(r.run_id)}" ${r.run_id === atual ? "selected" : ""}>
    ${esc(r.run_id)} · ${esc(ROTULO_ESTADO[r.status] ?? r.status)}</option>`).join("");
  const alvo = $("#resultado");
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
  const m = d.manifest;
  const erro = m.erro_mensagem ? `<div class="cartao"><div class="cabecalho-etapa"><h3>Erro</h3>${selo("erro", m.erro_classe)}</div>
    <p class="descricao">${esc(m.erro_mensagem)}</p></div>` : "";
  const versoes = Object.entries(m.arquivos_sha256 ?? {}).map(([arq, hash]) => `
    <tr><td><code>${esc(arq)}</code></td><td class="num" title="${esc(hash)}">${esc(hash.slice(0, 16))}…</td></tr>`).join("");
  const mapa = (obj) => Object.entries(obj ?? {}).map(([k, v]) => `${esc(k)}: <b>${esc(v)}</b>`).join(" · ") || "-";
  alvo.innerHTML = `<div class="resultado">
    <div class="cartao"><div class="cabecalho-etapa"><div><h2 class="mono">${esc(m.run_id)}</h2>
      <p class="descricao">${esc(fmtData(m.timestamp))}</p></div>
      <div>${selo(m.modo, m.modo)} ${selo(m.status, ROTULO_ESTADO[m.status] ?? m.status)}</div></div>
      <div class="metricas" style="margin-top:12px">
        ${metrica("Fonte", esc(m.fonte))}
        ${metrica("doc_id", esc(m.doc_id), true)}
        ${metrica("Claims", d.factsheet ? fmtInt(d.factsheet.claims.length) : "")}
        ${metrica("Rodadas E2/E3", fmtInt(d.validacoes.length || ""))}
        ${metrica("Tokens", fmtInt(m.custo_total_tokens))}
        ${metrica("Custo", fmtBRL(m.custo_total_brl))}
      </div>
    </div>
    ${erro}
    ${d.documento ? cartaoDocumento(d.documento) : ""}
    ${cartaoClaims(d.factsheet)}
    ${d.validacoes.length ? cartaoValidacoes(d.validacoes, d.factsheet) : ""}
    ${d.spans.length ? cartaoSpans(d.spans, m) : ""}
    <div class="cartao"><h3>Manifest: versões usadas no run</h3>
      <div class="metricas" style="margin-bottom:12px">
        ${metrica("Modelos", mapa(m.modelos))}
        ${metrica("Prompts", mapa(m.prompts))}
        ${metrica("LevelSpecs", mapa(m.level_specs))}
        ${metrica("Glossário", esc(m.glossario_versao))}
        ${metrica("K", esc(m.K))}
      </div>
      <div class="tabela-rolagem"><table><thead><tr><th>Arquivo</th><th>SHA-256</th></tr></thead><tbody>${versoes}</tbody></table></div>
    </div>
  </div>`;
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
      <td class="num">${fmtInt(r.custo_total_tokens)}</td>
      <td class="num">${fmtBRL(r.custo_total_brl)}</td>
    </tr>`).join("");
  alvo.innerHTML = `<div class="cartao"><div class="tabela-rolagem"><table>
    <thead><tr><th>Run</th><th>Início</th><th>Fonte</th><th>Modo</th><th>Status</th><th>Documento</th><th>Claims</th><th>Tokens</th><th>Custo</th></tr></thead>
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
  if (estado.aba === "historico") renderHistorico();
  if (estado.aba === "resultado") renderResultado();
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
  if (estado.aba === "resultado" && estado.resultadoRun === runId) renderResultado();
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

// Endereço da aba na URL: #fluxo, #fluxo/E3, #resultado/<run_id>, #historico.
function gravarEndereco() {
  const extra = estado.aba === "fluxo" ? estado.subaba : estado.aba === "resultado" ? estado.resultadoRun : null;
  history.replaceState(null, "", `#${estado.aba}${extra ? `/${extra}` : ""}`);
}

function lerEndereco() {
  const [aba, extra] = location.hash.slice(1).split("/");
  if (!["fluxo", "resultado", "historico"].includes(aba)) return;
  if (aba === "fluxo" && extra && (extra === "terminal" || ETAPAS.some((e) => e.id === extra))) estado.subaba = extra;
  if (aba === "resultado" && extra) estado.resultadoRun = decodeURIComponent(extra);
  trocarAba(aba);
}

function trocarAba(aba) {
  estado.aba = aba;
  for (const b of document.querySelectorAll(".aba")) b.setAttribute("aria-selected", String(b.dataset.aba === aba));
  for (const nome of ["fluxo", "resultado", "historico"]) $(`#painel-${nome}`).hidden = nome !== aba;
  if (aba === "resultado") { renderResultado(); carregarHistorico(); }
  if (aba === "historico") { renderHistorico(); carregarHistorico(); }
  if (aba === "fluxo") agendarRender();
  gravarEndereco();
}

document.addEventListener("click", (evento) => {
  const alvo = evento.target.closest("button, tr[data-run]");
  if (!alvo) return;
  if (alvo.matches("#fontes button")) {
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

$("#sel-run").addEventListener("change", (evento) => {
  estado.resultadoRun = evento.target.value;
  gravarEndereco();
  renderResultado();
});

// Atualiza a duração da etapa em andamento.
setInterval(() => { if (rodando() && estado.aba === "fluxo") agendarRender(); }, 1000);

lerEndereco();
carregarGrafo();
carregarHistorico();
carregarExecucao();
