# 02. Especificação matemática

Documento normativo. Se a implementação divergir daqui, ou o código está errado ou este
arquivo precisa ser atualizado por decisão registrada em `06_registro_de_decisoes.md`.

Formato de cada método: definição, entrada, saída, hipóteses, condições de invalidez,
critério de aceitação do teste automatizado, referência.

## Regras gerais de simulação

- Toda função estocástica recebe `seed: int` obrigatório e usa `numpy.random.default_rng`.
- Número de réplicas é parâmetro explícito, nunca constante escondida. Padrão sugerido: 10000.
- Toda estimativa de Monte Carlo vem acompanhada do próprio erro padrão. Reportar quantil sem
  reportar a incerteza do quantil é erro.
- Testes múltiplos exigem correção declarada. Sem isso, o conjunto de p valores é inútil.

## Constantes declaradas

Vivem em `core/constants.py`, com a derivação na docstring. Nenhuma delas pode aparecer como
literal no meio do código.

| Constante                    | Valor  | Derivação                                                       |
|------------------------------|--------|-----------------------------------------------------------------|
| `MIN_TRADES`                 | 30     | Limiar convencional de inferência assintótica. Ver seção 1.4     |
| `MIN_ACTIVE_FRACTION`        | 0.15   | Ver seção 1.4, curtose induzida por esparsidade                  |
| `MIN_PERIODS`                | 60     | Ver seção 1.4, razão amostra sobre largura de banda HAC          |
| `MAX_HOLDING_TO_PERIOD`      | 1.0    | Duração mediana de posse não pode exceder o período da grade     |
| `PNL_RTOL`                   | 1e-6   | Precisão de ponto flutuante e conversão de moeda                 |
| `MIN_BLOCK_SAMPLE_RATIO`     | 10.0   | Ver seção 2.1                                                    |
| `MIN_STATE_OBS`              | 20     | Ver seção 2.2                                                    |

`PNL_ATOL` não é constante: depende de tick mínimo, multiplicador e quantidade. Ver `01`.

---

## 1. Métricas descritivas

`core/gridding.py` e `core/metrics.py`

### 1.1 Grade temporal e separação de séries

Existem duas séries de retorno com papéis disjuntos, e a distinção é estrutural, não
convencional.

**`TradeReturns`.** Indexada pelo número do trade. Produz expectância, taxa de acerto, profit
factor, razão ganho médio sobre perda média, e distribuição de P&L por trade. Nenhuma dessas
tem unidade temporal.

**`PeriodReturns`.** Indexada por período de calendário. É a única origem de retorno
acumulado, CAGR, volatilidade anualizada, Sharpe, Sortino, drawdown máximo, tempo submerso e
fração de Kelly.

**Proibição.** Nenhuma estatística calculada sobre `TradeReturns` é anualizada em ponto
nenhum do código. O motivo é que o fator de anualização de Lo (2002),

    eta(q) = q / sqrt( q + 2 * soma_{k=1}^{q-1} (q - k) * rho_k )

tem `k` medido em defasagem de tempo calendário e `q` igual ao número de períodos por ano.
Sobre índice de trade, `rho_k` mede dependência na ordem de execução e `q` não existe: trades
por ano é realização amostral da taxa de chegada de sinais, não parâmetro da estratégia.
Aplicar `eta(q)` sobre índice de trade estima um objeto que não é o que a fórmula define.

**Atribuição de P&L.** No período que contém `exit_ts`. A alternativa, distribuir o P&L ao
longo da posse por marcação a mercado, exigiria série de preço dentro de uma métrica
descritiva e quebraria a garantia de suíte offline de D003.

**Escolha da grade.** Escada ordenada: DAILY, WEEKLY, MONTHLY. O motor escolhe a grade **mais
fina** que satisfaz simultaneamente:

1. `active_fraction >= MIN_ACTIVE_FRACTION`
2. `n_periods >= MIN_PERIODS`
3. duração mediana de posse menor ou igual ao comprimento do período

Mais fina, entre as viáveis, porque o erro padrão de Sharpe escala com o inverso da raiz de T
e a grade mais fina maximiza T. Conjunto viável vazio levanta `GridSparsityError`. O motor
nunca escolhe fora da escada e nunca infere o calendário. O usuário pode forçar a grade, e
nesse caso as três condições viram aviso no relatório em vez de erro.

`period`, `periods_per_year`, `calendar_id` e `active_fraction` entram no `ValidationReport`.

### 1.2 Sharpe: estimativa pontual

