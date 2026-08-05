# Prompt da v0.4: `core/overfit.py`

> Documento de tarefa escrito antes do código, conforme a instrução do projeto.
> `05` chama o par de testes desta versão de o mais importante do projeto inteiro: é onde a
> ferramenta demonstra que discrimina ruído de edge.

## Contexto mínimo

`02` seção 3 inteira, `04`, `05` v0.4, e D004, D006, D013, D018, D022 de `06`.

Estado: v0.3 fechada. 354 testes, `core` em 100 por cento de cobertura. Existem
`gridding`, `metrics`, `resample`, `risk`.

## Escopo

`02` seção 3: Deflated Sharpe Ratio, Minimum Track Record Length, PBO por CSCV, e SPA com
Reality Check como comparação. Mais o contrato de entrada da matriz de tentativas, que `05`
lista como pré requisito da implementação.

## Contrato novo: `TrialMatrix`

`02` seção 3 abre com uma pré condição: todo Sharpe desta seção vem de `PeriodReturns`, na
mesma grade e com o mesmo `periods_per_year` para todas as configurações comparadas. Comparar
Sharpes de grades diferentes é erro de unidade.

Decisão: tornar isso **estrutural** em vez de verificado. Um contrato `TrialMatrix` guarda a
matriz `(n_periods, n_configs)` de retornos por período mais **uma única** declaração de
`period`, `periods_per_year`, `calendar_id`, `basis` e `initial_capital`. Assim não existe
estado em que duas configurações da mesma matriz estejam em grades diferentes, e a pré condição
deixa de depender de checagem.

Vai para `contracts.py` e precisa de entrada em `01` e em `06`.

## Fatos já verificados numericamente. Use e não re-derive.

### 1. Máximo esperado de `N` Sharpes sob a nula

    SR* = sqrt(V) * [ (1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N*e)) ]

com `gamma` de Euler-Mascheroni. Medido contra simulação de `N` Sharpes i.i.d. `N(0, V)`:

| N     | 2      | 5      | 10     | 50     | 200    | 1000   |
|-------|--------|--------|--------|--------|--------|--------|
| razão | 1.0641 | 0.9771 | 0.9721 | 0.9872 | 0.9940 | 0.9953 |

É aproximação assintótica em `N`. Erro de 6 por cento em `N = 2` e abaixo de 1 por cento a
partir de `N = 200`. **Isso precisa ficar na docstring**: quem informa cinco tentativas recebe
um número com erro de alguns por cento, e a fonte do erro não é a amostra.

### 2. O denominador do PSR é exatamente `sqrt(T * variância de Mertens)`

    1 + SR^2/2 - g3*SR + (g4-3)/4*SR^2  ==  1 - g3*SR + (g4-1)/4*SR^2

Identidade algébrica, verificada a dez casas em três distribuições. Consequência prática: o
PSR pode ser escrito reusando `metrics.mertens_sharpe_variance`, e um teste exige igualdade
exata entre as duas rotas. Liga `overfit` a `metrics` sem duplicar a fórmula.

### 3. CSCV separa ruído de edge

`T = 1000`, `N = 50`, `S = 16`, logo `C(16,8) = 12870` combinações:

| cenário                       | PBO    | mediana do logit |
|-------------------------------|--------|------------------|
| puro ruído                    | 0.5096 | -0.0392          |
| edge real em uma configuração | 0.0301 | +3.9120          |
| edge em cinco de cinquenta    | 0.0609 |                  |

Meio a meio sob ruído é o resultado correto: a vencedora dentro da amostra é cara ou coroa
fora dela. Esse par de números **é** o critério de pronto da v0.4.

### 4. SPA: tamanho, poder, e a ordenação dos três p valores

Forma correta do recentramento, e eu errei o sinal na primeira tentativa:

    T*_b = max_k max( sqrt(n) * (dbar*_k - dbar_k + mu_k) / omega_k , 0 )

ou seja, subtrai a média amostral e **soma** a média estimada sob a nula. Subtrair `mu_k`
diretamente produz um teste com poder zero. As três escolhas de `mu`:

- `lower`: `min(dbar_k, 0)`
- `consistent`: `dbar_k` se `dbar_k < -A_k`, senão zero, com
  `A_k = omega_k / sqrt(n) * sqrt(2 * log log n)`
- `upper`: zero para todo `k`, que é a nula menos favorável e equivale ao Reality Check

Medido com `n = 500`, `K = 20`, 200 réplicas, 400 réplicas de bootstrap:

| cenário                          | lower | consistent | upper | Reality Check |
|----------------------------------|-------|------------|-------|---------------|
| tamanho sob a nula (nominal 0.05)| 0.110 | 0.090      | 0.090 | 0.050         |
| poder, um modelo com edge        | 0.755 | 0.710      | 0.710 | 0.710         |
| poder com modelos ruins no universo | 0.960 | 0.960   | 0.750 | 0.745         |

A ordenação `p_lower <= p_consistent <= p_upper` valeu em **todas** as réplicas dos três
cenários e vira teste de invariância.

A última linha é a demonstração direta do que `02` 3.4 afirma citando Hansen (2005): o SPA
recupera o poder que o Reality Check perde quando o universo contém estratégias ruins. 0.960
contra 0.745.

Achado a registrar: as variantes estudentizadas rejeitam 9 por cento sob nominal 5 por cento
em `n = 500`. O Reality Check não estudentizado fica em 5,0 por cento. A distorção de tamanho
é finita amostra e precisa estar escrita, não descoberta pelo leitor.

## Decisões de implementação

1. **Índices de bootstrap compartilhados entre modelos.** No SPA, a mesma matriz de índices
   tem que reamostrar todos os `k` simultaneamente. Sortear por modelo destruiria a dependência
   cruzada e o máximo sobre `k` mediria outra coisa. É o erro de implementação mais fácil de
   cometer aqui e o mais difícil de detectar.
2. **Sharpe simples dentro do CSCV.** Blocos concatenados fora de ordem temporal não admitem
   estimador HAC, cuja definição depende de defasagem em tempo. Usar média sobre desvio, com a
   divergência declarada em relação à seção 1.2.
3. **Sem número de tentativas, não roda.** D004 já decidiu. O DSR levanta erro tipado quando
   `n_trials` não é informado, e o relatório declara que a correção para busca não foi aplicada.
   Estimar por heurística seria fabricar o insumo que determina o resultado.

## Critérios de aceitação

Os quatro tipos de `04`, mais o par de `05`:

- Estratégia sintética de puro ruído reprovada: PBO próximo de 0,5, DSR baixo, SPA sem rejeitar.
- Estratégia sintética com edge declarado aprovada: PBO próximo de zero, DSR alto, SPA rejeita.
- Identidade exata entre o denominador do PSR e a variância de Mertens de `metrics`.
- Ordenação dos três p valores do SPA em toda réplica.
- SPA com mais poder que Reality Check na presença de modelos ruins.
- Todas as configurações comparadas na mesma grade, garantido por construção do contrato.

Se algum critério de `02` não for atingível, aponte o erro na especificação. Aconteceu quatro
vezes, D010, D013, D017 e D022.
