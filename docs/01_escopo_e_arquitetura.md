# 01. Escopo e arquitetura

Nome do pacote: `qvalid` (provisório, ver D009 em `06_registro_de_decisoes.md`).

## Problema

Backtest isolado é uma realização de um processo estocástico. A questão relevante não é
"quanto rendeu", e sim: qual a distribuição de resultados compatível com esse histórico,
qual a probabilidade de o resultado ter vindo de sorte ou de busca excessiva por parâmetros,
e sob quais condições de mercado o resultado se sustenta.

`qvalid` responde essas três perguntas a partir de um log de trades.

## Não objetivos

Explicitamente fora do escopo, em qualquer versão:

- Motor de execução de ordens, roteamento, conexão com corretora.
- Backtester de sinal a partir de regras. O usuário traz o log de trades já gerado, seja de
  TradingView, NinjaTrader, script próprio ou operação manual.
- Geração de código de estratégia em DSL de plataforma (Pine, NinjaScript, PowerLanguage,
  EasyLanguage). Ver D006.
- Replicar arquivo institucional de tick e livro de ofertas.
- Otimização de parâmetros. A biblioteca julga estratégia, não busca parâmetro. Ela recebe o
  conjunto de tentativas justamente para penalizar a busca.
- Previsão de preço.
- Nota agregada única do tipo A a F. Ver `02` seção 7. Colapsar evidência heterogênea em uma
  letra é o defeito que este projeto existe para corrigir.

## Arquitetura em três camadas

    adapters/  ->  core/  ->  report/
    (fonte)        (cálculo)   (saída)

Regra de dependência: `core` não importa nada de `adapters` nem de `report`. `report` importa
`core`. `adapters` importa apenas os contratos de dados. A dependência é sempre para dentro.

Corolário operacional: qualquer informação externa de que `core` precise, como calendário de
negociação, multiplicador de contrato ou capital inicial, entra como **argumento tipado**,
nunca como importação. Função de `core` que precisa buscar algo está mal desenhada.

Consequência prática: o motor de validação roda sobre um CSV de log de trades sem nenhuma
conexão de rede. Isso torna a suíte de testes rápida, determinística e offline.

### Camada 1: adapters

Responsabilidade única: transformar fonte externa em contrato canônico.

- `adapters/tradelog.py` importador de CSV genérico dirigido por mapeamento declarativo em
  YAML. TradingView e NinjaTrader entram na v0.7 como mapeamentos, não como código novo. Ver D016.
- `adapters/symbology.py` mapa canônico de símbolo, com multiplicador, tick mínimo, moeda e
  calendário. É a origem dos dois números que a identidade de coerência precisa. Ver D007.
- `adapters/market/` séries de mercado de referência (ver `03_fontes_de_dados.md`).
- `adapters/macro/` FRED e EDGAR.
- `adapters/calendars/` materialização de `TradingCalendar` a partir do mapa de symbology.

Cada adaptador expõe uma função pura que devolve um contrato canônico e levanta exceção
tipada em caso de esquema inesperado. Nenhum adaptador faz cálculo estatístico.

Os adaptadores são também o lugar onde os invariantes de coerência do `TradeLog` são
verificados, porque a verificação depende de multiplicador e tick mínimo, que vivem no mapa
de symbology. Ver D007.

### Camada 2: core

- `core/constants.py` constantes nomeadas com derivação na docstring. Ver `02`.
- `core/gridding.py` projeção de `TradeLog` em `TradeReturns` e `PeriodReturns`.
- `core/metrics.py` estatísticas descritivas de performance.
- `core/resample.py` bootstrap estacionário e reamostragem por cadeia de Markov.
- `core/overfit.py` DSR, PBO, Reality Check, SPA.
- `core/regimes.py` rotulagem de regime e matriz de transição.
- `core/risk.py` VaR, ES, distribuição de drawdown, risco de ruína.
- `core/propfirm.py` simulador de regras de mesa proprietária.
- `core/verdict.py` agregação em veredito, incluindo equivalente certeza sob CPT.

### Camada 3: report

- `report/html.py` relatório autocontido, com gráficos embutidos.
- `report/latex.py` saída em LaTeX para uso acadêmico e para o portfólio.
- `report/json.py` serialização do `ValidationReport`.
- `cli.py` ponto de entrada `qvalid validate log.csv --config cfg.yaml --out relatorio.html`.

## Contratos canônicos

Todos os timestamps são tz aware em UTC. Nenhuma função aceita timestamp ingênuo.

### TradeLog

Um registro por trade fechado.

