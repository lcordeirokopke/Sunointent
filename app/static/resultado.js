"use strict";

// Página Resultado: as peças por nível (artigo, carrossel e roteiro) com o carrossel em SVG e a exportação em SVG e PNG;
// abaixo, a matriz 3x3 com estado, scorecard, violações por posição, rastreabilidade e plano do nível de cada célula,
// os claims e o documento processado. Usa o estado e as utilidades de app.js, que carrega depois deste arquivo.

const ROTULO_METRICA = {
  numeric_fidelity: "M1 · números", text_size: "M2 · tamanho", readability: "M3 · legibilidade",
  term_density: "M4 · termos", analogy_rule: "M5 · analogia", llm_judge: "M6 · juiz", plan_contract: "M7 · contrato",
};
const ORDEM_METRICAS = ["numeric_fidelity", "text_size", "readability", "term_density", "analogy_rule", "llm_judge", "plan_contract"];

// O que cada métrica mede e se reprova a célula (docs/metricas.md). `reprova`: "sim", "nao" ou "parcial" (com a condição).
const NOTA_METRICAS = [
  { nome: "numeric_fidelity", reprova: "sim",
    texto: "Todo número da peça que não seja data vem de um marcador de um claim do plano; datas (dia, ano, trimestre, reunião) são conferidas contra os claims e o ano do documento." },
  { nome: "text_size", reprova: "sim",
    texto: "Palavras do artigo, caracteres de cada slide e palavras de cada cena dentro dos limites do contrato de formato do nível." },
  { nome: "readability", reprova: "parcial", condicao: "só o piso do Iniciante",
    texto: "Índice de Facilidade de Leitura (Flesch adaptado ao português) das unidades. Só o Iniciante tem piso por formato e reprova abaixo dele; no Intermediário e no Avançado só mede. O nível que o IFL do artigo indica (Iniciante, Intermediário ou Avançado, pelas faixas do LevelSpec) é um sinal para a matriz de confusão e nunca reprova." },
  { nome: "term_density", reprova: "nao",
    texto: "Ocorrências de termos do glossário acima de iniciante por 100 palavras. Só mede." },
  { nome: "analogy_rule", reprova: "parcial", condicao: "Iniciante e Intermediário",
    texto: "Todo termo do glossário acima do nível (acima de iniciante no Iniciante, acima de intermediário no Intermediário) tem analogia ou explicação no plano e um marcador na mesma unidade da primeira ocorrência. No Avançado não se aplica." },
  { nome: "llm_judge", reprova: "parcial", condicao: "analogia incorreta ou unidade não implicada",
    texto: "Um modelo de outra família julga se cada analogia explica o conceito corretamente e se cada unidade é implicada pelos claims que cita. Só roda depois que M1, M2, M3, M5 e M7 passam, e só pode reprovar." },
  { nome: "plan_contract", reprova: "sim",
    texto: "A estrutura da peça confere com o plano: quantidade de slides, soma das cenas, claim_ids dentro do plano do nível e cobertura do formato." },
];
const EFEITO_METRICA = Object.fromEntries(NOTA_METRICAS.map((n) => [n.nome, n]));

function seloEfeito(nome) {
  const n = EFEITO_METRICA[nome];
  if (!n) return "";
  if (n.reprova === "sim") return selo("erro", "reprova");
  if (n.reprova === "nao") return selo("normal", "só mede");
  return selo("dev", `reprova: ${n.condicao}`);
}

function cartaoNotasMetricas() {
  const linhas = NOTA_METRICAS.map((n) => `<tr>
      <td>${esc(ROTULO_METRICA[n.nome])}</td><td>${esc(n.texto)}</td><td>${seloEfeito(n.nome)}</td></tr>`).join("");
  return `<details class="cartao"><summary><b>O que cada métrica mede e quais reprovam</b></summary>
    <p class="nota-aviso" role="note"><span class="nota-selo">Nota</span>A célula é aprovada quando nenhuma métrica que reprova acusa violação. As métricas determinísticas (M1 a M5 e M7) rodam em toda tentativa; a M4 e o nível percebido da M6 são só informativos.</p>
    <div class="tabela-rolagem"><table><thead><tr><th>Métrica</th><th>O que mede</th><th>Reprova?</th></tr></thead>
    <tbody>${linhas}</tbody></table></div></details>`;
}

