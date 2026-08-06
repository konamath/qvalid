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

Quarto achado, este do próprio ato de ligar a CI: `mypy src` acusava 39 erros, e um deles era um
`AttributeError` na metade `str` da assinatura pública de `run_validation`, introduzido hoje pela
correção de D042. Sobreviveu à suíte inteira, ao exemplo e à verificação por clone limpo. Os 39
foram corrigidos, não silenciados. Ver D046.

A ressalva declarada no fechamento, de que a igualdade entre ambientes não tinha sido medida,
resolveu se contra a versão escrita. A CI mediu: dois dos 144 valores se movem de um a dois ULP
ao trocar a versão de numpy e scipy, no **mesmo** sistema operacional. O critério passou a ser
igualdade exata dentro de um ambiente mais concordância a `1e-9` relativo contra a referência,
derivado dos seis algarismos que o relatório renderiza. Ver D049, que substitui D043.

Registro honesto do que isso significa: das quatro reprovações que a CI produziu depois do
fechamento, três eram defeitos reais que nenhuma verificação local podia ver, e uma era uma
afirmação forte demais no README. "Verificável por comando" valia para o meu ambiente, não para o
de quem clona. A regra de abertura de `05` continua certa; o que faltava era dizer em qual
ambiente o comando roda.

---

## v1.1 e além: interface

Só depois da v1.0, e sob a condição de que a API pública esteja estável.

Critério de entrada:
- Nenhuma alteração de assinatura pública nas duas últimas versões.
- Cobertura de `core` acima da meta.

Caminho sugerido, em duas etapas:

1. **Etapa 1 entregue em 2026-08-05.** `qvalid ui` sobe um servidor em localhost e abre o
   navegador. Zero dependência nova, `http.server` da biblioteca padrão, e a página de resultado
   é o próprio relatório que `report/html.py` já produzia. Ver D057.

   **O critério de entrada não foi cumprido.** As duas últimas versões mudaram assinatura pública
   várias vezes, e seguimos assim mesmo a pedido. Fica registrado como decisão consciente e não
   como esquecimento: a regra existia para evitar interface presa a API instável, e a mitigação
   foi expor apenas duas funções, `run_validation` e `render_html`.
2. Se e somente se a etapa 1 for usada de fato por algumas semanas, aí sim uma aplicação
   servida por API, com fila para as simulações longas.

### v1.6 A parede do mapeamento, entregue em 2026-08-05

Com a interface pronta, o que separava a ferramenta do primeiro uso real deixou de ser o caminho
do arquivo e passou a ser o mapeamento: três YAML escritos à mão antes de qualquer número.

`qvalid inspect log.csv` lê o cabeçalho e imprime um rascunho, marcando o que não conseguiu
resolver em vez de escolher. Imprime e não grava, porque sob D016 o mapeamento é proveniência.
Ver D060, inclusive as três medições que fixaram o limiar de truncamento, a simetria da colisão
e o acordo com o mapeamento que uma pessoa escreveu à mão.

**Ainda não feito, e é o que falta:** rodar a ferramenta sobre uma exportação real da corretora
do autor. Todo número verificado até aqui veio de fixture, de dado sintético ou do S&P 500 do
FRED. Enquanto isso não acontecer, a etapa 2 abaixo não tem insumo para ser decidida.

### v1.7 A segunda parede, entregue em 2026-08-05

`qvalid probe log.csv -m mapping.yaml` inverte a identidade de P&L e recupera o multiplicador
que o arquivo implica, por símbolo, imprimindo ao lado de um campo vazio e nunca dentro dele.
Ver D061.

Sobra `run_config.yaml`, e ele é o que deve mesmo sobrar: todo campo dele é escolha da pessoa,
capital inicial, semente, taxa livre de risco, barreira de ruína, e nenhum é adivinhável a
partir do arquivo de trades.

**Continua não feito:** rodar sobre uma exportação real de corretora. As duas ferramentas de
diagnóstico foram construídas exatamente para esse momento e nunca viram um arquivo que não
fosse fixture, sintético ou o S&P 500 do FRED.

### v1.8 A primeira exportação estrangeira, entregue em 2026-08-05

Percurso completo, `inspect` → `probe` → `validate`, sobre um arquivo com vocabulário que o
projeto nunca viu: data dia primeiro, custos negativos, lucro antes dos custos. Quatro defeitos
apareceram, três corrigidos e um registrado como candidato. Ver D062.