| campo       | tipo          | observação                                                   |
|-------------|---------------|--------------------------------------------------------------|
| trade_id    | str           | único                                                        |
| symbol      | str           | símbolo canônico, ver `03_fontes_de_dados.md`                |
| side        | enum          | LONG ou SHORT                                                |
| qty         | float         | positivo                                                     |
| multiplier  | float         | positivo, preenchido pelo adaptador a partir da symbology    |
| entry_ts    | datetime UTC  |                                                              |
| exit_ts     | datetime UTC  | maior ou igual a entry_ts                                    |
| entry_px    | float         |                                                              |
| exit_px     | float         |                                                              |
| fees        | float         | maior ou igual a zero, magnitude do custo total              |
| pnl         | float         | na moeda da conta, líquido de fees                           |
| tags        | dict          | livre, usado para agrupar por setup ou parâmetro             |

Ações e cripto têm `multiplier` igual a 1. Futuros têm o multiplicador do contrato. O campo é
obrigatório e não tem padrão, porque padrão silencioso aqui produz P&L errado por ordens de
grandeza sem levantar erro.

Se a moeda do instrumento diferir da moeda da conta, a conversão acontece no adaptador e a
taxa usada é registrada em `tags`. `core` assume moeda única.

**Invariantes verificados na fronteira.** Sem sobreposição de `trade_id`, `exit_ts` maior ou
igual a `entry_ts`, `qty` e `multiplier` positivos, `fees` não negativo, e coerência de P&L
pela identidade

    s = +1 se side == LONG, -1 se side == SHORT
    pnl_bruto = s * (exit_px - entry_px) * qty * multiplier
    residuo   = pnl - (pnl_bruto - fees)

aceita quando `abs(residuo) <= max(atol, rtol * abs(pnl_bruto))`, com
`atol = tick_size * multiplier * qty`, justificado por meia tick de arredondamento em cada
perna, e `rtol = 1e-6`. Violação levanta `TradeIntegrityError` com o resíduo no corpo da
mensagem. Ambos os parâmetros de tolerância entram no `ValidationReport`.

`core` assume o contrato válido e não revalida, conforme `04`.

### TradingCalendar

Sequência de sessões de negociação, tz aware em UTC, com identificador. Materializado em
`adapters/calendars/` a partir do calendário declarado no mapa de symbology.

Sentinela permitida na v0.1: `WEEKDAYS_UTC`, que trata todo dia útil como sessão. É um padrão,
não um silêncio: o identificador do calendário efetivamente usado entra no `ValidationReport`.

### TradeReturns

Retorno por trade, na ordem de execução. Índice é o número do trade, não o tempo.

Campos obrigatórios: `basis` (FIXED_INITIAL ou CURRENT_EQUITY) e `initial_capital`.

**Restrição dura.** Nenhuma estatística calculada sobre `TradeReturns` pode ser anualizada.
O índice não é tempo. Ver D006.

### PeriodReturns

Retorno por período de calendário. É a única origem admissível de qualquer número anualizado.

Campos obrigatórios: `period` (DAILY, WEEKLY, MONTHLY), `periods_per_year`, `calendar_id`,
`basis`, `initial_capital`, `active_fraction`.

Regra de composição: sob `FIXED_INITIAL` o retorno do período é a soma aritmética dos P&L
atribuídos dividida pelo capital inicial. Sob `CURRENT_EQUITY` o retorno do período compõe
multiplicativamente sobre a equity corrente, e equity menor ou igual a zero levanta
`TradeIntegrityError`, porque retorno percentual sobre base não positiva é indefinido.

Atribuição de P&L: no período que contém `exit_ts`. Marcação a mercado ao longo da posse
exigiria série de preço dentro de `core` e está fora de escopo em qualquer versão. Ver D006.

### TrialMatrix

Retornos por período de **todas** as configurações testadas, matriz `(n_periods, n_configs)`,
com identificador por coluna. A grade é declarada **uma vez** para a matriz inteira: `period`,
`periods_per_year`, `calendar_id`, `basis` e `initial_capital` são campos da matriz, não da
coluna.

Isso torna estrutural a pré condição de `02` seção 3, de que todas as configurações comparadas
estejam na mesma grade. Não existe estado representável em que duas colunas da mesma matriz
tenham `periods_per_year` diferente, logo comparar seus Sharpes não pode ser erro de unidade.
Ver D024.

Consumido por `core/overfit.py`. O método `column` extrai uma configuração como
`PeriodReturns` carregando a grade da matriz.

### EquityPaths

Array `(n_paths, n_steps)` de caminhos simulados, acompanhado da seed, do método de geração,
e de dois campos obrigatórios: `unit` (TRADE ou PERIOD) e, quando `unit` for PERIOD, o
`period` correspondente.

