# Arquivos para experimentar

Três CSVs para arrastar na interface. Nenhum é dado real de corretora: os dois logs foram
construídos para o teste, e é por isso que a verdade sobre eles é conhecida e pode ser conferida.

Suba o servidor e arraste um deles no botão **Draft the configuration**:

```bash
qvalid ui
```

---

## `trades_winner.csv` — comece por este

760 trades ao longo de três anos, com expectância positiva de verdade. É o arquivo que produziu
o primeiro veredito aprovado do projeto.

O formulário chega quase todo preenchido. **Falta você digitar duas coisas**, e elas são o ponto
inteiro de D007: multiplicador e tick. Para este arquivo são `50` e `0.25`, que é o contrato do
E-mini S&P. Repare que o formulário mostra ao lado que a aritmética do próprio arquivo implica
50, e mesmo assim deixa a caixa vazia. Se você digitar 5 ou 500, a importação recusa o arquivo
inteiro em vez de produzir um relatório plausível e errado.

Fuso: `America/New_York`.

---

## `foreign_mt5.csv` — o interessante

240 trades numa exportação estilo MetaTrader, que não usa nenhuma palavra do vocabulário deste
projeto. Serve para ver a detecção trabalhando:

- as datas são `08.03.2022`, dia primeiro. Uma linha sozinha é ambígua entre 8 de março e 3 de
  agosto; a coluna inteira resolve, e o formulário chega com `%d.%m.%Y %H:%M:%S` escolhido.
- a coluna de custo vem negativa, e a convenção chega em `NEGATED` por causa disso.
- o lucro é **bruto**, antes dos custos. Troque `pnl_convention` para `GROSS`.

Multiplicador `25`, tick `0.5`, fuso `Europe/Berlin`.

---

## `trials_winner.csv` — a varredura

As vinte configurações testadas antes da vencedora. **A interface ainda não tem onde recebê-la**,
então pelo navegador o veredito sai sempre suprimido: sem a matriz não há correção para busca, e
sem correção para busca o relatório se recusa a comparar. Ver D004.

Pela linha de comando ela entra, e aí o veredito sai:

```bash
qvalid validate demo/trades_winner.csv \
  --config tests/fixtures/run_config_winner.yaml \
  --out relatorio.html
```

Esse é o relatório em que a probabilidade contra zero é 0,99931 e o Sharpe deflacionado é
0,87707. Doze pontos de confiança que pertencem à busca e não à estratégia.
