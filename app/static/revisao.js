"use strict";

// Aba da quarentena: a revisão humana dos claims reprovados depois da última rodada do loop E2/E3. A âncora e os
// números vêm sempre do código (sunontent/extracao/quarentena.py); quem revisa só confirma ou corrige o trecho e
// informa valor e unidade do número que a E1 não interpreta. Usa o estado e as utilidades de app.js.

const UNIDADES = ["pct", "pp", "BRL", "BRL_mi", "x", "contagem", "data"];

const revisao = { run: null, claim: null, preparo: null, erro: null, ocupado: false };

function pendentesDe(detalhe) {
  return (detalhe?.quarentena ?? []).filter((q) => !q.decisao);
}

function renderQuarentena() {
  const sel = $("#sel-run-quarentena");
  const comPendentes = estado.historico.filter((r) => r.quarentena);
  const atual = revisao.run ?? (comPendentes[0] ?? estado.historico[0])?.run_id ?? null;
  revisao.run = atual;
  sel.innerHTML = estado.historico.map((r) => `<option value="${esc(r.run_id)}" ${r.run_id === atual ? "selected" : ""}>
    ${esc(r.run_id)}${r.quarentena ? ` · ${r.quarentena} aguardando` : ""}</option>`).join("");
  const alvo = $("#quarentena");
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
  if (!d.quarentena?.length) {
    alvo.innerHTML = `<div class="vazio">Este run não tem claims em quarentena.</div>`;
    return;
  }
  const linhas = d.quarentena.map((q) => {
    const p = q.proposto ?? {};
    const acao = q.decisao
      ? `${selo(q.decisao === "aceitar" ? "ok" : "erro", q.decisao === "aceitar" ? "aceito" : "descartado")}
         <div class="motivo-neutro">${esc(q.autor)} · ${esc(fmtData(q.decidido_em))}</div>`
      : `<button type="button" class="botao btn-revisar" data-claim="${esc(q.claim_id)}"
           aria-pressed="${revisao.claim === q.claim_id}">Revisar</button>`;
    return `<tr>
      <td class="num">${esc(q.claim_id)}</td>
      <td>${esc(p.texto)}<div class="literal">${esc(p.literal_fonte)}</div></td>
      <td><span class="motivo">${esc(q.motivo)}</span></td>
      <td>${acao}</td></tr>`;
  }).join("");
  alvo.innerHTML = `<div class="cartao"><div class="cabecalho-etapa"><h3>Claims em quarentena</h3>
      ${selo(pendentesDe(d).length ? "erro" : "ok", pendentesDe(d).length ? `${pendentesDe(d).length} aguardando revisão` : "todos revisados")}</div>
    <div class="tabela-rolagem"><table>
      <thead><tr><th>ID</th><th>Claim proposto e trecho citado</th><th>Motivo da última recusa</th><th>Decisão</th></tr></thead>
      <tbody>${linhas}</tbody></table></div></div>
    ${revisao.claim ? painelRevisao(d) : ""}`;
}

function painelRevisao(d) {
  const item = d.quarentena.find((q) => q.claim_id === revisao.claim);
  if (!item) return "";
  const erro = revisao.erro ? `<p class="motivo">${esc(revisao.erro)}</p>` : "";
  if (!revisao.preparo) {
    return `<div class="cartao"><h3>Revisão de ${esc(item.claim_id)}</h3>${erro}<div class="vazio">Carregando a sugestão do código...</div></div>`;
  }
  const brutos = revisao.brutos ?? item.proposto.numeros ?? [];
  const numeros = revisao.preparo.numeros.map((n, i) => {
    const sugestao = n.numero
      ? `${esc(n.numero.valor)} ${esc(n.numero.unidade)} ${n.novo ? selo("dev", "novo, a confirmar") : selo("ok", "lido pela E1")}`
      : `${selo("erro", "informe valor e unidade")} <span class="motivo">${esc(n.motivo)}</span>`;
    return `<div class="numero-revisao" data-indice="${i}">
      <div><code>${esc(n.bruto)}</code> <span class="motivo-neutro">p. ${n.ancora.pagina}</span><div>${sugestao}</div></div>
      <input type="text" class="rev-valor" placeholder="valor (opcional)" aria-label="Valor de ${esc(n.bruto)}">
      <select class="rev-unidade" aria-label="Unidade de ${esc(n.bruto)}"><option value="">unidade</option>
        ${UNIDADES.map((u) => `<option value="${u}">${u}</option>`).join("")}</select></div>`;
  }).join("");
  return `<div class="cartao"><h3>Revisão de ${esc(item.claim_id)}</h3>${erro}
    <p class="descricao">${esc(item.proposto.texto)}</p>
    <div class="revisao">
      <label class="rotulo" for="rev-literal">Trecho citado da fonte (p. ${revisao.preparo.ancora.pagina})</label>
      <textarea id="rev-literal" rows="4">${esc(revisao.literal ?? revisao.preparo.literal_fonte)}</textarea>
      <label class="rotulo" for="rev-brutos">Números do claim, como aparecem no trecho (separados por ;)</label>
      <input type="text" id="rev-brutos" value="${esc(brutos.join("; "))}">
      <div class="linha-acoes"><button type="button" class="botao" id="rev-recalcular" ${revisao.ocupado ? "disabled" : ""}>Recalcular a sugestão</button></div>
      ${numeros || `<p class="motivo-neutro">O claim não tem número.</p>`}
      <p class="motivo-neutro">Sem valor e unidade informados, vale a sugestão do código. A âncora é sempre calculada pelo servidor.</p>
      <div class="linha-acoes">
        <button type="button" class="botao primario" id="rev-aceitar" ${revisao.ocupado ? "disabled" : ""}>Aceitar claim</button>
        <button type="button" class="botao perigo" id="rev-descartar" ${revisao.ocupado ? "disabled" : ""}>Descartar claim</button>
        <button type="button" class="botao fantasma" id="rev-fechar">Fechar</button>
      </div></div></div>`;
}