O campo `unit` existe porque `core/propfirm.py` implementa limite de perda diária e número
mínimo de dias operados. Essas regras só têm sentido sobre caminhos em unidade de calendário.
Simulador de mesa alimentado com caminhos em unidade de trade levanta erro tipado.

### RegimeLabels

Série indexada por timestamp com dois rótulos ordinais: estado de tendência e estado de
volatilidade. Rotulagem causal, calculada apenas com janela passada, terminando no período
**anterior**, de modo que o rótulo é conhecido antes de o período começar. Ver D026.

Campos obrigatórios: `window`, `warmup` e `reference_id`, porque rotular contra outra série de
referência ou com outra janela muda toda a atribuição. Todos entram no `ValidationReport`.

Períodos dentro do aquecimento carregam `UNDEFINED_STATE` e não um balde forçado. Eles saem da
atribuição com contagem e P&L reportados separadamente, de modo que a soma fecha.

Ordinal significa apenas que os valores de cada eixo são ordenados. O índice conjunto, que é
`tendência * n_volatilidade + volatilidade`, **não** é ordinal: o estado 4 de uma grade 3 por 3
não é melhor nem pior que o 3, é um par diferente.

### ValidationReport

> **Onde mora.** Em `report/model.py`, não em `contracts.py`. Ele agrega resultados de `core`,
> e `core` importa `contracts`, logo colocá lo entre os contratos fecharia um ciclo. A regra de
> dependência decide, não a taxonomia: o tipo que coleta resultados pertence à camada que os
> consome. A orquestração dos dez passos abaixo mora em `qvalid/pipeline.py`, a raiz de
> composição, único módulo autorizado a importar `adapters`, `core` e `report` ao mesmo tempo.
> Ver D029.
>
> **Painel de evidência.** Cada seção carrega ou um resultado ou um motivo de ausência, com os
> motivos enumerados em `RAN`, `SUPPRESSED`, `NOT_REQUESTED` e `FAILED`. O tipo recusa ter
> nenhum dos dois. É a forma estrutural da regra de `02` seção 7. Ver D031.


Dataclass serializável contendo: métricas, resultados de testes com p valor e intervalo,
diagnósticos, seed, número de réplicas, versão do pacote, hash do arquivo de entrada e
timestamp de execução.

Campos adicionais obrigatórios, porque cada um deles muda o resultado: `period`,
`periods_per_year`, `calendar_id`, `basis`, `initial_capital`, `active_fraction`,
`risk_free_rate`, largura de banda HAC usada, e as tolerâncias de coerência de P&L.

Sem isso o relatório não é reproduzível e não vale nada.

## Estrutura do repositório

    qvalid/
      src/qvalid/
        adapters/
        core/
        report/         # model.py, json.py, svg.py, html.py, latex.py
        contracts.py
        exceptions.py   # módulo folha, não importa nada do pacote
        pipeline.py     # raiz de composição, ver D029
        cli.py
      tests/
        unit/
        property/
        fixtures/      # CSV, symbology e mapeamento versionados, ver 04
      docs/
      examples/        # importa a biblioteca, nunca contém lógica própria
      data/            # não versionado, ver .gitignore
      pyproject.toml
      README.md
      CHANGELOG.md

## Fluxo de execução

1. Adaptador lê fonte, resolve symbology, verifica invariantes e devolve `TradeLog` validado.
2. Adaptador devolve `TradingCalendar`.
3. `gridding` projeta o log em `TradeReturns` e em `PeriodReturns`, escolhendo a grade pela
   regra de `02` seção 1.1 ou usando a grade forçada pelo usuário.
4. `metrics` calcula descritivas: as nativas por trade sobre `TradeReturns`, as anualizadas
   sobre `PeriodReturns`.
5. `regimes` rotula o período com a série de mercado de referência.
6. `resample` gera `EquityPaths` sob dois esquemas: bootstrap estacionário e cadeia de Markov
   por regime.
7. `risk` extrai distribuição de drawdown, VaR, ES e risco de ruína dos caminhos.
8. `overfit` aplica os testes que exigem o conjunto de tentativas, quando informado.
9. `verdict` agrega em veredito com incerteza declarada.
10. `report` serializa.

## Onde a interface entra

Depois da v1.0. A regra que preserva a opção: nenhuma lógica de cálculo pode viver na
interface. A interface só chama a API pública e renderiza o `ValidationReport`. Se essa regra
for respeitada, trocar CLI por web é trabalho de apresentação, não de reescrita.
