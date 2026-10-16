# Glossário financeiro: `config/glossario.yaml`

O glossário reúne os termos técnicos das três fontes do projeto (atas do Copom, fatos relevantes da CVM e releases de resultados da B3), o nível a partir do qual cada termo dispensa explicação, as definições por nível e as analogias. É uma fonte única: o planejador da E4 e a M5 leem o mesmo arquivo, como acontece com o LevelSpec.

O arquivo explica conceitos, nunca fatos de um documento. Os fatos vêm só do FactSheet.

## Estado

O arquivo está montado e versionado no git. Todas as analogias estão em `rascunho`, à espera de revisão humana. Hoje leem o arquivo o `manifesto.py`, que registra a versão e o hash no `RunManifest`, e a E4, pelo `sunontent/glossario.py`, que também é o leitor previsto para a M5, ainda não implementada. Um teste lista as flexões dos termos encontradas nos claims das referências que não estão em `variantes` (`docs/testes.md`, Glossário).

## Formato e campos

```yaml
versao: 5
termos:
  - id: selic
    termo: Selic
    variantes:
      - taxa Selic
      - taxa básica de juros
    categoria: juros
    nivel_minimo: intermediario
    definicao_simples: "..."
    definicao_tecnica: "..."
    analogias:
      - texto: "..."
        limite: "..."
        status: rascunho
    ambiguo: false
    fonte:
      nome: "Banco Central do Brasil, Taxa Selic"
      url: "https://www.bcb.gov.br/controleinflacao/taxaselic"
```

| Campo | Conteúdo |
|---|---|
| `versao` | inteiro no topo do arquivo, escolhido à mão; entra no `RunManifest` como `glossario_versao`. |
| `id` | minúsculas, sem acento, com `_`; único no arquivo. |
| `termo` | forma canônica, com a grafia usada pelas próprias instituições. |
| `variantes` | outras formas que casam no texto: plural, sigla, nome por extenso, sinônimo de mercado. |
| `categoria` | `juros`, `inflacao`, `atividade`, `cambio`, `fiscal`, `mercado`, `contabil`, `societario` ou `regulatorio`. |
| `nivel_minimo` | menor nível em que o termo aparece sem explicação (ver abaixo). |
| `definicao_simples` | frase curta para o Iniciante. |
| `definicao_tecnica` | definição para Intermediário e Avançado. |
| `analogias` | lista de analogias, cada uma com `texto`, `limite` e `status`; vazia quando não há analogia. |
| `ambiguo` | `true` quando a palavra também tem sentido comum fora das finanças. |
| `fonte` | `nome` e `url` da fonte oficial ou institucional que sustenta a entrada. |

Todos os campos são obrigatórios. `analogias` pode ser uma lista vazia, mas não pode faltar.

### `nivel_minimo`

- `iniciante`: palavra do dia a dia (inflação, juros, poupança); dispensa analogia.
- `intermediario`: vocabulário padrão de mercado (Selic, CDI, IPCA, dividendos, JCP); pede analogia no Iniciante.
- `avancado`: jargão pleno (forward guidance, hiato do produto, EBITDA ajustado, covenants); também pede analogia no Iniciante.

### `status` da analogia

`rascunho` ou `aprovada`. Toda analogia entra como `rascunho`; só um humano aprova.

### Referência de analogia

Uma analogia é referida pela sua posição na lista do termo: `selic#1` é a primeira analogia de `selic`. Reordenar ou remover analogias muda as referências, e por isso sobe a `versao`.

### Organização do arquivo

Os termos ficam agrupados por categoria, cada grupo aberto por um comentário (`# ---- juros`). A ordem dentro do grupo não tem significado para os leitores.

## Regras de montagem

- **Escopo:** o glossário vale para qualquer documento das três fontes. Termo que só faz sentido num documento específico (nome de produto, programa ou métrica própria de um emissor) não entra.
- **Fonte:** toda entrada tem fonte oficial ou institucional cuja URL foi aberta: Banco Central (glossário, perguntas frequentes, páginas de controle da inflação, Relatório de Inflação e de Política Monetária, notas técnicas e atas do Copom), CVM (resoluções e notas explicativas), B3 (glossário do Bora Investir e glossário de tarifas), Lei das S.A., pronunciamentos do CPC e agências reguladoras. A URL aponta para a página do termo quando ela existe; senão, para a página do glossário ou da norma que o define. Termo sem fonte não entra.
- **Atemporal:** definições e analogias não têm números, datas, valores atuais nem citam decisões ou eventos específicos.
- **Sem dígitos:** definições e analogias não têm dígitos, porque a M1 trata todo número da peça como afirmação sobre o documento. O nome da bolsa aparece como "bolsa brasileira", e não como sigla com dígito.
- **Mecanismo, não causa:** o texto descreve o mecanismo geral ("juros mais altos tendem a encarecer o crédito"), nunca o motivo de uma decisão.
- **Definição simples:** não usa outro termo do glossário com `nivel_minimo` acima de iniciante.
- **Analogias:** só termos com `nivel_minimo` acima de iniciante têm analogia; o termo iniciante fica com `analogias: []`. Toda analogia preenche `limite`, que diz onde ela deixa de valer. A analogia não pode ensinar algo errado: sem analogia segura, a lista fica vazia.
- **Forma da analogia:** o `texto` começa com "É como..." e compara o conceito com algo do dia a dia, sem citar o próprio termo. A analogia `nova` do planejador segue a mesma forma.
- **Ambiguidade:** com `ambiguo: true`, só as formas de mais de uma palavra casam no texto (por exemplo, `ação` casa como "ações ordinárias" ou "mercado de ações", e não sozinha). Uma entrada cujo termo tem várias palavras também é marcada quando tem variante de uma palavra só com sentido comum.
- **Variantes:** cada forma (termo ou variante) pertence a uma única entrada. Sigla ou expressão que coincide com uma palavra comum do português em outro sentido não vira variante (por exemplo, `IR` coincide com o verbo "ir" e `AGE` com "age", de agir). Expressão que os documentos usam em outro sentido também fica de fora (por exemplo, `curva futura`, usada para o preço do petróleo, e `termos reais`, usada para salários).
- **YAML:** sigla que o YAML lê como booleano vai entre aspas (`"ON"`, de ação ordinária).

