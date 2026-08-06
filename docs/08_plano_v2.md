# 08. Plano do ciclo v2

Escrito depois de ler `00` a `06` e de verificar o QuantPad no site em agosto de 2026. Formato de
`05`: versões numeradas, capacidade por versão, critério de pronto verificável, e o que cada uma
deliberadamente não faz.

---

## Parte 1. Validador ou plataforma

### O achado que reordena a pergunta

Antes de comparar com qualquer coisa, uma medição no próprio repositório:

    funcoes publicas em core/propfirm.py    2
    chamadas a propfirm no pipeline         0
    chamadas a superior_predictive_ability  0
    secoes que o painel do relatorio monta  11

`core/propfirm.py` implementa a seção 6 inteira de `02`: alvo de lucro, perda máxima estática e
móvel, limite diário, dias mínimos, fase de avaliação e fase financiada, com probabilidade de
aprovação, probabilidade de saque, valor esperado líquido do custo da avaliação e percentis de
dias até o primeiro saque. Está construído, especificado e testado. **Nenhum usuário consegue
alcançá lo.** O mesmo vale para `superior_predictive_ability`, que é o teste de Hansen (2005) da
seção 3.

Isto importa porque o QuantPad anuncia exatamente a primeira dessas duas como manchete: *"Stop
paying to fail prop challenges. Upload your trade log and QuantPad runs thousands of Monte Carlo
simulations against the real rules of Topstep, Apex, Take Profit Trader and more."*

Ou seja: a funcionalidade de destaque do concorrente já existe aqui, com a matemática escrita e
verificada, e o que falta são as regras em YAML e uma seção no painel. Isto não é uma lacuna de
capacidade, é um cano que não foi ligado. É o mesmo defeito que D052 achou na matriz de
tentativas e D056 na seção 3.2, agora pela terceira e quarta vez.

**Consequência para a pergunta.** Comparar com o QuantPad antes de ligar o que já existe é
comprar terreno tendo cômodo vazio dentro de casa.

### O custo honesto de cada lado

**Como plataforma**, seriam quatro produtos, e nenhum deles é o atual:

| O que | Custo real | Obstáculo antes da engenharia |
|-------|-----------|------------------------------|
| Dados de mercado | Cadeia OPRA, L2, 16 anos de futuros | **Licença de redistribuição.** Consumir dado pago para pesquisa própria é uma coisa; entregar uma ferramenta que serve esse dado a terceiros é ser revendedor de dado. É o modelo de negócio do QuantPad, não um recurso dele. |
| Agente de IA | Um produto do tamanho do atual | Concorre de frente com Cursor e Claude Code, que o próprio QuantPad integra por MCP em vez de competir. |
| Geração em DSL | Quatro linguagens, quatro linters | Cada uma exige acompanhar mudanças de plataforma indefinidamente. |
| Nuvem e comunidade | Infraestrutura, cobrança, suporte, efeito de rede | Efeito de rede exige usuários. Hoje há um. |

**Como validador**, o escopo é o de hoje, e a posição defensável é específica. O QuantPad não
mostra ter, e a arquitetura dele torna difícil ter:

- Deflação com **número de tentativas declarado pelo usuário**. D004 recusa estimar esse número.
  Uma plataforma que gera as estratégias sabe quantas gerou, e portanto tem um incentivo direto
  para relatar o número que favorece o resultado.
- **Ausência tipada** em vez de nota. Ver adiante.
- **Reprodutibilidade byte a byte** com hash de proveniência do log e da configuração.

### O argumento que decide, e não é sobre tamanho

**Um validador que também gera a estratégia perde a independência que o torna um validador.**

No QuantPad o mesmo agente escreve a estratégia, roda o backtest e emite a nota. É o auditor
auditando o próprio balanço. Não é acusação de má fé: é conflito estrutural, e vale mesmo quando
todos os envolvidos são honestos, porque o número de configurações tentadas passa a ser um dado
interno de quem está sendo julgado.

