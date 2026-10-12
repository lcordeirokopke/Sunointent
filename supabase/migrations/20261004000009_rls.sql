-- RLS ligado em todas as tabelas, sem policies: só o service role acessa. Ele é usado pelo
-- pipeline e pelo servidor do dashboard (E8), que nunca envia a chave ao navegador.

alter table documentos            enable row level security;
alter table coletas               enable row level security;
alter table runs                  enable row level security;
alter table documentos_processados enable row level security;
alter table documento_paginas     enable row level security;
alter table chunks                enable row level security;
alter table factsheets            enable row level security;
alter table numeros               enable row level security;
alter table claims                enable row level security;
alter table claim_numeros         enable row level security;
alter table validacoes_ancora     enable row level security;
alter table claims_quarentena     enable row level security;
alter table decisoes_quarentena   enable row level security;
alter table conteudos_adaptados   enable row level security;
alter table celulas               enable row level security;
alter table pecas                 enable row level security;
alter table peca_unidade_claims   enable row level security;
alter table scorecards            enable row level security;
alter table metricas              enable row level security;
alter table violacoes             enable row level security;
alter table decisoes_humanas      enable row level security;
alter table materializacoes_figma enable row level security;
alter table pecas_publicadas      enable row level security;
alter table llm_spans             enable row level security;
alter table corpus_rotulado       enable row level security;
