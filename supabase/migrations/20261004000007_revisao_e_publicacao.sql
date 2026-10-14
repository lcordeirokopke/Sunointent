-- E8 (decisão humana), E9 (materialização no Figma) e peças publicadas.

-- Decisão humana por célula (docs/arquiteturas.md, E8. Revisão humana): registrada, nunca descartada.
-- Uma decisão por célula, final.
-- PENDENTE: a documentação não define schema; autor, motivo e vínculo com a tentativa são inferidos.
create table decisoes_humanas (
  id          bigint generated always as identity primary key,
  run_id      text not null,
  celula_id   text not null,
  tentativa   int,  -- nulo só para célula sem peça (ERRO_INFRA na primeira tentativa)
  decisao     decisao_humana not null,
  motivo      text,  -- ex: tom, oportunidade editorial, repetição (docs/arquiteturas.md, E8. Revisão humana)
  autor       text not null,  -- nome digitado no dashboard
  decidido_em timestamptz not null default now(),
  unique (run_id, celula_id),
  unique (id, run_id, celula_id, tentativa, decisao),  -- alvo das FKs de materializacoes_figma e pecas_publicadas
  foreign key (run_id, celula_id) references celulas (run_id, celula_id),
  foreign key (run_id, celula_id, tentativa) references pecas (run_id, celula_id, tentativa),
  check (tentativa is not null or decisao = 'descartar')
);

create trigger decisoes_humanas_imutavel
  before update or delete on decisoes_humanas
  for each row execute function bloquear_alteracao();

create trigger decisoes_humanas_sem_truncate
  before truncate on decisoes_humanas
  for each statement execute function bloquear_alteracao();

-- Decisão sobre analogia nova (docs/arquiteturas.md, Revisão das analogias novas e entrada no glossário):
-- seção separada do dashboard, uma decisão por analogia nova de um nível do run, final. As aprovadas são
-- lidas pelo comando que gera a alteração em config/glossario.yaml.
create table decisoes_analogia (
  id          bigint generated always as identity primary key,
  run_id      text not null,
  nivel       nivel not null,
  termo       text not null,
  explicacao  text not null,  -- o texto da analogia como estava no plano
  decisao     decisao_analogia not null,
  limite      text,           -- onde a analogia deixa de valer, escrito por quem aprova; vai para o glossário
  autor       text not null,  -- nome digitado no dashboard
  decidido_em timestamptz not null default now(),
  unique (run_id, nivel, termo),
  check (decisao <> 'aprovar' or nullif(btrim(limite), '') is not null),
  foreign key (run_id, nivel) references planos_nivel (run_id, nivel)
);

create trigger decisoes_analogia_imutavel
  before update or delete on decisoes_analogia
  for each row execute function bloquear_alteracao();

create trigger decisoes_analogia_sem_truncate
  before truncate on decisoes_analogia
  for each statement execute function bloquear_alteracao();

-- Materialização de carrossel publicado no Figma (docs/arquiteturas.md, E9. Materialização no Figma;
-- docs/figma-integracao.md).
-- PENDENTE: a documentação não define schema; colunas inferidas dos dados citados.
create table materializacoes_figma (
  id             bigint generated always as identity primary key,
  run_id         text not null,
  celula_id      text not null,
  tentativa      int not null,
  decisao_id     bigint not null,
  decisao        decisao_humana not null default 'publicar' check (decisao = 'publicar'),
  file_key       text not null,
  frame_node_id  text,
  node_ids       jsonb,  -- node ids devolvidos ao trace (docs/arquiteturas.md, E9. Materialização no Figma)
  rota           text not null check (rota in ('A', 'B')),  -- docs/figma-integracao.md, Rotas, em ordem de recomendação
  criado_em      timestamptz not null default now(),
  foreign key (run_id, celula_id, tentativa) references pecas (run_id, celula_id, tentativa),
  foreign key (decisao_id, run_id, celula_id, tentativa, decisao)
    references decisoes_humanas (id, run_id, celula_id, tentativa, decisao),
  check (split_part(celula_id, '__', 2) = 'carrossel')
);

-- Peças publicadas, para detecção de repetição por similaridade (pgvector).
-- PENDENTE: mecanismo de detecção de repetição não descrito na documentação; repetição aparece
-- só como motivo de descarte (docs/arquiteturas.md, E8. Revisão humana).
-- PENDENTE: dimensão do embedding. O texto publicado é o payload aprovado.
create table pecas_publicadas (
  id               bigint generated always as identity primary key,
  run_id           text not null,
  celula_id        text not null,
  tentativa        int not null,
  decisao_id       bigint not null,
  decisao          decisao_humana not null default 'publicar' check (decisao = 'publicar'),
  texto            text not null,
  embedding        extensions.vector,
  embedding_modelo text,
  publicado_em     timestamptz not null default now(),
  unique (run_id, celula_id),
  foreign key (run_id, celula_id, tentativa) references pecas (run_id, celula_id, tentativa),
  foreign key (decisao_id, run_id, celula_id, tentativa, decisao)
    references decisoes_humanas (id, run_id, celula_id, tentativa, decisao),
  check ((embedding is null) = (embedding_modelo is null))
);