Quantify não tem esse conflito **porque não gera nada**. Ele recebe um log que outra pessoa
produziu, em outra ferramenta, e pergunta quantas configurações foram tentadas para chegar
naquilo. Essa pergunta só é honesta quando quem pergunta não é quem tentou.

Construir agente e backtester destruiria essa posição, e destruiria de graça, porque o mercado
de "IDE quant com IA" já tem ocupante com dado licenciado.

### Recomendação

**Validador, aprofundado.** Com uma correção de rota: o Quantify não é concorrente do QuantPad,
é **complemento**. Alguém que use o QuantPad para descobrir e codificar uma estratégia ainda
precisa de algo independente que diga se o backtest sobrevive à correção para busca. Esse é o
lugar, e ele fica melhor quanto mais gente usar plataformas de geração de estratégia.

O caminho recomendado **não revoga nenhuma decisão de `docs/06`.** Isso não é coincidência: é o
sinal de que as setenta decisões já apontavam para cá, e a pressão para virar plataforma vem da
comparação, não do produto.

---

## Parte 2. A nota de A a F, e a terceira forma

`01` lista nota única entre os não objetivos. D040 substituiu por equivalente certeza sob CPT,
com o argumento de que a objeção não é imprecisão, é ausência de interpretação: um "A" não
responde pergunta nenhuma. D031 construiu quatro estados tipados de ausência.

**A nota fica proibida.** Mas a pressão por ela é legítima e vale nomear: olhando o relatório
atual, o que se vê são tabelas de chaves cruas. `kelly_fraction 26.4062` sem uma linha dizendo
que Kelly puro sobre série de Sharpe alto e vol baixa dá alavancagem absurda por construção.
`never_hit_fraction 1` sem tradução. **O relatório é honesto e ilegível**, e é a ilegibilidade
que faz uma nota parecer atraente.

### A terceira forma: leitura e cobertura, não nota

Duas coisas, ambas deriváveis do painel que já existe, nenhuma exigindo estatística nova.

**1. Cobertura.** Um número glanceável que mede **completude, não qualidade**:

    8 de 11 verificações rodaram.

Uma nota finge ser isto e não é. Cobertura é verificável, não colapsa evidência heterogênea, e
não pode ser lida como aprovação.

**2. Uma frase de leitura**, gerada do painel, que nomeia o que concluiu **e o que não rodou na
mesma frase**:

> O Sharpe anualizado de 2,13 é distinguível de zero, com intervalo de 0,99 a 3,27, e o registro
> de 760 períodos já excede os 158 exigidos. **Não foi verificado** se ele sobrevive à busca que o
> produziu, porque o número de configurações testadas não foi declarado, e por isso não há
> veredito.

Isso tem a legibilidade de uma nota e é impossível de ler como aprovação, porque a ausência está
dentro da sentença em vez de numa tabela abaixo dela. É exatamente o que a seção 7 de `02` exige
quando diz que ausência de evidência tem que aparecer como ausência.

Vira **D071**, refinando D031 e D040 sem revogar nenhuma das duas.

---

## Parte 3. As versões

### v2.0 O que já está construído chega ao relatório

**Escopo.** Ligar `core/propfirm.py` e `superior_predictive_ability` ao pipeline e ao painel.
Arquivos de regra em YAML versionado para mesas reais, conforme o requisito de projeto já escrito
na seção 6 de `02`.

**Critério de pronto.**
- `qvalid validate` produz seção `propfirm` com probabilidade de aprovação, probabilidade de
  saque, valor esperado líquido do custo da avaliação e percentis de dias até o primeiro saque,
  para pelo menos duas mesas reais.
- A seção sai `NOT_REQUESTED` com motivo quando nenhum arquivo de regra é declarado, e
  `SUPPRESSED` com observado e limiar quando a grade não é diária, conforme D036.