const NOTA_JUIZ = `<p class="nota-aviso" role="note"><span class="nota-selo">Juiz (M6)</span>
  O juiz reprova a célula em dois casos: uma analogia do plano usada na peça não explica o conceito corretamente (é enganosa ou decorativa), ou uma unidade (seção, slide ou cena) diz algo que os claims que ela cita não sustentam: projeção apresentada como fato ou sem atribuição, causa, consequência ou comparação inventada, fato ou recomendação acrescentados, número por extenso diferente do claim (ou grandeza, período ou sujeito trocados) e contradição com um trecho. Explicação geral de conceito, pergunta retórica e transição não reprovam.
  Aprova quando todas as analogias estão corretas e todas as unidades são implicadas pelos claims.
  O <b>nível percebido</b> é só informativo: alimenta a matriz de confusão e não reprova nem aprova nada, então uma peça pedida como Intermediário e percebida como Avançado continua aprovada.</p>`;

const vista = { celula: null, tentativa: null, nivel: "iniciante", svgs: new Map() };

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

// Cabeçalho do run e o erro, usados em Resultado e em Informações técnicas.
function cartaoResumoRun(d) {
  const m = d.manifest;
  return `<div class="cartao"><div class="cabecalho-etapa"><div><h2 class="mono">${esc(m.run_id)}</h2>
      <p class="descricao">${esc(fmtData(m.timestamp))}</p></div>
      <div>${selo(m.modo, m.modo)} ${selo(m.status, ROTULO_ESTADO[m.status] ?? m.status)}</div></div>
      <div class="metricas" style="margin-top:12px">
        ${metrica("Fonte", esc(m.fonte))}
        ${metrica("doc_id", esc(m.doc_id), true)}
        ${metrica("Claims", d.factsheet ? fmtInt(d.factsheet.claims.length) : "")}
        ${d.descartados ? metrica("Claims descartados na E3", fmtInt(d.descartados)) : ""}
        ${metrica("Rodadas E2/E3", fmtInt(d.validacoes.length || ""))}
        ${d.plano ? metrica("Rodadas do plano", fmtInt(d.plano.rodadas)) : ""}
        ${metrica("Tokens", fmtInt(m.custo_total_tokens))}
        ${metrica("Custo", fmtBRL(m.custo_total_brl))}
      </div></div>`;
}

function cartaoErroRun(d) {
  const m = d.manifest;
  if (!m.erro_mensagem) return "";
  return `<div class="cartao"><div class="cabecalho-etapa"><h3>Erro</h3>${selo("erro", m.erro_classe)}
    ${m.etapa_erro ? `<span class="motivo">na etapa <code>${esc(m.etapa_erro)}</code></span>` : ""}</div>
    <p class="descricao">${esc(m.erro_mensagem)}</p></div>`;
}

function renderResultado() {
  const atual = estado.resultadoRun ?? estado.historico[0]?.run_id ?? null;
  estado.resultadoRun = atual;
  $("#sel-run").innerHTML = opcoesDeRuns(atual);
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
  alvo.innerHTML = `<div class="resultado">
    <h2 class="secao-titulo">Run</h2>
    ${cartaoResumoRun(d)}
    ${cartaoErroRun(d)}
    ${secaoPecas(d)}
    ${secaoAvaliacao(d)}
    <h2 class="secao-titulo">Claims e documento</h2>
    ${cartaoClaims(d.factsheet) || `<div class="vazio">O run não gravou o FactSheet.</div>`}
    ${cartaoDocumento(d.documento)}
  </div>`;
  carregarSlides();
}

// Peças por nível

