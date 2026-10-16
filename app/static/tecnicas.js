"use strict";

// Página Informações técnicas: tudo o que um run gravou, de um run por vez: custos e chamadas de LLM, modelos e versões
// do manifest, arquivos com SHA-256, documento fonte e processado, validações por rodada, plano editorial e gate do
// plano. Usa o estado e as utilidades de app.js, que carrega depois deste arquivo.

function cartaoDocumentoFonte(m, completo = false) {
  const hash = !m.doc_sha256 ? "" : completo
    ? `<span class="hash">${esc(m.doc_sha256)}</span>`
    : `<span title="${esc(m.doc_sha256)}">${esc(m.doc_sha256.slice(0, 16))}…</span>`;
  return `<div class="cartao"><h3>Documento fonte</h3><div class="metricas">
    ${metrica("Fonte", esc(m.fonte))}
    ${metrica("Modo", selo(m.modo, m.modo))}
    ${metrica("doc_id", esc(m.doc_id), true)}
    ${metrica("doc_sha256", hash, true)}
    ${metrica("Coletado em", esc(fmtData(m.coletado_em)))}
    ${metrica("coleta_id", esc(m.coleta_id ?? "sem coleta (dev)"), true)}
    ${metrica("Origem", m.url_origem ? `<a href="${esc(m.url_origem)}" target="_blank" rel="noopener">abrir PDF ↗</a>` : "")}
  </div></div>`;
}

function renderTecnicas() {
  const atual = estado.resultadoRun ?? estado.historico[0]?.run_id ?? null;
  estado.resultadoRun = atual;
  $("#sel-run-tecnicas").innerHTML = opcoesDeRuns(atual);
  const alvo = $("#tecnicas");
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
  const gate = cartaoValidacoesPlano(d.validacoes_plano, d.plano)
    || `<div class="vazio">O run não gravou rodadas do gate do plano.</div>`;
  alvo.innerHTML = `<div class="resultado">
    ${cartaoResumoRun(d)}
    ${cartaoErroRun(d)}
    <h2 class="secao-titulo">Custos e chamadas de LLM</h2>
    ${cartaoCustos(d)}
    <h2 class="secao-titulo">Modelos, versões e arquivos</h2>
    ${cartaoVersoes(d.manifest)}
    ${cartaoArquivos(d.manifest)}
    <h2 class="secao-titulo">Documento</h2>
    ${cartaoDocumentoFonte(d.manifest, true)}
    ${cartaoDocumento(d.documento)}
    <h2 class="secao-titulo">Validações por rodada</h2>
    ${cartaoValidacoes(d.validacoes, d.factsheet)}
    <h2 class="secao-titulo">Plano editorial</h2>
    ${cartaoPlano(d.plano, d.factsheet)}
    ${gate}
  </div>`;
}