Sobre `PeriodReturns` de retorno excedente `r_t - r_f`, com `r_f` parâmetro obrigatório de
padrão zero, sempre impresso no relatório.

Variância de longo prazo por Newey e West (1987) com núcleo de Bartlett:

    sigma_LR^2 = gamma_0 + 2 * soma_{k=1}^{L} (1 - k/(L+1)) * gamma_k

Largura de banda `L` por seleção automática de Newey e West (1994). O núcleo de Bartlett
garante estimativa positiva semidefinida por construção.

    SR_anual = sqrt(q) * media_amostral / sigma_LR

**Divergência declarada em relação a Lo (2002).** Lo estima `eta(q)` pela soma finita de
autocorrelações amostrais até a defasagem `q - 1`. Com `q = 252` e três anos de dado diário
isso são 251 autocorrelações estimadas a partir de 756 observações, e o estimador é
inutilizável. Como `Var(soma de q retornos)` converge para `q * sigma_LR^2`, vale
`eta(q) -> sqrt(q) * sigma / sigma_LR`, ou seja, o mesmo objeto populacional por estimador
estável. A divergência fica documentada na docstring da função, conforme `04`.

O relatório traz o Sharpe pelas duas escalas, `sqrt(q)` e HAC, mais o `L` usado. Divergência
grande entre as duas é diagnóstico de autocorrelação relevante, não erro.

### 1.3 Sharpe: erro padrão e intervalo

Método delta sobre o vetor de momentos `theta = (mu, s)` com `s = E[r^2]`, e
`SR = mu / sqrt(s - mu^2)`. As derivadas parciais são

    dg/dmu = s / (s - mu^2)^(3/2)
    dg/ds  = -mu / (2 * (s - mu^2)^(3/2))

e a variância do estimador é

    Var(SR) = (1/T) * grad_g' * Omega * grad_g

com `Omega` a matriz de covariância de longo prazo de `(r_t, r_t^2)`, estimada pelo mesmo
HAC da seção 1.2. Sob independência isso se reduz exatamente à forma de Mertens (2002),

    Var(SR) = (1/T) * (1 + SR^2/2 - gamma_3 * SR + (gamma_4 - 3)/4 * SR^2)

que é a expressão de Christie (2005) e Opdyke (2007). Implementar a forma geral e usar a
forma i.i.d. apenas como teste de consistência.

**Um Sharpe sem intervalo de confiança não entra no relatório.**

### 1.4 Condições de invalidez

- Menos de `MIN_TRADES` trades. Reportar métricas com aviso e suprimir as seções 3 e 4.
- Menos de `MIN_PERIODS` períodos. Derivação: o **parâmetro de seleção de defasagem** de Newey
  e West (1994) para o núcleo de Bartlett escala como `n = 4 * (T/100)^(2/9)`. Exigindo pelo
  menos 15 observações por defasagem retida, `T = 60` dá `n = 3` e razão 20. Abaixo disso o
  estimador HAC é ruído. Correção de nomenclatura: `n` não é a largura de banda. É o número de
  autocovariâncias que alimentam a estimativa plug in da largura de banda ótima, que sai de
  `L = floor(1.1447 * (alpha * T)^(1/3))` e costuma ser várias vezes maior. A derivação
  continua válida, porque a condição de observações por defasagem se aplica a `n`. Nem `MIN_PERIODS`
  nem esta condição são erro: são aviso, e a métrica continua sendo reportada, conforme o
  primeiro item desta lista. Ver D015.
- `active_fraction` abaixo de `MIN_ACTIVE_FRACTION`. Derivação: para uma série que assume um
  único valor não nulo com frequência `p`, a curtose é exatamente

      kappa(p) = (1 - 3p + 3p^2) / (p * (1 - p))

  Ela é fabricada pela esparsidade, não pela distribuição de retorno. A raiz de
  `9p^2 - 9p + 1 = 0` em `p = 0.1273` corresponde a curtose 6, ou seja, excesso 3, da mesma
  ordem do excesso de curtose de retornos diários de índice. Em `p = 0.1464` o excesso cai
  para 2. O limiar de 0.15 fica logo acima, com excesso induzido de 1.84, garantindo que o
  quarto momento que entra na seção 1.3 não seja dominado pelo artefato.
- Duração mediana de posse maior que o período da grade. O agrupamento na saída distorce a
  autocorrelação estimada e contamina o fator de anualização.
- Não estacionariedade dentro da amostra. Tanto o erro padrão quanto o fator de escala
  pressupõem estacionariedade e ergodicidade. Quebra estrutural invalida os dois.