function secaoPecas(d) {
  const titulo = `<h2 class="secao-titulo">Peças geradas</h2>`;
  const celulas = celulasDe(d);
  if (!celulas.length) {
    return `${titulo}<div class="vazio">O run não chegou a gravar as células (a E4 grava o plano e as 9 células).</div>`;
  }
  const porId = Object.fromEntries(celulas.map((c) => [c.celula_id, c]));
  const barra = NIVEIS.map(([nivel, rotulo]) => `<button type="button" role="radio" class="btn-nivel" data-nivel="${nivel}"
      aria-checked="${vista.nivel === nivel}">${rotulo}</button>`).join("");
  const blocos = FORMATOS.map(([formato, rotulo]) => blocoFormato(porId[celulaId(vista.nivel, formato)], formato, rotulo)).join("");
  return `${titulo}
    <div class="nivel-barra"><div class="segmentado" role="radiogroup" aria-label="Nível">${barra}</div></div>
    ${blocos}`;
}

function blocoFormato(celula, formato, rotulo) {
  const tentativa = celula ? ultimaTentativa(celula) : null;
  if (!tentativa) {
    return `<div class="cartao formato"><div class="formato-cabecalho"><h3>${esc(rotulo)}</h3>${celula ? selo(celula.estado) : selo("pendente")}</div>
      <div class="vazio">${celula ? "A célula não chegou a gravar uma peça." : "Célula ainda não gravada."}</div></div>`;
  }
  const p = tentativa.payload;
  let corpo = "";
  let acoes = "";
  if (formato === "artigo") {
    corpo = `<div class="leitura"><h4>${esc(p.titulo)}</h4><p class="linha-fina">${esc(p.linha_fina)}</p>
      ${p.secoes.map((s) => `<p>${esc(s.texto)}</p>`).join("")}</div>`;
  } else if (formato === "carrossel") {
    corpo = blocoSlides(celula, tentativa);
    acoes = `<span class="botoes">
      <button type="button" class="botao btn-todos" data-celula="${esc(celula.celula_id)}" data-formato="svg">↓ Todos em SVG</button>
      <button type="button" class="botao btn-todos" data-celula="${esc(celula.celula_id)}" data-formato="png">↓ Todos em PNG</button></span>`;
  } else {
    corpo = `<div class="cenas">${p.cenas.map((c, i) => `<div class="cena">
      <span class="tempo">Cena ${i + 1}<br><span class="cena-hora">${esc(c.inicio_s)} a ${esc(c.fim_s)} s</span></span>
      <div><div>${esc(c.fala)}</div><div class="gancho">${esc(c.gancho_visual)}</div></div></div>`).join("")}</div>`;
  }
  return `<div class="cartao formato"><div class="formato-cabecalho">
      <h3>${esc(rotulo)} <span class="motivo-neutro">· tentativa ${tentativa.tentativa}</span></h3>
      <span class="botoes">${selo(celula.estado)}</span>${acoes}</div>
    ${corpo}</div>`;
}

// Carrossel em SVG, SVG e PNG

function nomeDoSlide(celula, tentativa, n, extensao = "svg") {
  return `${celula.celula_id}_t${tentativa.tentativa}_slide${n}.${extensao}`;
}

function urlDoSlide(runId, celula, tentativa, n, baixar = false) {
  return `/api/runs/${encodeURIComponent(runId)}/carrossel/${encodeURIComponent(celula.celula_id)}/${n}.svg`
    + `?tentativa=${tentativa.tentativa}${baixar ? "&baixar=true" : ""}`;
}