- Seção `spa` roda quando uma série de comparação é fornecida, `NOT_REQUESTED` quando não.
- Cada arquivo de regra carrega campo `verified_on` com data e URL da regra publicada, e o
  relatório imprime essa data ao lado do resultado.

**O que não faz.** Não acrescenta matemática. Não inventa regra de mesa: regra sem `verified_on`
é recusada na carga.

**O que exige que não existe.** Decisão sua sobre **quais mesas**. Topstep, Apex e Take Profit
Trader são as que o QuantPad nomeia, todas americanas. Você é brasileiro; se opera mesa nacional,
essa entra primeiro. E uma decisão registrada sobre regra de terceiro em repositório público:
elas mudam, e um arquivo desatualizado que roda em silêncio é a falha desta semana com outra
roupa. O campo `verified_on` é a mitigação proposta.

---

### v2.1 O veredito passa a ser alcançável

**Escopo.** A matriz de tentativas chega ao navegador, e um comando novo constrói a matriz a
partir dos logs que a pessoa já tem.

**Critério de pronto.**
- Pelo navegador, com log e matriz, sai veredito não suprimido.
- `qvalid trials a.csv b.csv ... --config cfg.yaml -o trials.csv` projeta cada log na mesma grade
  e escreve a matriz alinhada, recusando quando as grades diferem, o que D024 já torna estrutural.
- Um teste percorre: vinte logs, matriz construída, relatório com veredito rankeável.

**O que não faz.** Não estima o número de tentativas, e não aceita contagem sem matriz. D004
permanece intacta. Não constrói matriz a partir de um único log.

**O que exige que não existe.** Nada externo. Esta é a versão que fecha a lacuna mais séria do
produto: hoje a conclusão que dá nome à ferramenta é inalcançável pela interface que a v1.16
acabou de terminar.

---

### v2.2 O relatório passa a ser legível sem virar nota

**Escopo.** Cobertura, frase de leitura, e uma linha de leitura por seção onde um número é
notoriamente mal lido.

**Critério de pronto.**
- Um teste afirma que **toda** seção ausente aparece nomeada na frase de leitura.
- Um teste estrutural proíbe qualquer letra de A a F, e qualquer pontuação agregada, em todo o
  módulo de relatório.
- A frase é derivada do painel sem calcular nada novo, verificado por teste que compara os
  números citados com os do painel.
- `kelly_fraction` sai acompanhado da nota de que fração de Kelly bruta sobre esta amostra é
  alavancagem que ninguém aplica, com o motivo.

**O que não faz.** Nenhuma nota, nenhum agregado, nenhuma estatística nova.

**O que exige que não existe.** Julgamento seu sobre a redação de cada linha de leitura. É texto
que a pessoa vai ler antes dos números, e texto errado aqui é pior que tabela crua.

---

### v2.3 O primeiro uso real

**Escopo.** Rodar tudo sobre uma exportação de verdade da sua corretora. Consertar o que quebrar.

**Critério de pronto.** Um relatório sobre o seu log real, com pelo menos três números conferidos
contra o extrato da corretora por fora da biblioteca, e cada defeito encontrado com entrada em
`06` e teste na camada que deveria tê lo pego.

**O que não faz.** Não constrói nada especulativo. Só conserta o que o arquivo real quebrar.

**O que exige que não existe.** **A sua exportação.** Esta versão está bloqueada em você desde a
v1.0. Quatro sessões suas olhando telas acharam oito defeitos que 908 testes não veem; um arquivo
que ninguém construiu para passar vai achar mais, e de outra natureza.

---

### v2.4 Comparação entre estratégias

**Escopo.** Ordenar vários logs entre si, que é para o que `verdict.rank` foi construído e o que
o SPA responde.

**Critério de pronto.**
- `qvalid compare a.yaml b.yaml c.yaml` produz o ordenamento por equivalente certeza, com os
  não ordenáveis listados à parte e o motivo de cada um, conforme D039.
- Recusa comparar logs em grades diferentes, com erro tipado citando as duas grades.
- As preferências CPT usadas saem impressas ao lado do ordenamento, conforme D040.