function lerFormulario() {
  const literal = $("#rev-literal").value;
  const brutos = $("#rev-brutos").value.split(";").map((b) => b.trim()).filter(Boolean);
  return { literal, brutos };
}

function consultaPreparo(literal, brutos) {
  const params = new URLSearchParams();
  if (literal != null) params.set("literal_fonte", literal);
  for (const b of brutos ?? []) params.append("brutos", b);
  return `/api/runs/${encodeURIComponent(revisao.run)}/quarentena/${encodeURIComponent(revisao.claim)}/preparo?${params}`;
}

async function carregarPreparo(literal = null, brutos = null) {
  const anterior = revisao.preparo;
  revisao.preparo = null;
  revisao.erro = null;
  renderQuarentena();
  try {
    revisao.preparo = await api(consultaPreparo(literal, brutos));
    revisao.literal = literal;
    revisao.brutos = brutos;
  } catch (erro) {
    revisao.erro = erro.message;
    revisao.preparo = anterior ?? { ancora: { pagina: "?" }, literal_fonte: literal ?? "", numeros: [] };
  }
  renderQuarentena();
}

async function decidir(acao) {
  const caminho = `/api/runs/${encodeURIComponent(revisao.run)}/quarentena/${encodeURIComponent(revisao.claim)}/${acao}`;
  let corpo;
  if (acao === "aceitar") {
    const { literal, brutos } = lerFormulario();
    const numeros = [...document.querySelectorAll("#quarentena .numero-revisao")].map((linha) => ({
      valor: linha.querySelector(".rev-valor").value.trim() || null,
      unidade: linha.querySelector(".rev-unidade").value || null,
    }));
    corpo = JSON.stringify({ literal_fonte: literal, brutos, numeros });
  } else if (!confirm("Descartar este claim? A decisão fica gravada e não pode ser desfeita.")) {
    return;
  }
  revisao.ocupado = true;
  revisao.erro = null;
  try {
    await api(caminho, { method: "POST", body: corpo });
    avisar(acao === "aceitar" ? `Claim ${revisao.claim} aceito.` : `Claim ${revisao.claim} descartado.`);
    revisao.claim = null;
    revisao.preparo = null;
    await Promise.all([carregarDetalhe(revisao.run), carregarHistorico()]);
  } catch (erro) {
    revisao.erro = erro.message;
  }
  revisao.ocupado = false;
  renderQuarentena();
}

document.addEventListener("click", (evento) => {
  const alvo = evento.target.closest("button");
  if (!alvo) return;
  if (alvo.matches(".btn-revisar")) {
    revisao.claim = alvo.dataset.claim;
    revisao.literal = null;
    revisao.brutos = null;
    carregarPreparo();
  } else if (alvo.id === "rev-recalcular") {
    const { literal, brutos } = lerFormulario();
    carregarPreparo(literal, brutos);
  } else if (alvo.id === "rev-aceitar") {
    decidir("aceitar");
  } else if (alvo.id === "rev-descartar") {
    decidir("descartar");
  } else if (alvo.id === "rev-fechar") {
    revisao.claim = null;
    revisao.preparo = null;
    revisao.erro = null;
    renderQuarentena();
  }
});

document.querySelector("#sel-run-quarentena").addEventListener("change", (evento) => {
  revisao.run = evento.target.value;
  revisao.claim = null;
  revisao.preparo = null;
  revisao.erro = null;
  gravarEndereco();
  renderQuarentena();
});