## Quem lê e como

| Leitor | Uso |
|---|---|
| `manifesto.py` | registra `versao` em `glossario_versao` e o SHA-256 do arquivo em `arquivos_sha256` do `RunManifest`. |
| E4.2, planejador | recebe, em cada claim, os termos acima de iniciante do texto com o `nivel_minimo` de cada um, e os termos acima de iniciante encontrados no texto dos candidatos do Iniciante que não têm nenhuma analogia no glossário; cria uma analogia `nova` para os que estiverem nos claims usados. |
| E4.3, complemento | no Iniciante, cada termo acima de iniciante encontrado no texto dos claims usados recebe a primeira analogia `aprovada` do termo ou, sem nenhuma aprovada, a primeira `rascunho`, com a referência `<id>#<n>`. |
| E4.3, gate | `jargao_sem_analogia`: todo termo acima de iniciante nos claims usados do Iniciante tem analogia no plano. `analogia_invalida`: a analogia `nova` não só repete as formas do termo. `motivo_invalido`: `exige_base_tecnica` só vale para claim com termo de `nivel_minimo` acima do nível, o que nunca acontece no Avançado. `tese_invalida`: a tese do Iniciante não tem termo acima de iniciante, e as formas dos termos ficam fora da busca de números da tese (o 15 de IPCA-15). |
| E4.3, limpeza | depois da última rodada, o claim do Iniciante com termo sem analogia sai do nível como `exige_base_tecnica`, e as analogias ficam só para os termos dos claims que restaram. |
| M5 | no Iniciante, todo termo acima de iniciante encontrado no texto precisa de `Analogia` no plano e de um marcador de analogia (`analogia.marcadores` do LevelSpec do Iniciante) na mesma unidade da primeira ocorrência, casando o termo pela [regra de casamento](#casamento-no-texto). |
| M6 | pode receber a definição do termo como referência ao julgar se a analogia está correta. |

Na `Analogia` do plano, `origem` é `glossario` ou `nova`, `glossario_ref` guarda a referência `<id>#<n>`, e `revisada` é `true` só para analogia do glossário com `status: aprovada`.

O extractor (E2) não consulta o glossário: ele lê só o documento.

### Casamento no texto

A E4 e a M5 usam a mesma regra para dizer se um termo aparece num texto. O casamento é por formas explícitas, sem lematizar:

- cada forma (o `termo` e cada variante) casa como está escrita, ignorando maiúsculas e acentos, com fronteira de palavra nos dois lados;
- a forma escrita só com maiúsculas, dígitos e sinais (sigla, como `CDI`, `IPCA-15` ou `NTN-B`) casa só com a caixa exata;
- plural e outras flexões só casam se estiverem em `variantes`;
- com `ambiguo: true`, só as formas de mais de uma palavra casam;
- quando uma forma está contida em outra no mesmo trecho, vale a mais longa (`taxa Selic` conta um termo, e não `taxa Selic` e `Selic`);
- os marcadores de número da peça (`{{<claim_id>.<ordem>}}`) não entram no casamento.

## Manutenção

### Incluir um termo

1. Confirmar que o termo serve para documentos das três fontes que ainda não foram vistos, e não só para um documento.
2. Achar a fonte oficial ou institucional e abrir a URL.
3. Escrever a entrada com todos os campos, seguindo as [regras de montagem](#regras-de-montagem), na seção da sua categoria.
4. Conferir que nenhuma forma nova já pertence a outra entrada e que nenhuma `definicao_simples` passou a conter a nova forma, se o termo estiver acima de iniciante.
5. Subir a `versao`.

### Revisar e aprovar analogias

A revisão humana troca `status: rascunho` por `status: aprovada` no próprio arquivo, sem mudar a posição da analogia na lista. Uma analogia enganosa é corrigida ou removida; remover muda as referências das seguintes, por isso sobe a `versao`.

### Quando subir a `versao`

A cada mudança de conteúdo: termo novo ou removido, definição, variante, nível, analogia ou status. O `RunManifest` guarda a versão e o hash; dois runs com a mesma versão e hashes diferentes revelam um arquivo editado sem subir a versão. O hash é calculado com fim de linha normalizado para `\n`, então trocar `\n` por `\r\n` não muda o hash.

### Termos fora do glossário

Aparecem nos documentos das três fontes, mas não têm fonte oficial ou institucional aberta que os defina:

- demanda agregada, política fiscal, desinflação, inflação cheia, inércia inflacionária, pleno emprego, produtividade do trabalho e taxa de ocupação;
- COE (as fontes dão só o nome por extenso) e ADV (aparece só nas demonstrações da B3);
- precatório, atualização monetária, tokenização, créditos de carbono, duplicata, comunicado ao mercado e leilão de frações.

Ficam de fora também, por serem próprios de um documento ou de um setor: RPC, Retail Liquidity Provider, SNG, ISE B3, Contratos de Eventos Financeiros, Trademate, B3RL, Método do Capital Recuperado, Base Regulatória de Ativos, ciclo de calibração e sistema ENET.
