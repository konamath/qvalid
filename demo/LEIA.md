# Arquivos para experimentar

Os nomes estão em caixa alta e numerados de propósito. A primeira versão desta pasta tinha
`trades_winner.csv` e `trials_winner.csv`, que diferem em duas letras, começam igual, terminam
igual e ficam coladas na lista do Finder. Deu no que tinha que dar: o arquivo errado foi escolhido
duas vezes seguidas. Nomes parecidos fazendo a coisa errada ser lida é literalmente o assunto
deste projeto, e eu o reproduzi na pasta de exemplos.

---

## `1-LOG-DE-TRADES-vencedor.csv` — comece por este

760 trades ao longo de três anos, com expectância positiva de verdade.

Na tela de configuração, três coisas para você preencher:

| campo | valor |
|-------|-------|
| Multiplier | `50` |
| Tick size | `0.25` |
| Time zone | `America/New_York` |

Todo o resto já chega certo. O veredito vai sair **suprimido**, e isso é correto: sem a matriz
de tentativas não há correção para busca, e sem ela a ferramenta se recusa a concluir. Ver D004.

---

## `2-LOG-DE-TRADES-metatrader.csv` — o interessante

240 trades numa exportação que não usa nenhuma palavra do vocabulário deste projeto. Serve para
ver a detecção trabalhando: as datas são `08.03.2022`, dia primeiro, e o formulário chega com
`%d.%m.%Y %H:%M:%S` já escolhido porque a coluna inteira resolveu a ambiguidade. A convenção de
custo chega em `NEGATED` porque a coluna vem negativa.

| campo | valor |
|-------|-------|
| Multiplier | `25` |
| Tick size | `0.5` |
| Time zone | `Europe/Berlin` |
| P&L column | trocar para `GROSS` |

---

## `NAO-E-UM-LOG-matriz-de-tentativas.csv`

As vinte configurações testadas antes da vencedora. **Não sobe na interface.** É uma matriz de
retornos por período, não um log de trades, e a página vai dizer isso se você tentar.

Ela entra pela linha de comando, e é por ela que o veredito sai:

```bash
cd ~/Desktop/quantify
qvalid validate demo/1-LOG-DE-TRADES-vencedor.csv \
  --config tests/fixtures/run_config_winner.yaml \
  --out relatorio.html
open relatorio.html
```

Esse é o relatório em que a probabilidade contra zero é 0,99931 e o Sharpe deflacionado é
0,87707. Doze pontos de confiança que pertencem à busca e não à estratégia.
