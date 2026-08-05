# 05. Roadmap

Regra: uma versão só fecha quando o critério de pronto for verificável por comando, não por
impressão. Nenhuma versão pula a suíte de testes.

Ordem escolhida por dependência técnica, não por interesse. O motor precisa existir antes de
qualquer coisa que o consuma.

Regra de granularidade: uma versão entrega um módulo de `core`. Versão que entrega dois é
sinal de que a fronteira entre eles não foi pensada.

---

## v0.1 Contratos, grade e métricas

**Status: fechada em 2026-08-04.** 271 testes, ruff limpo, cobertura de `core` em 100 por
cento e do pacote em 99. Verificável por `python examples/validate_csv.py`.

Escopo entregue: `contracts.py`, `exceptions.py`, `core/constants.py`, `core/gridding.py`,
`core/metrics.py`, `adapters/validation.py`, `adapters/calendars.py`, `adapters/symbology.py`,
`adapters/tradelog.py`.

Pronto quando:
- `TradeLog` valida e rejeita as violações de invariante previstas em `01`, incluindo a
  identidade de coerência de P&L com multiplicador.
- `gridding` escolhe a grade pela regra de `02` seção 1.1 e levanta `GridSparsityError` quando
  o conjunto viável é vazio.
- Métricas por trade e métricas anualizadas vivem em funções com assinaturas distintas, e não
  existe caminho de código que anualize `TradeReturns`.
- Sharpe com intervalo de confiança pela forma geral de `02` seção 1.3.
- Os dois casos analíticos de `02` seção 1.6 passam: diluição em forma fechada e trade único
  produzindo `1/sqrt(Y)`.
- Suíte de testes verde, incluindo os quatro tipos exigidos em `04`.

---

## v0.2 Reamostragem

**Status: fechada em 2026-08-04.** 315 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `core/resample.py`. A reamostragem markoviana de `02` seção 2.2 fica na v0.5,
onde `core/regimes.py` fornece os estados de que ela depende.

Escopo: bootstrap estacionário com seleção automática de comprimento de bloco, geração de
`EquityPaths` com `unit` propagada da entrada.

Pronto quando:
- Recuperação verificada em dado sintético i.i.d. e AR(1).
- `EquityPaths` gerado a partir de `PeriodReturns` carrega `period` e recusa mistura de
  unidades.

Verificação adicional que virou critério, ver D018: o comprimento de bloco estimado bate com o
que minimiza o erro quadrático médio por força bruta, e não apenas com a transcrição da
fórmula.

---

## v0.3 Risco

**Status: fechada em 2026-08-04.** 354 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `core/risk.py`. O critério de aceitação da forma fechada foi corrigido por D022:
vale contra a forma corrigida por continuidade, não contra a ingênua.

Escopo: `core/risk.py`. Distribuição de drawdown máximo, VaR, ES, risco de ruína, tempo até
barreira, fração de Kelly e Kelly ajustada por incerteza de parâmetro.

Pronto quando:
- Distribuição de drawdown máximo produzida a partir dos caminhos, com o drawdown observado
  posicionado como quantil dessa distribuição.
- Caminho determinístico decrescente atinge a barreira em passo exato conhecido.
- Sob passeio aleatório simétrico com barreira única, a probabilidade de ruína simulada bate
  com a solução fechada dentro do erro de Monte Carlo reportado.
- Chamada com `EquityPaths` de `unit` igual a TRADE em função que exige horizonte de calendário
  levanta erro tipado.

Justificativa da versão própria: a lógica de barreira absorvente construída aqui é a mesma
consumida pela v0.8. Enterrá la dentro da v0.2 deixaria a v0.8 dependente de código que nunca
teve critério de pronto próprio.

---

## v0.4 Testes de sobreajuste

**Status: fechada em 2026-08-04.** 404 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `core/overfit.py` e o contrato `TrialMatrix`, ver D024. Os limiares dos testes
vêm de medição sobre quatro sementes, registrada em D025.

