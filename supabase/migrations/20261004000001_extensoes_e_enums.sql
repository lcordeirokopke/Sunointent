-- Extensões e tipos fechados do contrato do pipeline.
-- Fonte dos valores: docs/arquiteturas.md (Objetos de dado) e docs/fluxos/ingestao.md.

create extension if not exists vector with schema extensions;

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
create type tipo_claim as enum ('decisao', 'projecao', 'condicionante', 'resultado', 'risco');

-- Quem produziu a linha de numeros ou claims: o pipeline (E1 e E2/E3) ou a revisão humana da
-- quarentena (docs/fluxos/extracao.md, Quarentena).
create type origem_dado as enum ('pipeline', 'humano');

-- Decisão humana sobre um claim em quarentena (docs/fluxos/extracao.md, Quarentena).
create type decisao_quarentena as enum ('aceitar', 'descartar');

-- Regras do gate do plano (docs/arquiteturas.md, E4.3 Complemento e gate do plano)
create type regra_plano as enum (
  'id_inexistente', 'contraditorio', 'nucleo_ausente', 'acima_do_limite', 'jargao_sem_analogia', 'motivo_invalido'
);

-- Slide.papel (docs/arquiteturas.md, Peça final)
create type papel_slide as enum ('gancho', 'corpo', 'conclusao');

-- CelulaState.estado (docs/arquiteturas.md, O objeto de estado)
create type estado_celula as enum ('PENDENTE', 'GERANDO', 'AVALIANDO', 'APROVADA', 'REPROVADA', 'ERRO_INFRA');

-- Scorecard.veredito (docs/arquiteturas.md, Scorecard e violação)
create type veredito as enum ('APROVADA', 'REPROVADA', 'ERRO_INFRA');

-- Modo de execução: normal (com E0) ou dev (documento de referência, --dev).
-- Campo modo do RunManifest (docs/arquiteturas.md, Run manifest).
create type modo_run as enum ('normal', 'dev');

-- Status do run. 'abortado' cobre falha do run inteiro, inclusive na E0 antes de existir documento.
create type status_run as enum ('em_andamento', 'concluido', 'abortado');

-- Classe de falha (docs/arquiteturas.md, 9. Failure handling; docs/fluxos/ingestao.md, Classificação de erros)
create type classe_falha as enum ('retriavel', 'terminal');

-- Decisão humana por célula na E8 (docs/arquiteturas.md, E8. Revisão humana: "publica ou descarta")
create type decisao_humana as enum ('publicar', 'descartar');

-- Decisão humana sobre analogia nova na E8 (docs/arquiteturas.md, Revisão das analogias novas e entrada no glossário)
create type decisao_analogia as enum ('aprovar', 'rejeitar');

-- Imutabilidade: bloqueia update, delete e truncate em tabelas append-only.
create or replace function bloquear_alteracao()
returns trigger
language plpgsql
as $$
begin
  raise exception 'tabela % é imutável: % não permitido', tg_table_name, tg_op;
end;
$$;