### 1.5 O que o Sharpe corrigido mede e o que não mede

**Mede.** Razão entre média e desvio do retorno excedente por unidade de tempo calendário, com
intervalo que reflete erro amostral sob estacionariedade e sem supor normalidade.

**Não mede.** Probabilidade de existir edge após busca, que é a seção 3.1. Risco de cauda,
porque a variância é simétrica e pune ganho igual a perda. Profundidade e duração de
drawdown. Não corrige seleção entre múltiplas configurações.

### 1.6 Aceitação

- Série constante tem volatilidade zero e Sharpe indefinido, não infinito.
- Série simétrica tem assimetria nula dentro de tolerância.
- Invariância de escala: multiplicar todo P&L por constante positiva não altera Sharpe.
- **Diluição por períodos vazios, forma fechada.** Com `p` a fração ativa, `mu_a` e `sigma_a`
  a média e o desvio dos períodos ativos, e `s = mu_a / sigma_a` o Sharpe por período
  calculado apenas sobre os ativos, valem exatamente

      media_grade = p * mu_a
      var_grade   = p * sigma_a^2 + p * (1 - p) * mu_a^2

  e portanto, **comparando por período**, que é a convenção do critério de aceitação e a que
  o código usa,

      SR_grade / SR_ativos = sqrt(p) / sqrt(1 + (1 - p) * s^2)

  **Comparando Sharpes anualizados**, com a série de ativos anualizada pela própria taxa de
  chegada `p * q` em vez de `q`, o fator `sqrt(p)` cancela e sobra

      SR_grade / SR_ativos = 1 / sqrt(1 + (1 - p) * s^2)

  As duas formas são corretas para comparações diferentes. A primeira é a que o motor calcula;
  a segunda é a que o praticante faz mentalmente ao ler um Sharpe anualizado de série ativa.
  Implementadas como `dilution_ratio_per_period` e `dilution_ratio_annualised`, com teste que
  garante que diferem exatamente por `sqrt(p)`. Ver D010.

  Verificar em dado sintético com `p` e `s` conhecidos. Com os períodos inativos exatamente
  nulos a identidade da variância é exata para a amostra sob variância populacional, logo o
  teste compara com tolerância de ponto flutuante, não com erro amostral. A razão é sempre
  menor ou igual a 1, e a grade completa é a estimativa correta, porque capital parado é
  capital alocado.
- **Caso degenerado de trade único.** Com um único período não nulo em `T` períodos e desvio
  amostral de denominador `T - 1`, vale exatamente `SR_período = 1/sqrt(T)`, logo
  `SR_anual = 1/sqrt(Y)` sob escala `sqrt(q)`, com `Y` o número de anos, para qualquer
  magnitude de P&L. Um trade que decuplicou o capital em cinco anos e um que ganhou um centavo
  produzem o mesmo 0.447. Esse é o teste que justifica `MIN_ACTIVE_FRACTION`.
- **Convenção de graus de liberdade.** Os dois casos fechados acima exigem convenções
  diferentes e isso precisa estar dito, não subentendido. A identidade de diluição é exata
  apenas sob variância populacional, denominador `T`. O caso degenerado de trade único dá
  exatamente `1/sqrt(T)` apenas sob variância amostral, denominador `T - 1`; sob denominador
  `T` ele dá `1/sqrt(T - 1)`. O motor reporta a estimativa pontual sob `T - 1`, que é a
  convenção do praticante, e usa `T` internamente no método delta, cujas derivadas são
  tomadas em relação a `E[r]` e `E[r^2]`. As duas diferem pelo fator `sqrt(T/(T-1))`, que é
  0,85 por cento em `MIN_PERIODS`. Ambas entram no relatório. Ver D014.
- Sob AR(1) com coeficiente conhecido, o fator por variância de longo prazo difere de `sqrt(q)`
  na direção prevista pelo sinal do coeficiente, e recupera `eta(q)` **com viés limitado e
  decrescente em `T`**, não dentro do erro amostral. O estimador de Bartlett é viesado para
  baixo na variância de longo prazo em amostra finita, logo a razão `sigma/sigma_LR` é viesada
  na direção de 1. Medido com coeficiente 0,4: 6,7 por cento em `T = 500`, 3,3 por cento em
  `T = 2000` e 1,9 por cento em `T = 8000`, decaindo a aproximadamente `T^(-1/3)`, que é a
  taxa que a teoria desse núcleo prevê. Em `T = 2000` esse viés equivale a sete erros padrão
  de Monte Carlo, então o critério anterior era inatingível e teria sido cumprido apenas por
  tolerância frouxa, que `04` proíbe. O critério é: direção correta, viés abaixo de 10 por
  cento, e monotonicamente decrescente numa escada de `T`. Ver D013.