A regra que sai disso: fixture escrita pelo projeto confere o código contra ele mesmo. Os quatro
defeitos passaram por 801 testes com cobertura acima da meta, ruff e mypy limpos.

**O que ainda falta, e é diferente:** uma exportação real de corretora. O arquivo estrangeiro
ainda foi fabricado por quem escreveu o teste, e só um arquivo que ninguém construiu para passar
pode achar o que não se sabe procurar.

### v1.9 A interface guiada, entregue em 2026-08-05

`POST /setup` recebe o log sozinho e devolve os três arquivos rascunhados com a evidência do
`probe` ao lado. Ver D063. Os rascunhos saíram de dentro do `cli.py` para `drafts.py`, com teste
afirmando que navegador e linha de comando emitem os mesmos bytes.

A etapa 1 de v1.1 agora cobre de fato o que prometia. A etapa 2, aplicação servida com fila,
continua condicionada a semanas de uso real, e o uso real continua sendo o que falta.

### v1.10 A seção que falhava sem ter falhado, entregue em 2026-08-05

`track_record` passa a rodar quando o Sharpe é negativo, dizendo que nenhum comprimento de série
basta, em vez de sair como `FAILED`. Ver D064. Era o achado que D062 deixou em aberto, e ele era
maior do que o nome de uma exceção: o relatório mais completo do projeto tinha uma seção ausente
onde nada tinha dado errado, o que inverte a regra de `02` seção 7.

### v1.11 A ferramenta aprova, entregue em 2026-08-05

Todo relatório que o projeto já tinha produzido terminava em veredito negativo ou suprimido, e o
teste do veredito se chamava "estratégia perdedora ainda recebe veredito negativo". Faltava a
fixture vencedora com a busca que a produziu. Ver D065.

Ela aprova, e nenhum defeito de assimetria apareceu. Contra zero o Sharpe é quase certo, 0,99931;
contra a melhor de vinte configurações, 0,87707. Essa diferença é o produto inteiro.

### v1.12 A interface interativa, entregue em 2026-08-05

Controles de verdade no lugar das três caixas de YAML: prévia das linhas do arquivo, um menu por
campo com as colunas reais, convenções pré-selecionadas a partir da evidência. Ver D066.

O melhor pedaço é que o formato de data passou a ser **lido da coluna inteira**. Uma linha é
ambígua entre dia-primeiro e mês-primeiro; 240 linhas resolvem. Quando não resolvem, as duas
leituras são mostradas lado a lado em vez de uma ser escolhida.

### v1.13 O que apareceu quando alguém usou, entregue em 2026-08-05

Dois defeitos na primeira sessão de uso real da interface, nenhum deles pego por 882 testes.
Ver D067. O grave: campo numérico obrigatório que voltava vazio virava o valor sugerido em
silêncio, então um navegador que recusasse uma vírgula decimal produzia relatório sobre a conta
errada. O barato: subir o arquivo errado dava dez avisos pequenos em vez de um grande.

### v1.14 A porta certa primeiro, entregue em 2026-08-05

A página inicial abria pedindo a configuração, que na primeira vez ninguém tem, e escondia o
caminho guiado embaixo. Invertida. Ver D068. Achado por alguém perguntando onde clicar.

### v1.15 O caminho do navegador nunca tinha funcionado, entregue em 2026-08-05

O formulário de configuração não declarava `enctype`, o navegador postava urlencoded, o servidor
só parseava multipart, e todo campo chegava vazio. A pessoa via "upload expirado", que era falso.
Ver D069. O formulário interativo da v1.12 nunca havia recebido uma submissão real.

### v1.16 O gráfico que desenhava cinco números, entregue em 2026-08-06

Primeira corrida completa pelo navegador. Funcionou, e o relatório trouxe um gráfico de drawdown
que fazia histograma dos cinco quantis, com a lista duplicada para as barras subirem. Ver D070.
O número estava certo e a figura ao lado dele estava errada.

Restrição permanente: nenhuma lógica de cálculo na camada de interface. A interface chama a
API pública e renderiza o `ValidationReport`. Qualquer cálculo que apareça no front é dívida
que impede voltar ao CLI e quebra a reprodutibilidade.