// Custos: resumo do run, soma por nó e modelo e todas as chamadas, com nível, célula, tentativa e rodada.
function cartaoCustos(d) {
  const spans = d.spans ?? [];
  if (!spans.length) return `<div class="vazio">Nenhuma chamada de LLM gravada neste run.</div>`;
  const m = d.manifest;
  const soma = (campo) => spans.reduce((total, s) => total + Number(s[campo] ?? 0), 0);
  const falhas = spans.filter((s) => s.falha_classe).length;

  const grupos = new Map();
  for (const s of spans) {
    const chave = `${s.no}\u0000${s.modelo}`;
    const g = grupos.get(chave) ?? { no: s.no, modelo: s.modelo, chamadas: 0, entrada: 0, saida: 0, custo: 0, duracao: 0, falhas: 0 };
    g.chamadas += 1;
    g.entrada += Number(s.tokens_entrada ?? 0);
    g.saida += Number(s.tokens_saida ?? 0);
    g.custo += Number(s.custo_brl ?? 0);
    g.duracao += Number(s.duracao_ms ?? 0);
    g.falhas += s.falha_classe ? 1 : 0;
    grupos.set(chave, g);
  }
  const porNo = [...grupos.values()].map((g) => `<tr>
      <td><code>${esc(g.no)}</code></td><td><code>${esc(g.modelo)}</code></td>
      <td class="num">${fmtInt(g.chamadas)}</td><td class="num">${fmtInt(g.entrada)}</td><td class="num">${fmtInt(g.saida)}</td>
      <td class="num">${fmtBRL(g.custo)}</td><td class="num">${fmtDuracao(g.duracao)}</td>
      <td>${g.falhas ? selo("erro", `${g.falhas} falha(s)`) : selo("ok", "ok")}</td></tr>`).join("");

  const todas = spans.map((s, i) => `<tr>
      <td class="num">${i + 1}</td>
      <td class="num">${esc(fmtHora(s.criado_em))}</td>
      <td><code>${esc(s.no)}</code></td>
      <td>${esc(s.nivel ?? "")}</td>
      <td class="num">${esc(s.celula_id ?? "")}</td>
      <td class="num">${esc(s.tentativa ?? "")}</td>
      <td class="num">${esc(s.rodada ?? "")}</td>
      <td><code>${esc(s.modelo)}</code></td>
      <td class="num">${fmtInt(s.tokens_entrada)}</td><td class="num">${fmtInt(s.tokens_saida)}</td>
      <td class="num">${fmtBRL(s.custo_brl)}</td><td class="num">${fmtDuracao(s.duracao_ms)}</td>
      <td>${s.falha_classe ? `${selo("erro", s.falha_classe)} <span class="motivo">${esc(s.falha_mensagem)}</span>` : selo("ok", "ok")}</td>
    </tr>`).join("");

  return `<div class="cartao"><h3>Resumo dos custos</h3><div class="metricas">
      ${metrica("Chamadas", fmtInt(spans.length))}
      ${metrica("Falhas", fmtInt(falhas))}
      ${metrica("Tokens de entrada", fmtInt(soma("tokens_entrada")))}
      ${metrica("Tokens de saída", fmtInt(soma("tokens_saida")))}
      ${metrica("Tokens do run", fmtInt(m.custo_total_tokens))}
      ${metrica("Custo do run", fmtBRL(m.custo_total_brl))}
      ${metrica("Tempo em chamadas", fmtDuracao(soma("duracao_ms")))}
    </div></div>
    <div class="cartao"><h3>Por nó e modelo</h3><div class="tabela-rolagem"><table>
      <thead><tr><th>Nó</th><th>Modelo</th><th>Chamadas</th><th>Entrada</th><th>Saída</th><th>Custo</th><th>Duração</th><th>Falha</th></tr></thead>
      <tbody>${porNo}</tbody></table></div></div>
    <div class="cartao"><h3>Todas as chamadas (${spans.length})</h3><div class="tabela-rolagem"><table>
      <thead><tr><th>#</th><th>Hora</th><th>Nó</th><th>Nível</th><th>Célula</th><th>Tent.</th><th>Rodada</th><th>Modelo</th><th>Entrada</th><th>Saída</th><th>Custo</th><th>Duração</th><th>Falha</th></tr></thead>
      <tbody>${todas}</tbody></table></div></div>`;
}

function tabelaChaveValor(titulo, objeto, cabecalho) {
  const linhas = Object.entries(objeto ?? {}).map(([chave, valor]) => `<tr><td><code>${esc(chave)}</code></td><td class="num">${esc(valor)}</td></tr>`).join("");
  return `<div class="cartao"><h3>${esc(titulo)}</h3>${linhas
    ? `<div class="tabela-rolagem"><table><thead><tr><th>${esc(cabecalho[0])}</th><th>${esc(cabecalho[1])}</th></tr></thead><tbody>${linhas}</tbody></table></div>`
    : `<p class="descricao">Nenhum registro.</p>`}</div>`;
}

// Manifest: os modelos por nó, as versões de prompts e LevelSpecs e os parâmetros do run.
function cartaoVersoes(m) {
  return `<div class="cartao"><h3>Manifest do run</h3><div class="metricas">
      ${metrica("Glossário (versão)", esc(m.glossario_versao))}
      ${metrica("K (retries)", esc(m.K))}
      ${metrica("Células em paralelo", esc(m.max_concorrencia))}
      ${metrica("Modo", selo(m.modo, m.modo))}
    </div></div>
    <div class="guia">
      ${tabelaChaveValor("Modelos por nó", m.modelos, ["Nó", "Modelo"])}
      ${tabelaChaveValor("Prompts", m.prompts, ["Template", "Versão"])}
      ${tabelaChaveValor("LevelSpecs", m.level_specs, ["Nível", "Versão"])}
    </div>`;
}

function cartaoArquivos(m) {
  const linhas = Object.entries(m.arquivos_sha256 ?? {}).map(([arquivo, hash]) => `
    <tr><td><code>${esc(arquivo)}</code></td><td class="hash">${esc(hash)}</td></tr>`).join("");
  return `<div class="cartao"><h3>Arquivos com SHA-256</h3>
    <p class="ajuda">Os arquivos de config/ e prompts/ usados no run, com o hash de cada um.</p>
    ${linhas ? `<div class="tabela-rolagem"><table><thead><tr><th>Arquivo</th><th>SHA-256</th></tr></thead><tbody>${linhas}</tbody></table></div>`
      : `<p class="descricao">Nenhum arquivo registrado.</p>`}</div>`;
}

document.querySelector("#sel-run-tecnicas").addEventListener("change", (evento) => {
  estado.resultadoRun = evento.target.value;
  gravarEndereco();
  renderTecnicas();
});
