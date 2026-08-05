# Prompt da v0.2: `core/resample.py`

> Documento de tarefa escrito antes do código, conforme a instrução do projeto. Contém o
> escopo, as decisões de implementação com trade off, os fatos já verificados numericamente,
> e os critérios de aceitação. Serve tanto para execução direta quanto para ser colado em
> Claude Code como especificação de tarefa.

## Contexto mínimo necessário

Leia, nesta ordem: `01` seção de contratos (`TradeReturns`, `PeriodReturns`, `EquityPaths`),
`02` seção 2.1, `04` inteiro, `05` v0.2, e as entradas D006, D013 e D015 de `06`.

Estado do repositório: v0.1 fechada. 271 testes, `core` em 100 por cento de cobertura.
Existem `contracts.py`, `exceptions.py`, `core/constants.py`, `core/gridding.py`,
`core/metrics.py`, `adapters/{validation,calendars,symbology,tradelog}.py`.

## Escopo

Bootstrap estacionário de Politis e Romano (1994) com comprimento de bloco esperado estimado
automaticamente por Politis e White (2004), gerando `EquityPaths` com `unit` propagada da
entrada. A reamostragem por cadeia de Markov entre regimes, `02` seção 2.2, **não** entra
aqui: ela depende de `core/regimes.py` e fica na v0.5. `resample.py` será estendido lá.

Fora de escopo: qualquer estatística sobre os caminhos. VaR, ES, drawdown, risco de ruína e
Kelly são v0.3, `core/risk.py`.

## Decisões de implementação, com o trade off explícito

### 1. Geração de índices totalmente vetorizada, sem laço em `t`

A forma direta do bootstrap estacionário é recursiva: no passo `t`, com probabilidade
`p = 1/b` sorteia nova âncora, senão continua do índice anterior mais um, módulo `n`. Isso é
um laço de comprimento `T`.

Escolhido: eliminar o laço com acumulação de máximo.

    novo   = rng.random((P, T)) < p          # coluna 0 forçada a True
    t      = arange(T)
    ultimo = maximum.accumulate(where(novo, t, -1), axis=1)
    offset = t - ultimo
    ancora = take_along_axis(ancoras, ultimo, axis=1)
    indice = (ancora + offset) % n

Trade off. Custo de memória sobe para três matrizes `(P, T)` simultâneas em vez de uma coluna
por vez. Com `P = 10000` e `T = 2000` isso é cerca de 480 MB de pico em `int64`. A regra de
performance de `04` manda vetorizar primeiro e medir antes e depois, então a medição entra no
registro e o refinamento por blocos de caminhos fica para quando a medição justificar.

### 2. `block_length = 1` **é** o bootstrap i.i.d.

Com `b = 1` vale `p = 1` e todo passo sorteia âncora nova, o que é exatamente reamostragem
i.i.d. com reposição. Escolhido: não escrever uma segunda função. O teste de comparação
exigido por `02` 2.1 passa a ser `b = 1` contra `b = b_hat` no mesmo caminho de código, o que
elimina a possibilidade de a comparação medir diferença de implementação em vez de diferença
de método.

### 3. Os caminhos são níveis de equity, não retornos

`01` chama o contrato de `EquityPaths` e `core/risk.py` precisa de drawdown e de barreira
absorvente, que só existem sobre nível. Escolhido: `(n_paths, n_steps)` com `n_steps = T + 1`
e coluna zero igual ao capital inicial, construído pela mesma regra de base que
`metrics.equity_curve`, aditiva sob `FIXED_INITIAL` e multiplicativa sob `CURRENT_EQUITY`.
Retorno reamostrado é recuperável por diferença ou razão, então nada se perde.

Trade off. Uma coluna a mais por caminho. Em troca, o caminho simulado e o caminho observado
saem da mesma regra e são diretamente comparáveis, o que é o que a v0.3 precisa para posicionar
o drawdown observado como quantil da distribuição simulada.

### 4. O contrato `EquityPaths` não ganha campo novo

Guardar nível absoluto em moeda de conta torna `initial_capital` recuperável de `values[:, 0]`
e a base implícita na construção. Escolhido: não estender o contrato de `01`. Menos churn e
nenhuma informação perdida.

## Fatos já verificados numericamente, use e não re-derive

