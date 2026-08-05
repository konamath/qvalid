# Prompt da v0.5: `core/regimes.py`

> Documento de tarefa escrito antes do código, conforme a instrução do projeto.

## Contexto mínimo

`02` seções 2.2 e 4, `04`, `05` v0.5, e D006, D013, D018, D022, D025 de `06`.

Estado: v0.4 fechada. 404 testes, `core` em 100 por cento de cobertura.

## Escopo

Rotulagem causal em grade bidimensional, matriz de transição, reamostragem markoviana e
atribuição de P&L por estado. Mais o contrato `RegimeLabels`, que `01` já descreve mas que
ainda não existe em código.

## Decisões de implementação, com o trade off explícito

### 1. O rótulo do período `t` usa janela terminando em `t - 1`

A alternativa é incluir o próprio período `t`. Não é look ahead no sentido estrito, já que
ambos são conhecidos ao fim de `t`, mas cria correlação mecânica: se a estratégia é comprada,
um período de alta é simultaneamente rotulado como tendência de alta e atribuído lucro. A
atribuição por estado passaria a medir a própria direção da posição.

Escolhido: janela estritamente anterior, de modo que o rótulo é conhecido **antes** de o
período começar. Custo: rótulos mais defasados, e um giro de regime dentro do período é
atribuído ao estado antigo. Em troca, a atribuição responde a pergunta que interessa.

### 2. Aquecimento explícito, com estado indefinido

Quantil expansível não existe nas primeiras observações. Escolhido: aquecimento de
`n_estados_por_eixo * MIN_STATE_OBS`, isto é 60 numa grade 3 por 3, durante o qual o rótulo é
`UNDEFINED` e **não** um balde forçado. Trades nesses períodos ficam fora da atribuição, com a
contagem reportada. Derivação reaproveita `MIN_STATE_OBS`, sem constante nova.

### 3. Teste de igualdade de médias sob variâncias desiguais

O eixo de volatilidade **garante** variâncias desiguais entre estados, por construção, e as
contagens também são desiguais. O ANOVA padrão supõe variâncias iguais. Medido, com médias
verdadeiramente iguais, desvios 1, 3 e 9 e 2000 réplicas:

| cenário                                 | Welch  | `f_oneway` | nominal |
|-----------------------------------------|--------|------------|---------|
| `n` iguais, desvios 1, 3, 9             | 0.0580 | 0.0870     | 0.05    |
| `n` = 40, 100, 300 com desvios 1, 3, 9  | 0.0410 | **0.0005** | 0.05    |

A segunda linha é o argumento inteiro. Com o `n` maior no estado de maior variância, que é
exatamente o arranjo típico aqui, o ANOVA padrão praticamente nunca rejeita. Escolhido: Welch
(1951), implementado no módulo, com a medição na docstring.

### 4. Onde mora a reamostragem markoviana

`02` seção 2.2 aparece sob `core/resample.py` e `05` v0.5 a lista no escopo de
`core/regimes.py`. Escolhido: `regimes.py`, porque ela não compartilha nada com o bootstrap
estacionário além da construção de `EquityPaths`, e porque manter cadeia, matriz de transição e
rótulos no mesmo módulo evita uma dependência circular de conceito. `02` precisa ser corrigido
para mover o cabeçalho.

## Fatos já medidos

**Custo do quantil expansível na forma ingênua**, um `np.quantile` por período:

| T      | 500   | 1000  | 2000  | 4000  |
|--------|-------|-------|-------|-------|
| tempo  | 0.012 | 0.029 | 0.073 | 0.207 |

Aceitável sem otimização. Fica no passo 1 da escada de `04` e a medição entra no registro.

## Critérios de aceitação

1. **Ausência de look ahead, e é o critério que `05` destaca.** Duas formas, ambas exatas e sem
   tolerância:
   - Estabilidade de prefixo: `rotular(serie[:k])` coincide com `rotular(serie)[:k]`.
   - Invariância a perturbação do futuro: alterar `serie[k:]` arbitrariamente não altera nenhum
     rótulo em `[:k]`.
   A segunda é a mais forte e reprova qualquer quantil calculado sobre a amostra inteira.
2. **Invariância a transformação monótona do estimador de deriva.** Classificação por quantil
   depende só da ordem, então aplicar uma função estritamente crescente ao estimador não pode
   mudar rótulo nenhum.
3. **Estratégia sintética que só ganha em alta volatilidade** concentra P&L no estado
   correspondente e o teste de Welch rejeita igualdade de médias.
4. **Recuperação de matriz de transição conhecida** a partir de cadeia sintética, dentro do erro
   amostral, com o erro reportado.
5. **Degenerados.** Estado com menos de `MIN_STATE_OBS` levanta `RegimeSparsityError` a menos
   que o colapso seja autorizado. Série toda em aquecimento. Um único estado ocupado.

Se algum critério de `02` não for atingível, aponte o erro na especificação. Aconteceu quatro
vezes, D010, D013, D017 e D022.
