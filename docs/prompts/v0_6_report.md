# Prompt da v0.6: relatório e CLI

> Documento de tarefa escrito antes do código, conforme a instrução do projeto.

## Contexto mínimo

`01` contratos e fluxo de execução, `02` seção 7, `04`, `05` v0.6, e D004, D015, D016, D024,
D025 de `06`.

Estado: v0.5 fechada. 440 testes, `core` em 100 por cento de cobertura. Toda a estatística
existe. Esta é a primeira versão que não acrescenta método.

## Escopo

`ValidationReport` serializável, HTML autocontido, LaTeX, e o comando `qvalid validate`.

## Onde `ValidationReport` pode morar

`01` lista `ValidationReport` entre os contratos canônicos, o que sugere `contracts.py`. Não
pode: ele agrega `PeriodMetrics`, `PboResult`, `RegimeAttribution` e outros, todos de `core`, e
`core` importa `contracts`. Colocá lo lá cria ciclo.

Escolhido: `report/model.py`. A dependência aponta para dentro, `report` importa `core`
importa `contracts`, exatamente como `01` exige. `01` precisa registrar que o contrato que
agrega resultados pertence à camada que os consome.

Orquestração dos dez passos de `01` vai para `qvalid/pipeline.py`, a raiz de composição, que é o
único módulo autorizado a importar `adapters`, `core` e `report` ao mesmo tempo. `cli.py` fica
fino: analisa argumentos, chama o pipeline, escreve arquivo.

## A armadilha de determinismo, e ela decide a implementação dos gráficos

O critério de pronto de `05` é duro: duas execuções com a mesma seed produzem relatórios
idênticos byte a byte, exceto o timestamp. Isso proíbe:

- ordem de iteração de dicionário não determinada, logo chaves ordenadas em toda serialização;
- qualquer segundo carimbo de tempo além do declarado;
- **metadado de data embutido por biblioteca de gráfico.** Um SVG do matplotlib carrega um
  elemento `dc:date` e quebraria a igualdade sem que nada no código pareça errado.

Além disso, matplotlib **não está** nas dependências de `pyproject.toml`. Acrescentá lo por
causa de três gráficos é caro.

Escolhido: gerar SVG à mão, num módulo pequeno de primitivas. Mais código agora, zero
dependência nova, e determinismo por construção em vez de por configuração cuidadosa de uma
biblioteca. Gráficos necessários: curva de equity, distribuição de drawdown com o observado
marcado, e atribuição por regime em barras.

Trade off honesto: gráfico à mão é feio comparado a matplotlib e não escala para gráficos
complexos. Se a v1.1 quiser gráficos interativos, a interface web os produz a partir do JSON,
que é a serialização de referência.

## O painel de evidência é a peça conceitual, não os formatos

`02` seção 7: nenhum teste suprimido por condição de invalidez entra como se tivesse sido
aprovado, e ausência de evidência aparece como ausência, nunca como aprovação silenciosa.

Escolhido: tornar isso estrutural. Cada seção do relatório é uma entrada de evidência que
carrega **ou** um resultado **ou** um motivo de ausência, e o tipo não admite ter nenhum dos
dois. Os motivos são enumerados, não texto livre:

- `RAN`, com o resultado.
- `SUPPRESSED`, condição de invalidez de `02` 1.4 atingida, com o limiar e o observado.
- `NOT_REQUESTED`, insumo que só o usuário pode fornecer não foi fornecido. É o caso de D004:
  sem número de tentativas o DSR não roda e o relatório **declara** que a correção para busca
  não foi aplicada.
- `FAILED`, erro tipado durante a execução, com a mensagem.

Um relatório sem essa distinção deixaria o leitor concluir que um teste ausente foi um teste
passado, que é precisamente o defeito que `02` seção 7 nomeia.

## Configuração em YAML versionado

Mesmo raciocínio de D016. Todo parâmetro que muda resultado vai para um arquivo: capital
inicial, base, taxa livre de risco, seed, número de réplicas, barreira de ruína, grade forçada,
número de tentativas, caminhos de symbology e de mapeamento, série de referência de regime.
Validado com pydantic e `extra="forbid"`, e o conteúdo inteiro entra no relatório.

## Critérios de aceitação

1. **Igualdade byte a byte.** Duas execuções com a mesma seed produzem JSON, HTML e LaTeX
   idênticos depois de substituir o campo de timestamp. Sem tolerância.
2. **Completude declarada.** Um teste enumera os campos que `01` exige, seed, réplicas, versão,
   hash do input, `period`, `periods_per_year`, `calendar_id`, `basis`, `initial_capital`,
   `active_fraction`, `risk_free_rate`, largura de banda HAC e as tolerâncias de coerência, e
   falha se algum sumir.
3. **Ausência nunca vira aprovação.** Um relatório com DSR não solicitado carrega o motivo, e
   um teste verifica que nenhum caminho de código produz seção sem resultado e sem motivo.
4. **HTML autocontido.** Sem referência externa: nada de `src="http`, nada de `<link`.
5. **Degenerados.** Log pequeno demais para a escada, seção que levanta erro tipado, série de
   referência ausente.