- Sob i.i.d. simulado, a forma geral da seção 1.3 **é igual** à forma de Mertens, não converge
  para ela. Com momentos populacionais e largura de banda zero as duas expressões produzem o
  mesmo número para qualquer amostra finita, logo o teste de consistência é exato a ponto
  flutuante em vez de estatístico.
- **Dispersão nula é um teste numérico, não uma igualdade a zero.** Uma série constante de
  valor não representável em binário, como `0.001` repetido, tem variância calculada da ordem
  de `1e-38` em vez de zero, e o Sharpe resultante é `7e16`. Isso é a infinidade que o
  primeiro item desta seção proíbe, disfarçada de número finito. A regra usada é: a dispersão
  não se distingue de zero quando o desvio cai abaixo de `T * eps * max|x|`, que é a cota de
  erro do próprio cálculo da variância. Não introduz parâmetro livre.

---

## 2. Reamostragem

`core/resample.py`

Entrada: `TradeReturns` ou `PeriodReturns`. A `unit` da entrada propaga para `EquityPaths` e
determina o que pode consumir os caminhos. Ver `01`.

### 2.1 Bootstrap estacionário

Reamostragem em blocos de comprimento aleatório geométrico, que preserva dependência de curto
prazo e mantém a série reamostrada estacionária.

- Comprimento esperado de bloco estimado automaticamente (Politis e White, 2004; correção em
  Patton, Politis e White, 2009), com possibilidade de sobrescrever manualmente.
- Referência do método: Politis e Romano (1994).

**Hipóteses.** Estacionariedade fraca e dependência de curto alcance. Se a série tiver quebra
estrutural, o bootstrap mistura regimes distintos e subestima a cauda. Esse é justamente o
motivo de existir a seção 4.

**Invalidez.** Comprimento de bloco estimado maior do que a amostra dividida por
`MIN_BLOCK_SAMPLE_RATIO`: abortar e exigir mais dados.

**Aceitação.** Sob série i.i.d. simulada, o comprimento de bloco estimado converge para
próximo de 1 e a distribuição bootstrap da média cobre a média verdadeira na taxa nominal.
Sob AR(1) com coeficiente conhecido, o bootstrap em blocos preserva a autocorrelação de
primeira ordem melhor do que o bootstrap i.i.d., com margem verificável. Medido com
`rho = 0,6` e autocorrelação observada de 0,642: blocos devolvem 0,591, i.i.d. devolve 0,003.

**Aceitação adicional, e é a que vale mais.** O comprimento de bloco estimado deve bater com o
que de fato minimiza o erro quadrático médio do estimador que a regra existe para otimizar,
apurado por força bruta sobre o estimador exato de variância do bootstrap estacionário. Sob
AR(1) com `rho = 0,5` e `n = 1000`, o argmin por força bruta é 10 e a regra plug in devolve
10,56. Conferir a transcrição da fórmula contra a própria fórmula não é verificação. Ver D018.

**Nota aritmética sobre a guarda.** Como o menor comprimento de bloco possível é 1 e a guarda
recusa `b > n / MIN_BLOCK_SAMPLE_RATIO`, toda amostra com menos de 10 observações é recusada
por construção, independentemente da estrutura de dependência. Ver D021.

### 2.2 Reamostragem por cadeia de Markov entre regimes

> **Localização.** Esta subseção descreve método implementado em `core/regimes.py`, não em
> `core/resample.py`. A cadeia não compartilha nada com o bootstrap estacionário além da
> construção de `EquityPaths`, e depende de `RegimeLabels`. Ver D028.

Estimar matriz de transição empírica entre estados de regime e reamostrar retornos
condicionalmente ao estado, preservando o agrupamento temporal de condições boas e ruins.

**Hipóteses.** Markov de primeira ordem sobre os estados. Retornos condicionalmente
permutáveis dentro de cada estado.

**Invalidez.** Qualquer estado com menos de `MIN_STATE_OBS` observações. Nesse caso, colapsar
a grade de regimes antes de simular, levantando `RegimeSparsityError` se o colapso não for
autorizado.

**Aceitação.** Recuperar matriz de transição conhecida a partir de dado sintético gerado por
cadeia conhecida, dentro do erro amostral.

---

## 3. Testes de sobreajuste

`core/overfit.py`

Esta é a seção que separa o projeto de uma planilha de métricas.

