# 04. Convenções de código

## Stack

- Python 3.12 ou superior.
- `uv` para ambiente e dependências.
- `ruff` para lint e formatação.
- `mypy` em modo estrito nos módulos de `core`. Nos adaptadores, modo padrão.
- `pytest` mais `hypothesis` para testes de propriedade.
- `numpy`, `pandas`, `scipy`, `statsmodels`, `pyarrow`, `duckdb`, `pydantic`, `typer`.

## Idioma

Código, nomes, docstrings e mensagens de erro em inglês. Documentação de projeto, registro de
decisões e relatório em português. Motivo: o repositório é peça de portfólio internacional, e
identificador misturando idioma é ruído.

## Docstrings

Padrão NumPy. Em qualquer função que implemente método de literatura, a docstring inclui a
referência completa e uma seção declarando as hipóteses. Se a implementação divergir do paper,
a divergência fica documentada na própria função, não em comentário solto.

Caso vigente de divergência: a estimação do fator de anualização por variância de longo prazo
em vez da soma finita de Lo (2002). Ver `02` seção 1.2.

## Determinismo

- Nenhuma chamada a `numpy.random` no nível de módulo.
- `rng = np.random.default_rng(seed)` dentro da função, com `seed` como parâmetro obrigatório.
- Nenhum estado global aleatório.
- A seed usada entra no `ValidationReport`.

## Constantes

Toda constante que participa de decisão estatística vive em `core/constants.py`, com nome
falante e a derivação completa na docstring. A tabela vigente está em `02`.

Constante sem derivação escrita é número mágico com nome. A regra não é ter nome, é ter
justificativa verificável.

## Testes

Toda função de cálculo entra com teste no mesmo commit. Mínimo exigido por função estatística:

1. **Caso analítico.** Quando existir resultado fechado, comparar com ele.
2. **Invariâncias.** Escala, permutação quando aplicável, monotonicidade esperada.
3. **Casos degenerados.** Amostra mínima, variância zero, todos os trades vencedores, todos
   perdedores, um único trade, série com valores ausentes.
4. **Propriedade sob dado sintético.** Gerar dado por processo de parâmetro conhecido e
   verificar recuperação do parâmetro dentro do erro amostral, com seed fixa.

Proibições em teste: dependência de rede, dependência de arquivo fora de `tests/fixtures/`,
tolerância frouxa escolhida para o teste passar.

Meta de cobertura em `core`: acima de 90 por cento, com a ressalva de que cobertura mede linha
executada, não correção. O que garante correção é o item 4 acima.

## Validação de entrada

Contratos validados na fronteira com pydantic ou checagem explícita. Dentro de `core`, assumir
o contrato válido e não revalidar a cada função. Validação difusa espalhada pelo código esconde
de onde veio o erro.

A fronteira inclui a verificação de coerência de P&L do `TradeLog`, porque ela depende de
multiplicador e tick mínimo, que vivem no mapa de symbology em `03`. Ver D007.

Exceções tipadas próprias:

| Exceção                 | Condição                                                          |
|-------------------------|-------------------------------------------------------------------|
| `SchemaError`           | Esquema inesperado na fonte externa                               |
| `TradeIntegrityError`   | Invariante de `TradeLog` violado, inclusive coerência de P&L      |
| `InsufficientSampleError` | Estatística não formável: menos de dois períodos, dispersão nula. Mínimo declarado é aviso, não erro. Ver D015 |
| `GridSparsityError`     | Nenhuma grade da escada satisfaz as três condições de `02` 1.1    |
| `CalendarCoverageError` | `TradingCalendar` não cobre os `exit_ts` do `TradeLog`. Ver D012  |
| `LookaheadError`        | Estatística de rotulagem usando informação futura                 |
| `RegimeSparsityError`   | Estado de regime abaixo do mínimo de observações                  |
| `UnitMismatchError`     | `EquityPaths` de unidade incompatível com a função consumidora    |

Nunca levantar `ValueError` genérico em condição prevista. Toda exceção tipada carrega no
corpo da mensagem o valor observado e o limiar violado, não apenas a descrição.

## Performance

Ordem de ataque, nessa sequência, e só avançar quando a etapa anterior estiver esgotada:

1. Vetorizar em NumPy.
2. Reduzir alocação e cópia dentro do laço de Monte Carlo.
3. `numba` na função crítica.
4. Extensão em C++ via `pybind11`, se o ganho justificar o custo de manutenção.

Nenhuma otimização entra sem medição registrada antes e depois.

## Git

- Branch por módulo, merge por pull request, mesmo trabalhando sozinho. O histórico legível é
  parte do artefato de portfólio.
- Commits pequenos, mensagem no imperativo, escopo único.
- Versionamento semântico e `CHANGELOG.md` atualizado a cada versão.
- `data/`, `.venv/`, saídas de relatório e credenciais no `.gitignore`.
- Chave de API sempre por variável de ambiente. Nunca em arquivo versionado, nem em exemplo.

## Proibições

- Notebook como fonte de verdade. Notebook fica em `examples/`, importa a biblioteca e não
  contém lógica própria.
- Dado versionado no git.
- Número mágico sem constante nomeada e justificada.
- Função de cálculo que lê arquivo ou acessa rede.
- Anualizar qualquer estatística calculada sobre `TradeReturns`. Ver D006.
- Parâmetro estatístico com valor padrão silencioso em decisão que muda o resultado, como
  comprimento de bloco, número de réplicas, nível de confiança, período da grade, calendário
  ou taxa livre de risco. Padrão pode existir, mas precisa aparecer no relatório.
