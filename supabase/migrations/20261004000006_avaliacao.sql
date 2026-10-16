-- E6: Scorecard, MetricaResultado e Violacao (docs/arquiteturas.md, Scorecard e violação).

-- Um scorecard por tentativa com peça. A falha antes de existir peça não grava scorecard: o erro fica em celulas.
create table scorecards (
  run_id       text not null,
  celula_id    text not null,
  tentativa    int not null,
  veredito     veredito not null,  -- da tentativa; o estado final da célula fica em celulas
  custo_tokens bigint not null check (custo_tokens >= 0),  -- tokens das chamadas de LLM da tentativa
  duracao_ms   int not null check (duracao_ms >= 0),       -- do início da E5 ao fim da E6 da tentativa
  criado_em    timestamptz not null default now(),
  primary key (run_id, celula_id, tentativa),
  foreign key (run_id, celula_id, tentativa) references pecas (run_id, celula_id, tentativa)
);

-- MetricaResultado. valor é float | bool: uma das duas colunas é preenchida (valor_bool só na llm_judge).
create table metricas (
  id             bigint generated always as identity primary key,
  run_id         text not null,
  celula_id      text not null,
  tentativa      int not null,
  nome           metrica not null,
  valor_num      double precision,
  valor_bool     boolean,
  faixa_esperada text not null,
  passou         boolean not null,
  -- Um modelo Pydantic por métrica (sunontent/schemas.py, docs/metricas.md), com o nome no campo `metrica`.
  detalhes       jsonb not null,
  unique (run_id, celula_id, tentativa, nome),
  foreign key (run_id, celula_id, tentativa) references scorecards (run_id, celula_id, tentativa),
  check ((valor_num is null) <> (valor_bool is null)),
  check ((nome = 'llm_judge') = (valor_bool is not null))
);

-- Violacao. instrucao_corretiva é reinjetada no retry (docs/arquiteturas.md, O Refinement Loop).
-- A ordem do id é a ordem da lista de violações da métrica.
create table violacoes (
  id                  bigint generated always as identity primary key,
  metrica_id          bigint not null references metricas (id),
  metrica             metrica not null,
  trecho              text not null,
  esperado            text not null,
  obtido              text not null,
  instrucao_corretiva text not null,
  unidade_indice      int check (unidade_indice >= 0),  -- seção, slide ou cena; null no título, na linha fina e na peça inteira
  campo               campo_peca,
  inicio              int check (inicio >= 0),          -- intervalo do trecho no campo
  fim                 int,
  check ((inicio is null) = (fim is null)),
  check (fim is null or fim > inicio)
);

create index violacoes_metrica_idx on violacoes (metrica_id);