**Pré condição comum.** Todo Sharpe que entra nesta seção é o Sharpe **por período**, não
anualizado, sobre `PeriodReturns`, na mesma grade e com o mesmo `periods_per_year` para todas as
configurações comparadas. Comparar Sharpes calculados em grades diferentes é erro de unidade.

A palavra "por período" não é redundante. A seção 1.2 produz quatro Sharpes distintos da mesma
série: por período amostral, por período populacional, anualizado por raiz de q, e anualizado por
HAC. Os dois anualizados são os que o relatório destaca. Alimentar o DSR com um anualizado
enquanto a variância entre tentativas vem dos por período erra o resultado por um fator de raiz de
q, que em grade diária é cerca de dezesseis, e o número resultante continua parecendo plausível.
O código sempre esteve certo e mais preciso que este texto, ver a docstring de
`deflated_sharpe_ratio`; a lacuna era da especificação.

### 3.1 Deflated Sharpe Ratio

Ajusta o Sharpe observado pelo número de tentativas independentes, pela assimetria e pela
curtose da distribuição de retornos, devolvendo a probabilidade de o Sharpe verdadeiro ser
positivo. Referência: Bailey e López de Prado (2014).

Entrada obrigatória: número de configurações testadas e a variância entre os Sharpes dessas
configurações. Se o usuário não informar quantas tentativas fez, o teste não roda. Estimar
esse número por conta própria seria inventar dado. Ver D004.

### 3.2 Minimum Track Record Length

Comprimento mínimo de amostra para que o Sharpe observado seja estatisticamente distinguível
de um limiar, dados os momentos amostrais. Mesma referência. O resultado sai em número de
períodos da grade vigente e é convertido para tempo calendário no relatório.

### 3.3 Probability of Backtest Overfitting via CSCV

Validação cruzada combinatória simétrica: particionar o histórico, avaliar o ranqueamento das
configurações dentro e fora da amostra, e estimar a probabilidade de a melhor configuração na
amostra ficar abaixo da mediana fora dela. Referência: Bailey, Borwein, López de Prado e Zhu
(2017).

**Requisito.** Exige a matriz de performance de todas as configurações testadas, não apenas a
vencedora. O formato é o contrato `TrialMatrix` de `01`, que declara a grade uma vez para a
matriz inteira e torna a pré condição desta seção estrutural. Ver D024.

**Medido, `T = 1000`, `N = 50`, `S = 16`, média sobre quatro sementes.** Sob ruído puro o PBO
fica em 0,475, com faixa de 0,26 a 0,58 mesmo com 12870 combinações, logo uma execução única
não caracteriza o estimador. Com Sharpe por período de 0,25 o PBO vai a zero. Meio a meio sob
ruído é a resposta correta, não falha do método: a vencedora dentro da amostra é cara ou coroa
fora dela.

**O logit é limitado por `log(N)`**, porque o melhor posto relativo é `N/(N+1)`. Com cinquenta
configurações o teto é 3,912. A magnitude do logit mediano portanto **não** é comparável entre
universos de tamanhos diferentes, só o sinal e a distância relativa ao teto. Ver D025.

### 3.4 Reality Check e SPA

Teste de superioridade da melhor estratégia sobre um benchmark, corrigindo para a busca sobre
o universo de estratégias. Referências: White (2000) para o Reality Check e Hansen (2005) para
o SPA, que corrige a perda de poder do primeiro na presença de estratégias ruins no universo.

Implementar o SPA como padrão e o Reality Check como comparação.

**Forma do recentramento, e é onde o erro mora.** A estatística de bootstrap é

    T*_b = max_k max( sqrt(n) * (dbar*_k - dbar_k + mu_k) / omega_k , 0 )

ou seja, subtrai a média amostral e **soma** a média estimada sob a nula. Subtrair `mu_k`
diretamente, que é a leitura natural, produz teste com poder zero e ainda assim aparenta
funcionar. Ver D025.

**Índices de bootstrap compartilhados.** A mesma matriz de índices tem que reamostrar todos os
`k` simultaneamente. Sortear por modelo destrói a dependência cruzada, e o máximo sobre `k` é
precisamente onde essa dependência importa.

**Medido, `n = 500`, `K = 20`, 200 réplicas.** Os três p valores de Hansen satisfazem
`lower <= consistent <= upper` em todas as réplicas. Tamanho sob a nula, contra nominal 0,05:
0,110, 0,090, 0,090, e 0,050 para o Reality Check não estudentizado. Poder com um modelo com
edge: 0,755, 0,710, 0,710, 0,710. **Poder com modelos ruins no universo: 0,960, 0,960, 0,750 e
0,745.** A última linha é a demonstração direta do que Hansen (2005) afirma, e é a razão de o
SPA ser o padrão.

