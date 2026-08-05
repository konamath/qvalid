# Prompt da v0.3: `core/risk.py`

> Documento de tarefa escrito antes do código, conforme a instrução do projeto.

## Contexto mínimo

`01` contratos, `02` seção 5, `04` inteiro, `05` v0.3, e D013, D015, D018, D020 de `06`.

Estado: v0.2 fechada. 315 testes, `core` em 100 por cento de cobertura.
`resample.resample_equity_paths` já devolve `EquityPaths` em **níveis absolutos** de equity,
shape `(n_paths, n_steps + 1)`, coluna zero igual ao capital inicial, com `unit` propagada.

## Escopo

`02` seção 5 inteira: distribuição de drawdown máximo, VaR e ES por percentil empírico com
intervalo por bootstrap sobre os próprios caminhos, risco de ruína como probabilidade de
atingir barreira absorvente em horizonte declarado, tempo até a barreira com distribuição, e
fração de Kelly ajustada por incerteza de parâmetro.

## Correção necessária a D020

D020 afirmou que a base é recuperável dos caminhos por serem níveis absolutos. **Está errado.**
Dados os níveis, o retorno por passo é `diff / L_0` sob `FIXED_INITIAL` e `diff / L_{t-1}` sob
`CURRENT_EQUITY`, e não há como distinguir qual gerou a série. A base tem que entrar como
argumento tipado nas funções que precisam de retorno por passo, e entra no relatório.

## Três formas fechadas, já verificadas numericamente. Use e não re-derive.

### 1. Caminho determinístico decrescente

Trivial e exato: com passo constante `-d` e barreira a distância `a`, a primeira passagem é no
passo `ceil(a / d)`. Serve de teste de sanidade do índice de primeira passagem, sem tolerância.

### 2. Ruína sob passeio aleatório simétrico com barreira única

A forma fechada contínua pelo princípio da reflexão é

    P(min_{t<=T} W_t <= -a) = 2 * Phi(-a / (sigma * sqrt(T)))

e **ela não é o critério de aceitação**, porque a barreira é monitorada em tempo discreto e o
caminho pode cruzar entre dois passos sem ser observado. A correção de continuidade de Broadie,
Glasserman e Kou (1997) desloca a barreira por `beta * sigma * sqrt(dt)` com
`beta = -zeta(1/2) / sqrt(2*pi) = 0.5826`:

    P_discreta = 2 * Phi(-(a + beta * sigma * sqrt(dt)) / (sigma * sqrt(T)))

Medido com 300 mil caminhos, sigma igual a 1 e passo unitário:

| T    | a    | simulado | ingênua | corrigida | \|s-i\|/se | \|s-c\|/se |
|------|------|----------|---------|-----------|-----------|-----------|
| 252  | 20.0 | 0.19478  | 0.20771 | 0.19478   | 17.9      | 0.0       |
| 252  | 10.0 | 0.50403  | 0.52873 | 0.50500   | 27.1      | 1.1       |
| 1000 | 40.0 | 0.19841  | 0.20590 | 0.19937   | 10.3      | 1.3       |
| 60   | 8.0  | 0.26699  | 0.30170 | 0.26786   | 43.0      | 1.1       |

A forma ingênua erra por 10 a 43 erros padrão de Monte Carlo. A corrigida bate dentro de 1.3.
O critério de `02` seção 5, "bate com a solução fechada dentro do erro de Monte Carlo", só é
atingível contra a segunda. `02` precisa declarar qual, e a entrada de decisão registra isso.

### 3. Drawdown máximo esperado

Magdon-Ismail, Atiya, Pratap e Abu-Mostafa (2004) para movimento browniano sem deriva:

    E[MDD] = sqrt(pi / 2) * sigma * sqrt(T) ~ 1.2533 * sigma * sqrt(T)

Sob monitoramento discreto a mesma correção aparece, e **em dobro**, porque um drawdown envolve
tanto o máximo corrente quanto o nível atual, ou seja, duas fronteiras:

    E[MDD_discreto] ~ sqrt(pi/2) * sigma * sqrt(T) - 2 * beta * sigma

Medido, razão entre simulado e a forma corrigida: 0.996 em `T = 60`, 0.998 em 252, 1.0001 em
1000, 1.0003 em 4000. Contra a forma não corrigida as razões são 0.876, 0.940, 0.971 e 0.986,
ou seja, erro de 12 por cento na amostra curta.

## Decisões de implementação

1. **Base entra como argumento.** Ver a correção a D020 acima.
2. **Convenção de sinal.** VaR e ES são reportados como perdas positivas, não como quantis
   negativos de retorno. ES é a medida principal por ser coerente no sentido de Artzner,
   Delbaen, Eber e Heath (1999); VaR entra só por convenção de comunicação, e a docstring diz
   isso.
3. **Intervalo por bootstrap sobre os caminhos.** Os caminhos são independentes entre si por
   construção, logo aqui o bootstrap correto é i.i.d. sobre caminhos, não em blocos. Seed
   obrigatória.
4. **Absorção não é aplicada em silêncio.** Ruína e tempo até barreira usam primeira passagem
   sobre o caminho bruto. Um caminho que fura a barreira e se recupera é impossível na prática,
   então quem quiser estatística terminal sob regra de parada chama `absorb_at_barrier`
   explicitamente, e o relatório registra se houve absorção. Padrão silencioso aqui mudaria VaR
   e ES sem aviso, o que `04` proíbe.
5. **Guarda de unidade.** Risco de ruína em horizonte declarado e tempo até barreira exigem
   `unit` igual a PERIOD e levantam `UnitMismatchError` caso contrário. `02` seção 5 é explícita:
   horizonte em unidade de trade não é horizonte.
6. **Kelly ajustado.** Calcular Kelly em cada caminho reamostrado e reportar a distribuição mais
   um quantil inferior, em vez da forma bayesiana com priori normal. Motivo: usa a incerteza que
   o bootstrap já produziu, sem introduzir uma priori não declarada. A divergência em relação à
   forma bayesiana fica escrita na docstring, conforme `04`.

## Critérios de aceitação

1. **Analítico.** Passo determinístico atinge a barreira no passo exato, sem tolerância. Ruína
   bate com a forma corrigida dentro do erro de Monte Carlo reportado, e difere da ingênua na
   direção prevista. `E[MDD]` bate com a forma corrigida dentro de meio por cento.
2. **Invariâncias.** Determinismo por seed. VaR e ES invariantes a escala conjunta de capital e
   P&L. ES maior ou igual ao VaR no mesmo nível, sempre. Monotonicidade do VaR no nível.
3. **Degenerados.** Caminho constante, barreira acima do capital inicial, barreira nunca
   atingida, um caminho, um passo, todos os caminhos arruinados.
4. **Recuperação.** As três formas fechadas acima, com seed fixa e erro de Monte Carlo reportado.

Se algum critério de `02` não for atingível, aponte o erro na especificação. Já aconteceu três
vezes, D010, D013 e D017, e a quarta está descrita na seção 2 acima.
