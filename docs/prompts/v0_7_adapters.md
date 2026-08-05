# Prompt da v0.7: adaptadores reais

> Documento de tarefa escrito antes do código, conforme a instrução do projeto.

## Contexto mínimo

`01` camada de adaptadores, `03` inteiro, `04`, `05` v0.7, e D003, D007, D016, D032 de `06`.

Estado: v0.6 fechada. 503 testes, `core` em 100 por cento de cobertura, e o pipeline roda de
CSV a relatório.

## Escopo

Importadores de TradingView e NinjaTrader, adaptador de série de mercado com cache e manifesto,
FRED, e calendários reais substituindo `WEEKDAYS_UTC`.

É a versão mais larga do roadmap e a única que toca rede. A regra de granularidade de `05`, uma
versão entrega um módulo de `core`, não se aplica: nada aqui entra em `core`.

## A tensão que decide o desenho

`04` proíbe dependência de rede em teste. `03` exige que todo download passe por cache com
manifesto de procedência. `05` pede prova de que o cache evita o segundo download. As três
coisas só coexistem de um jeito: **a rede fica atrás de um protocolo injetável**, e o cache é
testado com um buscador falso que conta chamadas.

Consequência de desenho: nenhuma função deste módulo faz `requests.get` diretamente. O cache
recebe um objeto que sabe buscar, chama uma vez, grava, e nas chamadas seguintes não chama. O
teste passa um contador e afirma que o contador ficou em um. Isso prova a propriedade que `05`
pede, e prova offline.

## Decisões de implementação

### 1. Cache imutável com manifesto append only

`03` fixa a estrutura: `data/raw/` exatamente como veio, `data/curated/` em parquet, e
`manifest.jsonl` com uma linha por recorte. A linha carrega fonte, símbolo, esquema, período,
timestamp do download, número de linhas, hash do arquivo e custo estimado.

Escolhido: chave de cache derivada de fonte, símbolo e recorte, e o arquivo bruto nomeado pelo
hash da chave, não pelo símbolo. Motivo: dois recortes diferentes do mesmo símbolo são dois
arquivos, e nomear por símbolo obrigaria a inventar convenção de sufixo que alguém vai quebrar.

O manifesto é append only e nunca reescrito. Uma linha por evento, inclusive quando o recorte
já existia, de modo que o histórico de acesso fique legível. Recorte já presente não gera
download e a linha registra isso.

### 2. TradingView precisa de código, NinjaTrader não

Este é o teste da aposta de D016. NinjaTrader exporta uma linha por round turn, com colunas de
entrada e de saída, logo é mapeamento e nada mais. TradingView exporta **duas linhas por
trade**, uma de entrada e uma de saída, com um número de trade ligando as duas. Isso é
pareamento, não renomeação de coluna.

Escolhido: acrescentar `row_layout` ao mapeamento, com `ONE_ROW_PER_TRADE` e
`TWO_ROWS_PER_TRADE`, e no segundo caso declarar qual coluna liga as linhas e quais valores
marcam entrada e saída. O pareamento vira caminho de código genérico, e as duas plataformas
continuam sendo arquivos de configuração.

Honestidade obrigatória: os mapeamentos das duas plataformas são escritos sem um export real à
mão. Vão marcados como modelo a verificar contra o próprio export do usuário, e a docstring diz
isso. Um mapeamento errado é exatamente o modo de falha que D017 documenta.

### 3. Calendário real vem de YAML versionado, não de dependência nova

Substituir `WEEKDAYS_UTC` exige feriados. As opções eram uma biblioteca de calendários, que
acrescenta dependência e não está instalada, ou um arquivo por venue.

Escolhido: arquivo. Um YAML com feriados, horário de fechamento e fechamentos antecipados,
validado com pydantic. Mesma lógica de D016: o dado que muda o resultado fica versionado ao
lado do código. Limitação declarada: a lista de feriados precisa de manutenção, e um feriado
faltando remove uma sessão da grade e muda `periods_per_year`.

O identificador do calendário já entra no `ValidationReport` desde a v0.1, então trocar de
calendário é visível no relatório.

## Critérios de aceitação

1. **O cache evita o segundo download, comprovadamente.** Buscador falso que conta chamadas,
   duas requisições do mesmo recorte, contador em um.
2. **O manifesto registra todo recorte**, com os campos que `03` lista, e é append only.
3. **O mapa de symbology alimenta multiplicador e tick mínimo** na validação de coerência.
   Já vale desde a v0.1 e o teste fica como regressão.
4. **Pareamento de duas linhas** recupera exatamente o mesmo `TradeLog` que o formato de uma
   linha, sobre dado equivalente. É o teste que prova que as duas rotas concordam.
5. **Calendário real produz menos sessões que o sentinela** no mesmo intervalo, e
   `sessions_per_year` cai na direção prevista.
6. **Nenhum teste toca a rede.** Verificável por inspeção e por não haver import de cliente HTTP
   fora do módulo de busca.