Limitação declarada: as variantes estudentizadas rejeitam 9 por cento sob nominal 5 em
`n = 500`. É efeito de amostra finita e encolhe com a amostra.

---

## 4. Regimes

`core/regimes.py`

Grade bidimensional sobre uma série de mercado de referência:

- Eixo de tendência: classificação em quantis de um estimador causal de deriva, calculado com
  janela estritamente passada.
- Eixo de volatilidade: quantis de volatilidade realizada na mesma janela.

Padrão sugerido: 3 por 3, gerando 9 estados. Reduzir para 2 por 2 se a amostra for pequena.

**Regra dura.** Os cortes de quantil devem ser estimados de forma expansível, com apenas dado
passado, ou o rótulo contamina o resultado com informação futura. Quantil calculado sobre a
amostra inteira é look ahead e invalida toda a análise subsequente.

**Regra de causalidade, forma operacional.** O rótulo do período `t` usa janela terminando em
`t - 1`, logo é conhecido antes de o período começar. Incluir o próprio período não seria look
ahead estrito, mas criaria correlação mecânica: numa estratégia comprada, um período de alta
seria rotulado como tendência de alta e creditado com lucro ao mesmo tempo, e a atribuição
mediria a direção da posição. Ver D026.

**Aquecimento.** `estados_por_eixo * MIN_STATE_OBS`, isto é 60 numa grade 3 por 3. Durante ele o
rótulo é indefinido e **não** um balde forçado. Períodos indefinidos saem da atribuição com
contagem e P&L reportados, de modo que a soma fecha.

**Saída.** Atribuição de P&L por estado, com contagem de trades e teste de igualdade de média
entre estados. A pergunta que interessa: o resultado vem de todos os estados ou de um só.

**O teste de igualdade de médias é o de Welch (1951), não o ANOVA padrão.** O eixo de
volatilidade garante variâncias desiguais entre estados por construção, e as contagens também
diferem. Medido, com médias verdadeiramente iguais e desvios 1, 3 e 9 sobre 2000 réplicas: com
contagens iguais, Welch rejeita 0,0580 e o ANOVA padrão 0,0870, contra nominal 0,05; com
contagens 40, 100 e 300, Welch rejeita 0,0410 e o ANOVA padrão **0,0005**. Com o `n` maior no
estado de maior variância, que é o arranjo típico aqui, o teste de variância igual praticamente
nunca rejeita. Ver D027.

**Aceitação.** Dado sintético em que a estratégia só ganha em alta volatilidade deve produzir
concentração de P&L no estado correspondente e rejeição da igualdade de médias. Verificação
automatizada de ausência de look ahead: deslocar a série de referência para frente no tempo
não pode alterar nenhum rótulo já emitido.

---

## 5. Risco

`core/risk.py`

Sobre os `EquityPaths` simulados:

**Limitação medida da composição com a seção 2, ver D051.** A distribuição de drawdown vem de
caminhos reamostrados, e o bootstrap estacionário quebra a dependência em cada emenda de bloco.
Sob independência a distribuição simulada é exata, razão medida de 1,0012 ± 0,0037. Com
dependência ela fica **pequena demais**, e de forma monótona: 0,954 em rho 0,20, 0,940 em 0,40,
0,926 em 0,60, com o percentil 95 um pouco pior que a mediana em todos os casos.

A direção importa. O percentil 95 é o número que alguém usa para dimensionar capital e ele sai
otimista, e o drawdown observado é colocado num quantil mais alto do que merece. Por isso o
relatório emite aviso na própria seção sempre que o comprimento de bloco estimado passa de 2, em
vez de deixar a ressalva só neste documento.

- VaR e Expected Shortfall por percentil empírico, com intervalo de confiança por bootstrap
  sobre os próprios caminhos.
- Distribuição do drawdown máximo, não apenas o valor pontual do backtest. O drawdown máximo
  observado é uma realização, quase sempre otimista.
- Risco de ruína como probabilidade de atingir barreira absorvente, definida pelo capital
  mínimo operacional, em horizonte declarado.
- Tempo até a barreira, com distribuição.
- Fração de Kelly e fração de Kelly ajustada por incerteza de parâmetro.

**Pré condição.** Risco de ruína em horizonte declarado e tempo até barreira exigem
`EquityPaths` com `unit` igual a PERIOD. Horizonte em unidade de trade não é horizonte.