Escopo: `core/overfit.py`. DSR, Minimum Track Record Length, PBO via CSCV, SPA.

Pronto quando:
- Formato de entrada da matriz de tentativas definido e documentado.
- Estratégia sintética construída para ser puro ruído é reprovada pelos testes.
- Estratégia sintética com edge real declarado é aprovada.
- Todas as configurações comparadas usam a mesma grade e o mesmo `periods_per_year`.

Esse par de testes é o mais importante do projeto inteiro. É a demonstração de que a
ferramenta discrimina.

---

## v0.5 Regimes

**Status: fechada em 2026-08-04.** 440 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `core/regimes.py` e o contrato `RegimeLabels`. A ausência de look ahead é
verificada de duas formas exatas, ver D026, e o teste de igualdade de médias passou a ser o de
Welch, ver D027.

Escopo: `core/regimes.py`. Rotulagem causal, matriz de transição, reamostragem markoviana,
atribuição de P&L.

Pronto quando:
- Verificação automatizada de ausência de look ahead: deslocar a série de referência para
  frente no tempo não altera nenhum rótulo já emitido.
- Estratégia sintética que só ganha em um regime é identificada corretamente.

---

## v0.6 Relatório e CLI

**Status: fechada em 2026-08-04.** 503 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `report/{model,json,svg,html,latex}.py`, `pipeline.py` e `cli.py`. A igualdade
byte a byte é verificada nas três saídas, ver D030, e a ausência de evidência virou estado
tipado, ver D031.

Escopo: `ValidationReport` serializável, saída HTML autocontida, saída LaTeX, comando `qvalid`.

Pronto quando:
- Duas execuções com a mesma seed produzem relatórios idênticos byte a byte, exceto timestamp.
- O relatório declara seed, réplicas, versão, hash do input e todos os campos de grade
  listados em `01`.

---

## v0.7 Adaptadores reais

**Status: fechada em 2026-08-04.** 564 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `adapters/cache.py` com manifesto, `adapters/market.py` com FRED, o pareamento de
duas linhas por trade, e o calendário real por YAML versionado.

Esta versão quebra a regra de granularidade de `05` por natureza, já que nada dela entra em
`core` e são quatro peças independentes.

Escopo: importadores de TradingView e NinjaTrader, adaptador de série de mercado com cache e
manifesto, FRED, calendários de negociação reais substituindo `WEEKDAYS_UTC`.

Pronto quando:
- Cache evita segundo download comprovadamente. **Feito**, com buscador que conta chamadas, ver
  D033.
- Manifesto registra todo recorte baixado. **Feito**, append only e registrando também os
  acertos de cache.
- Mapa de symbology alimenta multiplicador e tick mínimo na validação de coerência de P&L.
  **Feito desde a v0.1**, e agora com teste de regressão.
- Pareamento de duas linhas produz o mesmo `TradeLog` que o formato de uma linha. **Feito**,
  ver D034.
- FRED atrás do protocolo de busca, com chave por variável de ambiente e parsing separado da
  rede. **Feito.** A única chamada de socket do pacote fica isolada em três linhas não cobertas
  por teste, deliberadamente: mockar `urlopen` seria testar o mock.
- Calendário real substituindo `WEEKDAYS_UTC`. **Feito**, ver D035. Produz 251,36 sessões por
  ano contra 261,04 do sentinela, e o efeito de -1,87 por cento no Sharpe anualizado confirma a
  previsão de 1,8 por cento escrita na derivação de `WEEKDAYS_PER_YEAR` na v0.1.

---

## v0.8 Mesa proprietária

**Status: fechada em 2026-08-04.** 606 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `core/propfirm.py` e três conjuntos de regras em `tests/fixtures/propfirm/`.
Decisões em D036, D037 e D038.

Escopo: `core/propfirm.py` com regras em YAML.