function blocoSlides(celula, tentativa) {
  const total = tentativa.payload.slides.length;
  const blocos = Array.from({ length: total }, (_, i) => {
    const n = i + 1;
    const url = urlDoSlide(estado.resultadoRun, celula, tentativa, n);
    const guardado = vista.svgs.get(url);
    return `<div class="slide-bloco" data-slide="${esc(url)}" data-celula="${esc(celula.celula_id)}"
        data-nome="${esc(nomeDoSlide(celula, tentativa, n, "svg"))}">
      <div class="quadro">${guardado?.svg ?? ""}</div>
      <div class="estouro" ${guardado?.estouro ? "" : "hidden"}>O texto não cabe na altura da caixa: o limite de caracteres do LevelSpec e a caixa do template não batem.</div>
      <div class="acoes"><span>Slide ${n}</span><span class="botoes">
        <a class="botao fantasma" href="${esc(urlDoSlide(estado.resultadoRun, celula, tentativa, n, true))}"
          download="${esc(nomeDoSlide(celula, tentativa, n, "svg"))}" title="Salva o SVG, com o texto editável">↓ SVG</a>
        <button type="button" class="botao fantasma btn-png" title="Salva a imagem 1080 x 1350 em PNG">↓ PNG</button></span></div></div>`;
  }).join("");
  return `<p class="ajuda">Gerado na hora pelo servidor a partir da peça gravada. O SVG tem o texto editável no Inkscape ou no Illustrator
    (instale a fonte Inter); o PNG sai em 1080 x 1350. Nada é gravado.</p><div class="slides-faixa">${blocos}</div>`;
}

// Busca o SVG de cada slide ainda sem cache e o põe na página; o que a prévia mostra é o mesmo arquivo do download.
async function carregarSlides() {
  for (const bloco of document.querySelectorAll("#resultado .slide-bloco")) {
    const url = bloco.dataset.slide;
    const quadro = bloco.querySelector(".quadro");
    if (vista.svgs.has(url)) continue;
    try {
      const guardado = await buscarSlide(url);
      quadro.innerHTML = guardado.svg;
      bloco.querySelector(".estouro").hidden = !guardado.estouro;
    } catch (erro) {
      quadro.innerHTML = `<span class="motivo">Não foi possível gerar o slide: ${esc(erro.message)}</span>`;
    }
  }
}

async function buscarSlide(url) {
  if (vista.svgs.has(url)) return vista.svgs.get(url);
  const resposta = await fetch(url);
  if (!resposta.ok) throw new Error(`HTTP ${resposta.status}`);
  const guardado = { svg: await resposta.text(), estouro: resposta.headers.get("X-Estouro-Altura") === "true" };
  vista.svgs.set(url, guardado);
  return guardado;
}

// A fonte Inter vai dentro do SVG só na hora de gerar o PNG: o navegador não carrega fonte externa de um SVG usado como imagem.
let cssDasFontes = null;

function bufferParaBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let texto = "";
  for (let i = 0; i < bytes.length; i += 0x8000) texto += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(texto);
}

async function fontesEmbutidas() {
  if (cssDasFontes) return cssDasFontes;
  const faces = await Promise.all([["Inter-Regular.ttf", 400], ["Inter-Bold.ttf", 700]].map(async ([arquivo, peso]) => {
    const resposta = await fetch(`/static/fontes/${arquivo}`);
    if (!resposta.ok) throw new Error(`fonte ${arquivo}: HTTP ${resposta.status}`);
    const base64 = bufferParaBase64(await resposta.arrayBuffer());
    return `@font-face{font-family:"Inter";font-weight:${peso};src:url(data:font/ttf;base64,${base64}) format("truetype");}`;
  }));
  cssDasFontes = faces.join("");
  return cssDasFontes;
}

async function svgParaPng(svg) {
  const css = await fontesEmbutidas();
  const comFontes = svg.replace(/<svg[^>]*>/, (abre) => `${abre}<style>${css}</style>`);
  const url = URL.createObjectURL(new Blob([comFontes], { type: "image/svg+xml;charset=utf-8" }));
  try {
    const imagem = new Image();
    await new Promise((ok, falha) => {
      imagem.onload = ok;
      imagem.onerror = () => falha(new Error("o navegador não conseguiu desenhar o SVG"));
      imagem.src = url;
    });
    const tela = document.createElement("canvas");
    tela.width = 1080;
    tela.height = 1350;
    tela.getContext("2d").drawImage(imagem, 0, 0, 1080, 1350);
    return await new Promise((ok, falha) => tela.toBlob((b) => (b ? ok(b) : falha(new Error("não foi possível gerar o PNG"))), "image/png"));
  } finally {
    URL.revokeObjectURL(url);
  }
}