**Nota.** ES é preferível a VaR como medida principal por ser coerente no sentido de Artzner,
Delbaen, Eber e Heath (1999). VaR entra apenas por convenção de comunicação.

**Nota obrigatória sobre monitoramento discreto.** A barreira é verificada uma vez por período,
e um caminho pode furá la e voltar entre dois passos sem ser observado. Toda estatística de
barreira deste módulo fica portanto **abaixo** da resposta em tempo contínuo, por uma
quantidade conhecida. A correção de continuidade de Broadie, Glasserman e Kou (1997) desloca a
barreira por `beta * sigma * sqrt(dt)` com `beta = -zeta(1/2)/sqrt(2*pi) = 0,5826`. Isso não é
defeito do estimador: é modelo fiel de uma conta marcada uma vez por dia.

**Aceitação.** Caminho determinístico decrescente atinge a barreira em passo exato conhecido,
sem tolerância nenhuma.

Sob passeio aleatório simétrico com barreira única, a probabilidade de ruína simulada bate com
a solução fechada **corrigida por continuidade** dentro do erro de Monte Carlo, e o erro de
Monte Carlo é reportado. Contra a forma não corrigida ela não bate, e a especificação anterior,
que dizia apenas "a solução fechada", era inatingível. Medido com 300 mil caminhos:

| T    | a    | simulado | ingênua | corrigida | \|s-i\|/se | \|s-c\|/se |
|------|------|----------|---------|-----------|-----------|-----------|
| 252  | 20,0 | 0,19478  | 0,20771 | 0,19478   | 17,9      | 0,0       |
| 252  | 10,0 | 0,50403  | 0,52873 | 0,50500   | 27,1      | 1,1       |
| 1000 | 40,0 | 0,19841  | 0,20590 | 0,19937   | 10,3      | 1,3       |
| 60   | 8,0  | 0,26699  | 0,30170 | 0,26786   | 43,0      | 1,1       |

Ver D022.

**Aceitação adicional, drawdown máximo esperado.** Magdon-Ismail, Atiya, Pratap e Abu-Mostafa
(2004) dão `E[MDD] = sqrt(pi/2) * sigma * sqrt(T)` para movimento browniano sem deriva. Sob
monitoramento discreto a mesma correção entra **em dobro**, porque drawdown é a distância entre
o máximo corrente e o nível atual, duas fronteiras monitoradas:

    E[MDD_discreto] = sqrt(pi/2) * sigma * sqrt(T) - 2 * beta * sigma

Razões entre simulado e essa forma: 0,996 em `T = 60`, 0,998 em 252, 1,0001 em 1000 e 1,0003 em
4000. Contra a forma não corrigida: 0,876, 0,940, 0,971 e 0,986, ou seja, 12 por cento de erro
em amostra de sessenta períodos. O fator dois não está no paper original e foi obtido por
medição aqui.

**Absorção na barreira não é conservadora.** Congelar o caminho na barreira modela parada
executada exatamente nela. Isso baixa o valor terminal de todo caminho que furou e se
recuperou, que é o efeito esperado, mas **sobe** o valor terminal de todo caminho que terminou
abaixo da barreira, porque tal conta teria sido encerrada ali. A cauda profunda fica melhor
depois da absorção, logo ES absorvido não deve ser lido como o número conservador. Some se a
isso a hipótese de execução sem gap e sem derrapagem. A absorção nunca é aplicada por padrão e
fica registrada no identificador do método.

---

## 6. Simulador de mesa proprietária

`core/propfirm.py`

Modelo de barreiras aplicado a caminhos reamostrados de P&L, obrigatoriamente com `unit`
igual a PERIOD e `period` igual a DAILY, porque as regras são diárias:

- Alvo de lucro.
- Limite de perda máxima, com variante estática e variante móvel calculada sobre o pico.
- Limite de perda diária.
- Número mínimo de dias operados.
- Fase de avaliação e fase financiada, com divisão de lucro e ciclo de saque.

**Saídas.** Probabilidade de aprovação, probabilidade de saque, valor esperado líquido
descontando o custo da avaliação, distribuição de dias até o primeiro saque, percentis 5 e 95.

**Requisito de projeto.** As regras de cada mesa vão para arquivo YAML versionado, nunca para
o código. Elas mudam com frequência e a lógica de barreira é a mesma para todas.