Pronto quando:
- Casos degenerados determinísticos produzem o resultado exato esperado.
- Pelo menos três conjuntos de regras distintos configurados apenas por YAML, sem tocar código.
- Recusa `EquityPaths` que não seja de unidade diária.

---

## v0.9 Veredito

**Status: fechada em 2026-08-04.** 653 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: `core/verdict.py` e a integração ao pipeline. Decisões em D039 e D040.

Achado ao integrar: com a lista estrita de requisitos, o pipeline **nunca** produz veredito,
porque o Sharpe deflacionado precisa da matriz de tentativas e um log de trades sozinho não pode
fornecê la. É D004 propagando até o fim, e o relatório de exemplo mostra exatamente isso.

Escopo: `core/verdict.py`. Painel de evidência e ordenamento por equivalente certeza sob CPT.

Pronto quando:
- Teste suprimido por condição de invalidez aparece como ausente no painel, nunca como
  aprovado.
- O ordenamento por equivalente certeza é reproduzível a partir dos parâmetros de utilidade e
  ponderação declarados no relatório.

---

## v1.0 Publicação

**Status: fechada em 2026-08-05.** 671 testes, `core` em 100 por cento de cobertura, pacote em
99. Entregue: README, licença MIT, CI, referência commitada do relatório, e o pacote renomeado
para `qvalid`. Decisões em D041 a D045.

Escopo: documentação, exemplo reprodutível ponta a ponta, README com resultado ilustrativo,
CI rodando testes, licença.

Pronto quando:
- Alguém clona o repositório e reproduz o exemplo com um comando. **Feito e verificado por
  comando**, não por impressão: clone real, pacote construído e instalado em alvo isolado sem a
  árvore de fontes no caminho, `python examples/validate_full.py`, e o relatório bate com
  `tests/fixtures/expected_report.json` exatamente, sobre 144 valores.
- README explica em menos de uma página o que a biblioteca decide e com base em quê. **Feito.**
  Os números que ele cita estão fixados em teste, então a página não pode deixar de ser verdade
  em silêncio.
- Nome do pacote definitivo registrado no PyPI. Ver D009. **Resolvido em D045**: `qval` está
  ocupado, `quantify` também. O nome é `qvalid`. Falta o ato de publicar, que depende de conta e
  de token e portanto é do dono do projeto, não da suíte.

Três achados que a preparação da versão produziu, todos corrigidos e nenhum visível antes de
alguém perguntar "isso reproduz em outra máquina?":
- O BLAS quebrava a igualdade byte a byte acima de 10⁵ observações. Ver D041.
- A proveniência gravava caminho absoluto, o que impede reprodução em outro checkout e ainda
  vaza o diretório pessoal em um relatório feito para ser entregue. Ver D042.
- Três dependências declaradas desde a v0.1 nunca foram importadas. Ver D044.

Ressalva declarada: a igualdade byte a byte entre sistemas operacionais é afirmada pela matriz
de CI e não por medição feita aqui, porque só existe Linux neste ambiente e `math.log` e
`math.exp` diferem entre bibliotecas C. Ver D043.

---

## v1.1 e além: interface

Só depois da v1.0, e sob a condição de que a API pública esteja estável.

Critério de entrada:
- Nenhuma alteração de assinatura pública nas duas últimas versões.
- Cobertura de `core` acima da meta.

Caminho sugerido, em duas etapas:

1. Interface local para uso pessoal, priorizando velocidade de construção sobre acabamento.
   Objetivo: parar de digitar comando para rodar validação rotineira.
2. Se e somente se a etapa 1 for usada de fato por algumas semanas, aí sim uma aplicação
   servida por API, com fila para as simulações longas.

Restrição permanente: nenhuma lógica de cálculo na camada de interface. A interface chama a
API pública e renderiza o `ValidationReport`. Qualquer cálculo que apareça no front é dívida
que impede voltar ao CLI e quebra a reprodutibilidade.