A estimativa de comprimento de bloco implementada é a de Politis e White (2004) com janela de
topo plano

    lam(t) = 1              para |t| <= 1/2
    lam(t) = 2 * (1 - |t|)  para 1/2 < |t| <= 1
    lam(t) = 0              caso contrário

com `K = max(5, ceil(sqrt(log10 n)))`, limiar de significância `2 * sqrt(log10(n) / n)`,
`M_max = ceil(sqrt(n)) + K`, `M = min(2 * m_hat, M_max)`, e

    g_hat = soma_k lam(k/M) * R(k)
    G_hat = soma_k lam(k/M) * |k| * R(k)
    D_SB  = 2 * g_hat^2
    b_opt = ( 2 * G_hat^2 / D_SB )^(1/3) * n^(1/3)

truncado em `B_max = ceil(min(3 * sqrt(n), n / 3))`.

**Medições feitas antes de escrever o módulo.** Não repita, cite.

`b_opt` médio sob AR(1), `n = 2000`, 60 réplicas:

| rho | 0.0  | 0.2  | 0.4   | 0.6   | 0.8   |
|-----|------|------|-------|-------|-------|
| b   | 1.39 | 5.44 | 10.86 | 17.56 | 32.49 |

Escala em `n` sob rho igual a 0.5. Razões observadas entre tamanhos sucessivos que dobram:
1.251, 1.307, 1.315, 1.297, contra `2^(1/3) = 1.260` previsto pela teoria.

Validação forte, e é ela que deve virar teste. Sob AR(1) com rho igual a 0.5 e `n = 1000`, a
variância de longo prazo verdadeira é `1/(1-rho)^2 = 4.0`. Varrendo `b` por força bruta e
minimizando o erro quadrático médio do estimador exato do bootstrap estacionário,

    w(k) = ((n-|k|)/n) * (1-1/b)^|k| + (|k|/n) * (1-1/b)^(n-|k|)

o argmin observado é `b = 10` e a estimativa de Politis e White é 10.56. Transcrever o paper
não prova nada; bater com o `b` que de fato minimiza o EQM prova.

## Exceções tipadas

- `InsufficientSampleError` quando `b_hat > n / MIN_BLOCK_SAMPLE_RATIO`. `02` 2.1 manda abortar
  e exigir mais dados. A constante já existe em `core/constants.py` e está sem uso.
- `UnitMismatchError` já é levantada pelo construtor de `EquityPaths` quando `unit` e `period`
  discordam. A função de reamostragem propaga `unit` da entrada e nunca a escolhe.

## Critérios de aceitação, os quatro tipos de `04`

1. **Caso analítico.** Com `b = 1` e semente fixa, a matriz de índices tem distribuição uniforme
   sobre `0..n-1` e o comprimento esperado de bloco realizado é 1. Com `b` genérico, o
   comprimento de bloco realizado tem média `b` dentro do erro de Monte Carlo reportado.
2. **Invariâncias.** Mesma semente produz caminhos idênticos byte a byte. Sementes diferentes
   produzem caminhos diferentes. Escalar todos os retornos por constante positiva escala os
   caminhos sob `FIXED_INITIAL` de forma exata. Reamostrar não altera o conjunto de valores
   possíveis, apenas sua ordem e multiplicidade.
3. **Casos degenerados.** Série constante, série de comprimento mínimo, `b` maior que `n`,
   `b_hat` acima do teto de `MIN_BLOCK_SAMPLE_RATIO`, um único caminho, um único passo.
4. **Recuperação sob dado sintético.** Sob i.i.d. o `b` estimado fica próximo de 1. Sob AR(1)
   com coeficiente declarado, o `b` estimado bate com o argmin do EQM por força bruta. A
   distribuição bootstrap da média cobre a média verdadeira na taxa nominal. O bootstrap em
   blocos preserva a autocorrelação de primeira ordem estritamente melhor do que `b = 1`, com
   margem medida e não adjetivada.

Adicionalmente, e isto vale mais do que os quatro: se algum critério de `02` 2.1 não for
atingível, **aponte o erro na especificação em vez de afrouxar tolerância**. Foi o que produziu
D010, D013 e D017. Tolerância escolhida para o teste passar é proibida por `04`.

## Ao final

Escreva as entradas de decisão no formato de `06` e apresente o texto pronto para colar.
Registre a medição de tempo e memória do caminho de Monte Carlo, porque `04` exige medição
antes e depois de qualquer decisão de performance.