**Ordem de checagem dentro do dia, e ela é o modelo.** Limite diário, depois perda máxima,
depois alvo, depois limite de calendário. Consequências intencionais: dia que fura os dois
limites conta como furo diário, que é o que a mesa diria; e dia que atinge o alvo enquanto fura
um limite é falha, não aprovação. Mesas divergem nisso, então a escolha fica declarada. Ver D036.

**O motivo da morte é guardado por caminho.** Uma mesa cujas falhas são todas limite diário é um
problema diferente de uma cujas falhas são todas drawdown móvel, e a probabilidade de aprovação
sozinha esconde qual. Medido sobre 4000 caminhos idênticos: mesa estática reprova 2830 por limite
diário e 283 por perda máxima; mesa móvel, 2644 e 588.

**Capital da estratégia não entra.** Só as diferenças diárias dos caminhos são lidas, aplicadas a
uma conta que começa no tamanho da mesa. A pergunta passa a ser "esta estratégia, neste tamanho,
numa conta daquele tamanho". Ver D037.

**Condicionamento.** Dias até aprovar e até o primeiro saque são percentis condicionais ao evento,
vazios quando ele nunca ocorre. O valor esperado líquido é média sobre **todos** os caminhos,
inclusive os que pagaram a taxa e falharam, porque a pergunta é se vale a pena tentar e não
quanto ganha quem passa. Ver D038.

**Aceitação.** Caso degenerado com P&L determinístico positivo por dia deve produzir
probabilidade de aprovação igual a 1 e número de dias exato até o alvo. Verificado sem
tolerância, e estendido a cada barreira: o dia exato do furo diário, o nível exato da perda
máxima, o atraso exato imposto pelo mínimo de dias operados, e o dia exato do primeiro saque.

---

## 7. Veredito

`core/verdict.py`

Agregação final. Duas saídas paralelas:

1. Painel de evidência: cada teste com seu resultado, incerteza e condição de validade. Sem
   colapsar em nota única.
2. Ordenamento por equivalente certeza sob Teoria do Prospecto Cumulativa, reaproveitando o
   núcleo já implementado no projeto Atlas. A vantagem sobre uma nota arbitrária de A a F é
   que o ranqueamento passa a ter interpretação: qual estratégia um agente com essa função de
   utilidade e essa ponderação de probabilidade prefere.

**Regra.** O veredito nunca é apresentado como número único sem o painel. Colapsar evidência
heterogênea em uma letra é exatamente o defeito que este projeto existe para corrigir.

**Regra adicional.** Nenhum teste suprimido por condição de invalidez entra no ordenamento por
equivalente certeza como se tivesse sido aprovado. Ausência de evidência aparece como ausência
no painel, nunca como aprovação silenciosa.

**Forma estrutural da regra.** `rank` devolve duas listas, ordenados e não ordenáveis, e para
candidato sem seção obrigatória o equivalente certeza **não é calculado**. Calcular e esconder
deixaria o número disponível para quem olhasse. `Verdict` recusa o estado inconsistente: o
número existe exatamente quando o candidato é ordenável. As duas listas nunca se misturam. Ver
D039.

**A lista de requisitos é declarável, não fixa.** O padrão exige `resampling` e
`deflated_sharpe`. Encurtá la é declaração e entra no relatório e no hash da configuração.
Comparar duas estratégias que ambas carecem da correção é legítimo desde que se diga; comparar
uma com e uma sem é o que a regra impede. Consequência de D004 que só apareceu na integração:
com a lista estrita, o pipeline nunca produz veredito, porque um log de trades sozinho não
fornece a matriz de tentativas.

**Especificação do equivalente certeza.** Função de valor de Tversky e Kahneman (1992),
`v(x) = x^alpha` para ganho e `-lambda * (-x)^beta` para perda, com ponderação cumulativa
`w(p) = p^c / (p^c + (1-p)^c)^(1/c)`, perdas acumuladas de baixo para cima e ganhos de cima para
baixo. Ponto de referência é parâmetro, com padrão zero, isto é "comparado com não operar".

Verificações exatas exigidas: com `alpha = beta = lambda = gamma = delta = 1` o modelo reduz
**exatamente** à média aritmética; resultado certo tem a si mesmo como equivalente certeza;
aposta simétrica tem equivalente certeza negativo sob aversão a perda; dominância estocástica de
primeira ordem é respeitada.

Os pesos de decisão **não** somam 1 sob ponderação subaditiva, e a diferença é o efeito certeza,
não erro. A probabilidade cumulativa precisa ser truncada em `[0, 1]`: somar pesos iguais
ultrapassa 1 por épsilons, `1 - p` fica negativo, e base negativa em potência fracionária é
`nan`. Ver D040.