**O que não faz.** Não ordena por Sharpe. Não compara através de grades diferentes. Não escolhe
parâmetros CPT por você: os padrões de Tversky e Kahneman são médias de laboratório, e D040 já
diz que quem for dimensionar posição deve fornecer os próprios.

**O que exige que não existe.** Nada externo.

---

## Parte 4. O que eu recomendo não construir

Esta lista vale tanto quanto a de cima.

**1. Superfície de volatilidade e qualquer análise de opções.** Exige cadeia com bid, ask e last
por strike e vencimento, solucionador de volatilidade implícita, ajuste livre de arbitragem em
borboleta e calendário, e curva de juros e dividendos. A matemática é a parte barata. `03` já põe
cadeia completa de opções fora de escopo da v1, e o obstáculo real é licença: consumir dado pago
para pesquisa própria é uma coisa, **entregar uma ferramenta que serve esse dado a terceiros é
ser revendedor de dado**, que é o negócio do QuantPad e não um recurso dele.

Se você pessoalmente quer uma superfície de vol para uma pesquisa sua, isso é um notebook com a
sua própria chave, não uma funcionalidade do produto. Verifique preço vigente antes de qualquer
compra, como `03` já manda.

**2. Nota de A a F.** Contradiz `01` e D040. A v2.2 entrega a legibilidade sem o custo.

**3. Agente de IA e geração em DSL.** Quatro linguagens, quatro linters, acompanhamento
indefinido de mudança de plataforma, e concorrência frontal com ferramentas que o próprio
QuantPad prefere integrar a combater.

**4. Motor de backtest.** É o mais tentador e o mais destrutivo. **Um validador que gera a
estratégia deixa de ser independente daquilo que julga**, e a independência é a única coisa que
o Quantify tem e o QuantPad estruturalmente não pode ter.

**5. Nuvem, contas e comunidade.** Efeito de rede exige usuários, e hoje há um. Além disso a
promessa de que nada sai da máquina é hoje verdadeira e verificável, e é um argumento de venda
para quem não quer entregar o próprio histórico de trades a um terceiro.

**6. Assinatura de dado ao vivo.** D002 decidiu compra por recorte com cache imutável, e o uso
real do projeto continua esparso.

---

## Parte 5. Decisões a registrar

Nenhuma revogação é necessária no caminho recomendado, e vale repetir: isso é evidência de que as
setenta decisões existentes já apontavam para cá.

**D071. Validador e não plataforma, e o motivo é independência.** Registra a escolha, o custo dos
quatro produtos descartados, e o argumento estrutural de que gerar a estratégia destrói a posição
de quem a julga. Constrange escopo futuro: qualquer proposta de gerar sinal, código ou backtest
passa a exigir revogação explícita desta entrada.

**D072. Leitura e cobertura em vez de nota.** Refina D031 e D040 sem revogar. Registra a
observação de que a ilegibilidade do relatório é o que torna a nota atraente, e que a correção
é traduzir, não resumir.

**D073. Regra de mesa é dado de terceiro com data de verificação.** Registra que arquivo de regra
carrega `verified_on` e URL, que a carga recusa arquivo sem esse campo, e que o relatório imprime
a data. Regra de mesa muda; arquivo desatualizado rodando em silêncio é a mesma classe de falha
que esta semana inteira encontrou.

---

## Ordem sugerida e o motivo

    v2.1  ->  v2.0  ->  v2.2  ->  v2.3  ->  v2.4

A v2.1 vem primeiro porque a conclusão que dá nome à ferramenta é hoje inalcançável pela
interface que acabou de ser construída, e isso é mais grave que qualquer ausência. A v2.0 vem
em seguida porque entrega uma manchete inteira do concorrente com código que já está escrito e
testado. A v2.2 torna as duas primeiras legíveis. A v2.3 é a única que depende de você e deveria
ser puxada para frente no instante em que você tiver o arquivo.
