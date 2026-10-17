-- Tipos fechados do contrato do pipeline.
-- Fonte dos valores: docs/arquiteturas.md (Objetos de dado) e docs/fluxos/ingestao.md.

-- Audiência (docs/arquiteturas.md, Nível e formato)
create type nivel as enum ('iniciante', 'intermediario', 'avancado');

-- Formato de mídia (docs/arquiteturas.md, Nível e formato)
create type formato as enum ('artigo', 'carrossel', 'roteiro');

-- Fonte de coleta da E0 (argumento da CLI: python -m sunontent <fonte>)
create type fonte as enum ('copom', 'cvm', 'b3');

-- Tipo de documento, 1:1 com a fonte (docs/arquiteturas.md, FactSheet; docs/fluxos/ingestao.md, Responsabilidade da E0)
create type tipo_documento as enum ('copom_ata', 'cvm_fato_relevante', 'b3_release');

-- Unidade de Numero (docs/arquiteturas.md, Número e âncora)
-- Na unidade 'data', cada componente numérico da data é um número, com o inteiro escrito como valor
-- (15 e 16 de setembro de 2026 dá 15, 16 e 2026). bps é gravado como 'pp' (1 bp = 0,01 p.p.).
create type unidade_numero as enum ('pct', 'pp', 'BRL', 'BRL_mi', 'x', 'contagem', 'data');

-- Claim.tipo (docs/arquiteturas.md, Claim)
create type tipo_claim as enum ('decisao', 'projecao', 'condicionante', 'resultado', 'risco', 'evento_corporativo');

-- Regras do gate do plano (docs/arquiteturas.md, E4.3 Complemento e gate do plano)
create type regra_plano as enum (
  'id_inexistente', 'contraditorio', 'nucleo_ausente', 'acima_do_limite', 'jargao_sem_analogia', 'motivo_invalido',
  'tese_invalida', 'analogia_invalida', 'prioridade_invalida'
);

-- Slide.papel (docs/arquiteturas.md, Peça final)
create type papel_slide as enum ('gancho', 'corpo', 'conclusao');

-- NumeroUsado.campo (docs/arquiteturas.md, Peça final): o número com marcador vale no texto das unidades e no título.
create type campo_numero as enum ('texto', 'titulo');

-- CelulaState.estado (docs/arquiteturas.md, O objeto de estado)
create type estado_celula as enum ('PENDENTE', 'GERANDO', 'AVALIANDO', 'APROVADA', 'REPROVADA', 'ERRO_INFRA');

-- CelulaState.motivo_reprovacao (docs/arquiteturas.md, E7. Router): só na célula REPROVADA.
create type motivo_reprovacao as enum ('k_esgotado', 'nao_convergencia');

-- Scorecard.veredito (docs/arquiteturas.md, Scorecard e violação)
create type veredito as enum ('APROVADA', 'REPROVADA', 'ERRO_INFRA');

-- MetricaResultado.nome e Violacao.metrica (docs/arquiteturas.md, Scorecard e violação)
create type metrica as enum (
  'numeric_fidelity', 'text_size', 'readability', 'term_density', 'analogy_rule', 'llm_judge', 'plan_contract'
);

-- Violacao.campo (docs/arquiteturas.md, Scorecard e violação): o campo da peça onde está o trecho.
create type campo_peca as enum ('titulo', 'linha_fina', 'texto', 'gancho_visual');

-- Modo de execução: normal (com E0) ou dev (documento de referência, --dev).
-- Campo modo do RunManifest (docs/arquiteturas.md, Run manifest).
create type modo_run as enum ('normal', 'dev');

-- Status do run. 'abortado' cobre falha do run inteiro, inclusive na E0 antes de existir documento.
create type status_run as enum ('em_andamento', 'concluido', 'abortado');

-- Classe de falha (docs/arquiteturas.md, 8. Failure handling; docs/fluxos/ingestao.md, Classificação de erros)
create type classe_falha as enum ('retriavel', 'terminal');

-- Imutabilidade: bloqueia update, delete e truncate em tabelas append-only.
create or replace function bloquear_alteracao()
returns trigger
language plpgsql
as $$
begin
  raise exception 'tabela % é imutável: % não permitido', tg_table_name, tg_op;
end;
$$;
