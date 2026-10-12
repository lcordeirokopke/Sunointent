-- Run e RunManifest (docs/arquiteturas.md, Run manifest).
-- Uma linha por execução do grafo, criada pelo __main__.py antes da E0 (ou da leitura da referência no --dev).
-- doc_id, doc_sha256, coleta_id, url_origem e coletado_em são preenchidos uma vez, quando a E0 ou a
-- referência entrega o DocumentoFonte. O manifest é imutável depois de fechado; custos e status são
-- atualizados durante o run.

create table runs (
  run_id             text primary key,
  "timestamp"        timestamptz not null,
  modo               modo_run not null,
  fonte              fonte not null,
  status             status_run not null default 'em_andamento',

  -- Documento processado. Nulo só em run abortado antes de a E0 ou a referência entregar o documento.
  -- Nos dois modos o documento está em documentos: no --dev, o documento de referência é registrado
  -- em documentos e no bucket se ainda não estiver lá. No modo normal coleta_id aponta para a coleta
  -- do run; no --dev não há coleta.
  doc_id             text,
  doc_sha256         char(64) check (doc_sha256 ~ '^[0-9a-f]{64}$') references documentos (doc_sha256),
  coleta_id          bigint unique,
  url_origem         text,
  coletado_em        timestamptz,

  -- Versões (RunManifest)
  modelos            jsonb not null,  -- {"extractor": snapshot, "adapter": ..., "synthesizer": ..., "judge": ...}
  prompts            jsonb not null,  -- {"extractor": 1, "adapter": 3, ...}: versão do cabeçalho de cada template
  level_specs        jsonb not null,  -- {"iniciante": 1, "intermediario": 1, "avancado": 1}
  glossario_versao   int not null check (glossario_versao >= 1),
  arquivos_sha256    jsonb not null,  -- {"<caminho relativo>": "<sha256>"} de prompts/, config/levels/, glossario.yaml e models.yaml
  "K"                int not null check ("K" >= 1),

  -- Incrementados na mesma instrução que grava o span: with s as (insert into llm_spans ...
  -- on conflict (span_id) do nothing returning ...) update runs ... from s. Iguais à soma de llm_spans do run.
  custo_total_tokens bigint not null default 0,
  custo_total_brl    numeric(14, 6) not null default 0,

  -- Falha do run inteiro (E0, referência do --dev, leitura na E1, teto de orçamento).
  -- Preenchido só com status 'abortado'.
  erro_classe        classe_falha,
  erro_mensagem      text,

  criado_em          timestamptz not null default now(),

  check (run_id ~ ('^' || fonte::text || '_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{6}$')),
  check ((doc_id is null) = (doc_sha256 is null)),
  check ((doc_sha256 is null) = (url_origem is null) and (doc_sha256 is null) = (coletado_em is null)),
  check (status <> 'concluido' or doc_sha256 is not null),
  unique (run_id, doc_id, doc_sha256),  -- alvo da FK composta de documentos_processados
  check ((status = 'abortado') = (erro_classe is not null)),
  check ((erro_classe is null) = (erro_mensagem is null)),
  -- Documento conhecido: modo normal tem coleta, --dev não tem.
  check (case when doc_sha256 is null then coleta_id is null
              else (modo = 'normal') = (coleta_id is not null) end),
  foreign key (coleta_id, doc_id, doc_sha256, url_origem, coletado_em)
    references coletas (id, doc_id, doc_sha256, url_origem, coletado_em)
);

create index runs_doc_sha256_idx on runs (doc_sha256);
create index runs_doc_id_idx on runs (doc_id);
create index runs_timestamp_idx on runs ("timestamp" desc);

-- Checkpoint do LangGraph: as tabelas (checkpoints, checkpoint_blobs, checkpoint_writes,
-- checkpoint_migrations) são criadas pelo PostgresSaver.setup() do langgraph-checkpoint-postgres
-- no schema langgraph, fora dos schemas expostos pela Data API. A conexão do checkpointer é
-- separada da conexão das tabelas do projeto e usa search_path = langgraph, sem public.
-- Granularidade: um thread por célula (docs/arquiteturas.md, Granularidade do checkpoint),
-- thread_id = <run_id>:<celula_id>.
create schema if not exists langgraph;
revoke all on schema langgraph from public, anon, authenticated;