function salvarArquivo(blob, nome) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = nome;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 4000);
}

async function baixarSlide(url, nome, formato) {
  const { svg } = await buscarSlide(url);
  if (formato === "png") salvarArquivo(await svgParaPng(svg), nome.replace(/\.svg$/, ".png"));
  else salvarArquivo(new Blob([svg], { type: "image/svg+xml;charset=utf-8" }), nome);
}

async function baixarTodos(celulaIdAlvo, formato, botao) {
  const blocos = [...document.querySelectorAll(`#resultado .slide-bloco[data-celula="${CSS.escape(celulaIdAlvo)}"]`)];
  botao.disabled = true;
  try {
    for (const bloco of blocos) {
      await baixarSlide(bloco.dataset.slide, bloco.dataset.nome, formato);
      await new Promise((ok) => setTimeout(ok, 350));
    }
  } catch (erro) {
    avisar(`Não foi possível baixar os slides: ${erro.message}`, true);
  }
  botao.disabled = false;
}

const NOTA_SINAL_IFL = `<p class="nota-aviso" role="note"><span class="nota-selo">Sinal do IFL (M3)</span>
  O IFL é um <b>sinal de nível</b>: no artigo, o nível cuja faixa de IFL contém o IFL da peça; no carrossel e no roteiro, o estilo de Iniciante (IFL no piso do Iniciante e analogia na peça), porque o limite de caracteres encurta as frases de qualquer nível.
  O sinal só alimenta as matrizes e <b>não reprova nada</b>. O que reprova na M3 é o <b>piso de IFL do Iniciante</b>; o Intermediário e o Avançado só medem.</p>`;

// Matrizes de confusão do run, da última tentativa de cada célula: nível pedido contra o nível percebido pelo juiz (M6),
// contra o nível que o IFL do artigo indica e contra o estilo de Iniciante do carrossel e do roteiro (M3). Só informativas.
// `faixas` são as faixas de IFL do artigo por nível (LevelSpec), que o servidor manda com o run.
function cartaoConfusao(celulas, faixas) {
  const metricaDe = (c, nome) => ultimaTentativa(c)?.scorecard?.metricas?.find((m) => m.nome === nome);
  const nivelDoArtigo = (c) => {
    const d = metricaDe(c, "readability")?.detalhes;
    if (!d) return null;
    if (faixas && d.ifl != null) {
      const achado = Object.entries(faixas).find(([, f]) => (f.min == null || d.ifl >= f.min) && (f.max == null || d.ifl < f.max));
      if (achado) return achado[0];
    }
    return d.nivel_estatistico;
  };
  const tabela = (titulo, colunas, valor) => {
    const conta = {};
    let total = 0;
    for (const c of celulas) {
      const v = valor(c);
      if (!v) continue;
      conta[`${c.nivel}|${v}`] = (conta[`${c.nivel}|${v}`] ?? 0) + 1;
      total += 1;
    }
    if (!total) return `<div><h4>${titulo}</h4><p class="motivo-neutro">Nenhuma célula avaliada.</p></div>`;
    const linhas = NIVEIS.map(([nivel, rotulo]) => `<tr><th>${rotulo}</th>${colunas.map(([chave]) =>
      `<td class="num">${conta[`${nivel}|${chave}`] ?? 0}</td>`).join("")}</tr>`).join("");
    return `<div><h4>${titulo}</h4><div class="tabela-rolagem"><table><thead><tr><th>pedido / medido</th>${colunas.map(([, r]) =>
      `<th>${r}</th>`).join("")}</tr></thead><tbody>${linhas}</tbody></table></div></div>`;
  };
  return `<div class="cartao"><h3>Matrizes de confusão de níveis</h3>
    ${NOTA_JUIZ}
    ${NOTA_SINAL_IFL}
    <p class="motivo-neutro">Da última tentativa de cada célula. Não reprovam nada.</p>
    ${tabela("Nível percebido pelo juiz (M6), nos 3 formatos", NIVEIS, (c) => metricaDe(c, "llm_judge")?.detalhes?.nivel_percebido)}
    ${tabela("Nível do artigo pela faixa de IFL (M3), só sinal", NIVEIS, (c) => (c.formato === "artigo" ? nivelDoArtigo(c) : null))}
    ${tabela("Estilo de Iniciante no carrossel e no roteiro, pelo IFL e pela analogia (M3), só sinal",
      [["iniciante", "iniciante"], ["acima_de_iniciante", "acima de iniciante"]],
      (c) => (c.formato === "artigo" ? null : metricaDe(c, "readability")?.detalhes?.nivel_estatistico))}</div>`;
}

