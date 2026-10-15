-- Observabilidade (spans de LLM) e corpus rotulado de calibração.

-- Span por chamada de LLM (docs/arquiteturas.md, 1. Model client / router e 6. Observability / tracing).
-- PENDENTE: a documentação não define schema de span; colunas inferidas dos dados citados.
create table llm_spans (
  span_id        uuid primary key,  -- gerado em Python por chamada; repetir o insert do mesmo span não duplica
  run_id         text not null references runs (run_id),
  no             text not null check (no in ('extractor', 'adapter', 'synthesizer', 'judge')),
  nivel          nivel,             -- nulo no extractor (E2) e no adapter (E4), que planeja os 3 níveis numa chamada
  celula_id      text,              -- nulo antes do fan-out (E2, E4)
  tentativa      int check (tentativa >= 1),  -- Peca.tentativa; o retry de parse não muda este número
  rodada         int check (rodada between 1 and 3),  -- rodada do gate do plano; só no adapter
  modelo         text not null,
  tokens_entrada int not null check (tokens_entrada >= 0),  -- 0 quando a chamada falha sem resposta
  tokens_saida   int not null check (tokens_saida >= 0),
  custo_brl      numeric(14, 6) not null check (custo_brl >= 0),
  duracao_ms     int not null,
  falha_classe   classe_falha,
  falha_mensagem text,
  criado_em      timestamptz not null default now(),
  foreign key (run_id, celula_id) references celulas (run_id, celula_id),
  check (no <> 'extractor' or nivel is null),
  check (no in ('extractor', 'adapter') or nivel is not null),
  check ((no in ('synthesizer', 'judge')) = (celula_id is not null)),
  check ((celula_id is null) = (tentativa is null)),
  check (celula_id is null or split_part(celula_id, '__', 1) = nivel::text),
  check ((falha_classe is null) = (falha_mensagem is null)),
  check (rodada is null or no = 'adapter')
);

create index llm_spans_run_idx on llm_spans (run_id, celula_id);

-- Corpus rotulado por nível pela equipe editorial, para calibrar as faixas do LevelSpec e montar
-- a matriz de confusão de níveis (docs/arquiteturas.md, O LevelSpec e Lacunas conhecidas).
-- PENDENTE: a documentação não define schema; colunas inferidas.
create table corpus_rotulado (
  id             bigint generated always as identity primary key,
  texto          text not null,
  nivel_rotulado nivel not null,
  formato        formato,
  origem         text,
  rotulado_por   text not null,
  metricas       jsonb,  -- valores medidos de M1 a M5 sobre o texto
  criado_em      timestamptz not null default now()
);

-- LevelSpec, glossário, models.yaml e prompts ficam no git; o banco guarda só as versões no run.