// Avaliação por célula

function secaoAvaliacao(d) {
  const titulo = `<h2 class="secao-titulo">Avaliação por célula</h2>
    <p class="nota-aviso" role="note"><span class="nota-selo">Aviso</span>Escolha uma célula para ver o scorecard, a peça com as marcações, a rastreabilidade e o plano do nível.</p>
    ${cartaoNotasMetricas()}`;
  const celulas = celulasDe(d);
  if (!celulas.length) return "";
  const porId = Object.fromEntries(celulas.map((c) => [c.celula_id, c]));
  const quadro = (nivel, formato, rotuloFormato) => {
    const c = porId[celulaId(nivel, formato)];
    if (!c) return `<td><span class="formato-rotulo">${rotuloFormato}</span>${selo("pendente")}</td>`;
    const t = ultimaTentativa(c);
    return `<td><button type="button" class="matriz-celula" data-celula="${esc(c.celula_id)}"
        aria-pressed="${vista.celula === c.celula_id}">
      <span class="formato-rotulo">${rotuloFormato}</span>
      ${selo(c.estado)}
      <span class="tentativa">${t ? `tentativa ${t.tentativa} · ${esc(t.scorecard?.veredito ?? "sem scorecard")}` : "sem peça"}</span>
      <span class="linha-metricas">${chipsMetricas(t)}</span></button></td>`;
  };
  const linhas = NIVEIS.map(([nivel, rotulo]) => `<tr><th>${rotulo}</th>${FORMATOS.map(([f, r]) => quadro(nivel, f, r)).join("")}</tr>`);
  return `${titulo}<div class="cartao"><h3>Matriz de saída</h3>
    <table class="matriz-grade"><thead><tr><th></th>${FORMATOS.map(([, rotulo]) => `<th>${rotulo}</th>`).join("")}</tr></thead>
    <tbody>${linhas.join("")}</tbody></table></div>
    ${vista.celula && porId[vista.celula] ? detalheCelula(porId[vista.celula], d) : `<div class="vazio">Escolha uma célula da matriz.</div>`}
    ${cartaoConfusao(celulas, d.sinal_ifl_artigo)}`;
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
    rotulo: `Cena ${i + 1} · ${c.inicio_s} a ${c.fim_s} s`, indice: i,
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
  return `<div class="cartao"><h3>Peça com marcações · tentativa ${tentativa.tentativa}</h3>
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
      <td>${seloEfeito(m.nome)}</td>
      <td>${selo(m.passou ? "ok" : "erro", m.passou ? "passou" : "reprovou")}</td></tr>`).join("");
  const juiz = metricas.some((m) => m.nome === "llm_judge");
  const violacoes = metricas.flatMap((m) => m.violacoes).map((v) => `<div class="violacao-item">
      ${selo("erro", ROTULO_METRICA[v.metrica] ?? v.metrica)}
      <b>${esc(v.trecho)}</b>
      <span class="motivo-neutro">${v.unidade_indice != null ? `unidade ${v.unidade_indice + 1} · ` : ""}${esc(v.campo ?? "")}${v.inicio != null ? ` [${v.inicio}-${v.fim}]` : ""}</span>
      <div>esperado: ${esc(v.esperado)} · obtido: ${esc(v.obtido)}</div>
      <div class="instrucao">${esc(v.instrucao_corretiva)}</div></div>`).join("");
  return `<div class="cartao"><div class="cabecalho-etapa"><h3>Scorecard · tentativa ${tentativa.tentativa}</h3>
      ${selo(sc.veredito)}</div>
    <div class="metricas" style="margin-bottom:12px">
      ${metrica("Tokens do juiz", fmtInt(sc.custo_tokens))}
      ${metrica("Duração", fmtDuracao(sc.duracao_ms))}
    </div>
    <div class="tabela-rolagem"><table><thead><tr><th>Métrica</th><th>Valor</th><th>Faixa esperada</th><th>Efeito</th><th></th></tr></thead>
    <tbody>${linhas}</tbody></table></div>
    ${juiz ? NOTA_JUIZ : ""}
    ${violacoes ? `<h4>Violações</h4>${violacoes}` : ""}</div>`;
}

function detalheCelula(celula, d) {
  const motivo = celula.estado === "REPROVADA" ? `<span class="motivo-neutro">motivo: ${esc(celula.motivo_reprovacao)}</span>`
    : celula.estado === "ERRO_INFRA" ? `<span class="motivo">${esc(celula.erro_classe)}: ${esc(celula.erro_mensagem)}</span>` : "";
  const cabecalho = `<div class="cartao"><div class="cabecalho-etapa"><div>
      <h2 class="mono">${esc(celula.celula_id)}</h2>
      <p class="descricao">${celula.tentativas.length} tentativa(s) gravada(s)</p></div>
      <div>${selo(celula.estado)} ${motivo}</div></div>`;
  const tentativa = celula.tentativas.find((t) => t.tentativa === vista.tentativa) ?? ultimaTentativa(celula);
  if (!tentativa) return `${cabecalho}</div><div class="vazio">A célula não chegou a gravar uma peça.</div>`;
  const botoes = celula.tentativas.map((t) => `<button type="button" class="botao fantasma btn-tentativa"
      data-tentativa="${t.tentativa}" aria-pressed="${t === tentativa}">Tentativa ${t.tentativa}${t.scorecard ? ` · ${esc(t.scorecard.veredito)}` : ""}</button>`).join("");
  const planoNivel = d.plano?.niveis?.find((p) => p.nivel === celula.nivel);
  return `${cabecalho}<div class="barra-tentativas">${botoes}</div></div>
    ${blocoScorecard(tentativa)}
    ${blocoPeca(celula, tentativa, d)}
    ${planoNivel ? blocoNivelPlano(planoNivel, contextoPlano(d.plano, d.factsheet)) : ""}`;
}

document.addEventListener("click", (evento) => {
  const nivel = evento.target.closest(".btn-nivel");
  if (nivel) {
    vista.nivel = nivel.dataset.nivel;
    renderResultado();
    return;
  }
  const celula = evento.target.closest(".matriz-celula");
  if (celula) {
    vista.celula = celula.dataset.celula;
    vista.tentativa = null;
    renderResultado();
    gravarEndereco();
    return;
  }
  const tentativa = evento.target.closest(".btn-tentativa");
  if (tentativa) {
    vista.tentativa = Number(tentativa.dataset.tentativa);
    renderResultado();
    return;
  }
  const png = evento.target.closest(".btn-png");
  if (png) {
    const bloco = png.closest(".slide-bloco");
    png.disabled = true;
    baixarSlide(bloco.dataset.slide, bloco.dataset.nome, "png")
      .catch((erro) => avisar(`Não foi possível gerar o PNG: ${erro.message}`, true))
      .finally(() => { png.disabled = false; });
    return;
  }
  const todos = evento.target.closest(".btn-todos");
  if (todos) baixarTodos(todos.dataset.celula, todos.dataset.formato, todos);
});

document.querySelector("#sel-run").addEventListener("change", (evento) => {
  estado.resultadoRun = evento.target.value;
  vista.celula = null;
  vista.tentativa = null;
  gravarEndereco();
  renderResultado();
});
