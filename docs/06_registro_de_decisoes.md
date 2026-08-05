# 06. Registro de decisões

Formato de cada entrada: identificador, data, status, contexto, decisão, alternativas
descartadas, consequência. Decisão revogada não é apagada. Ela é marcada como substituída,
com referência à entrada que a substitui.

Status possíveis: proposta, aceita, substituída, revogada.

---

## D001. Biblioteca antes de interface

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** A referência do produto comercial é uma IDE web com agente embutido. Construir
interface consome a maior parte do tempo e é a camada que menos comunica competência técnica.

**Decisão.** Construir uma biblioteca Python com CLI. Interface fica para depois da v1.0,
condicionada à estabilidade da API pública.

**Alternativas descartadas.** Começar por interface, porque facilita o uso diário. Descartada
por inversão de dependência: a interface depende do motor, o motor não depende dela. Começar
por interface produz lógica dentro do front e trava a evolução.

**Consequência.** Uso diário fica menos confortável até a v1.1. Em contrapartida, a interface
posterior é trabalho de apresentação, não de reescrita.

---

## D002. Dado sob demanda em vez de assinatura

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** O custo do produto de referência vem majoritariamente do arquivo de dados
licenciado, não do software. O uso real de pesquisa é esparso.

**Decisão.** Fontes gratuitas como base e compra por volume apenas para recortes específicos,
com cache local imutável e manifesto de procedência.

**Alternativas descartadas.** Assinatura mensal de provedor institucional, que garante acesso
amplo mas cobra por disponibilidade que não se consome em pesquisa individual.

**Consequência.** Nenhum estudo que exija varredura ampla de tick sobre muitos anos. Escopo de
mercados deliberadamente estreito.

---

## D003. Motor de validação agnóstico à fonte

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Acoplar cálculo a provedor de dado torna o teste lento, dependente de rede e
frágil a mudança de API externa.

**Decisão.** `core` não importa `adapters`. Todo cálculo opera sobre contratos canônicos.

**Consequência.** Suíte de testes offline e determinística. Custo: uma camada extra de
tradução em cada adaptador novo.

---

## D004. O usuário informa o número de tentativas

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Deflated Sharpe Ratio e PBO exigem saber quantas configurações foram testadas.
Esse dado não é recuperável a partir do log de trades da configuração vencedora.

**Decisão.** Se o número de tentativas não for informado, os testes da seção 3 de `02` não
rodam e o relatório declara explicitamente que a correção para busca não foi aplicada.

**Alternativas descartadas.** Estimar o número de tentativas por heurística. Descartada porque
seria fabricar o insumo que determina o resultado.

**Consequência.** Relatório mais honesto e menos completo em muitos casos de uso. É o
comportamento correto.

---

## D005. Nome do pacote

**Data.** 2026-08-04
**Status.** substituída por D009

**Contexto.** `qvalid` é provisório. Verificar disponibilidade no PyPI antes de fixar.

**Decisão.** Manter `qvalid` até a v0.5. Trocar exige alterar um único ponto, já que o nome não
aparece hardcoded fora do `pyproject.toml` e dos imports.

**Motivo da substituição.** O prazo estava ancorado em número de versão, e a renumeração de
D008 moveu o significado de v0.5. Prazo de decisão deve ser ancorado em evento, não em rótulo
que outra decisão pode deslocar.

---

## D006. Sharpe vive na grade calendário, nunca no índice de trade

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** A seção 1 de `02` exigia Sharpe anualizado com a correção de Lo (2002) sobre um
log de trades. O fator de anualização `eta(q)` é definido sobre autocorrelações em tempo
calendário e sobre um número fixo de períodos por ano. Nenhum dos dois existe sobre uma série
indexada por número de trade, cujo espaçamento é irregular e cuja taxa de chegada é realização
amostral, não parâmetro da estratégia. O erro padrão, ao contrário do fator de escala, não
exige grade: as duas coisas estavam fundidas na especificação.

**Decisão.** Três partes.

1. Separar `TradeReturns` e `PeriodReturns` como contratos distintos. Toda estatística
   anualizada opera sobre `PeriodReturns`, com `period`, `periods_per_year` e `calendar_id`
   declarados na fronteira e nunca inferidos pelo motor. Estatísticas nativas por trade nunca
   são anualizadas, e a proibição é estrutural: a assinatura da função de anualização não
   aceita `TradeReturns`.
2. Atribuir P&L ao período que contém `exit_ts`. Marcação a mercado fica fora de escopo em
   qualquer versão.
3. Anualizar por variância de longo prazo estimada por Newey e West (1987) com largura de
   banda automática de Newey e West (1994), e estimar o erro padrão pela forma geral do método
   delta com o mesmo HAC, que se reduz a Mertens (2002) sob independência. Taxa livre de risco
   é parâmetro obrigatório com padrão zero impresso no relatório.

A grade é escolhida pela regra da escada em `02` seção 1.1: a mais fina que satisfaz fração
ativa mínima, número mínimo de períodos e duração mediana de posse compatível.

**Alternativas descartadas.** Sharpe por trade anualizado pela contagem observada de trades
por ano, descartada porque embute independência e taxa de chegada estacionária sem declarar
nenhuma das duas. Marcação a mercado para distribuir P&L ao longo da posse, descartada porque
exigiria adaptador de mercado dentro de uma métrica descritiva e quebraria D003. Estimar
`eta(q)` pela fórmula literal, descartada por inviabilidade estatística: 251 autocorrelações a
partir de poucas centenas de observações. Grade fixa em diário, descartada porque estratégia
de posse longa e baixa frequência cai no caso degenerado de trade único.

**Consequência.** A v0.1 ganha um módulo, `core/gridding.py`, e um pré requisito: definir a
grade antes de calcular qualquer número anualizado. Métricas por trade e métricas calendário
passam a viver em funções separadas, sem caminho de conversão entre elas. O Sharpe reportado
fica sistematicamente menor do que o calculado apenas sobre períodos ativos, pelo fator
`1/sqrt(1 + (1-p) * s^2)`, o que é o comportamento correto porque capital parado é capital
alocado. Estratégias muito esparsas passam a ser recusadas com `GridSparsityError` em vez de
receber um Sharpe que mede contagem de períodos.

---

## D007. Multiplicador entra no contrato, coerência é verificada na fronteira

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `01` exigia `pnl` coerente com preços e quantidade dentro de tolerância. Para
futuros a identidade envolve multiplicador de contrato, e a tolerância envolve tick mínimo.
Ambos vivem no mapa de symbology, ou seja, na camada de adaptadores. `TradeLog` não tinha
campo de multiplicador, logo o invariante era inverificável sem quebrar a regra de dependência
de D003.

**Decisão.** `multiplier` vira campo obrigatório e sem padrão de `TradeLog`, preenchido pelo
adaptador. A verificação de coerência acontece na fronteira, onde symbology está disponível,
conforme `04`. Identidade e tolerância ficam escritas em `01`. Violação levanta
`TradeIntegrityError` com o resíduo na mensagem. `fees` passa a ser magnitude não negativa,
eliminando a ambiguidade de sinal da redação anterior.

**Alternativas descartadas.** Deixar a verificação em `core` e importar symbology, descartada
por violar D003. Assumir multiplicador igual a 1 quando ausente, descartada porque erra o P&L
de futuros por ordens de grandeza sem levantar erro, que é o pior modo de falha possível.
Manter `fees` com sinal livre, descartada porque duas convenções de sinal coexistindo em um
campo produzem erro silencioso na identidade.

**Consequência.** Todo adaptador novo precisa resolver symbology antes de emitir `TradeLog`.
Custo real de implementação em cada adaptador, em troca de um invariante que de fato pega o
erro mais comum de importação de log de futuros.

---

## D008. Risco vira versão própria e o roadmap é renumerado

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `core/risk.py` estava em `01` e especificado em `02` seção 5, mas não tinha
versão atribuída em `05`. VaR, ES, risco de ruína, tempo até barreira e Kelly ajustado por
incerteza ficaram órfãos entre a v0.2 e a v0.3 antigas. A v0.7 antiga, mesa proprietária,
depende diretamente da lógica de barreira absorvente que mora em risco.

**Decisão.** Risco vira v0.3 com critério de pronto próprio. As versões seguintes deslizam:
sobreajuste vai para v0.4, regimes para v0.5, relatório e CLI para v0.6, adaptadores para
v0.7, mesa proprietária para v0.8. Veredito ganha v0.9, que também estava sem versão.
Fica registrada em `05` a regra de granularidade: uma versão entrega um módulo de `core`.

**Alternativas descartadas.** Dobrar risco dentro da v0.2, descartada porque produziria versão
de dois módulos e deixaria a barreira absorvente sem critério de pronto próprio, exatamente o
defeito que gerou esta decisão. Deixar risco para depois de sobreajuste, descartada porque
inverte a dependência com mesa proprietária.

**Consequência.** Renumeração de cinco versões. Custo zero em código, porque nada foi
construído, e custo de uma entrada de registro, D009, porque D005 ancorava prazo em número de
versão. Lição registrada: prazo ancorado em rótulo de versão quebra sob renumeração.

---

## D009. Nome do pacote decidido por evento, não por versão

**Data.** 2026-08-04
**Status.** aceita
**Substitui.** D005

**Contexto.** D005 fixava a decisão do nome para a v0.5. A renumeração de D008 moveu o que
v0.5 significa, o que revela que o prazo estava mal ancorado.

**Decisão.** Manter `qvalid` como nome de trabalho. A verificação de disponibilidade no PyPI e a
fixação definitiva do nome acontecem como critério de pronto da v1.0, que é o momento em que o
nome passa a ser público e portanto caro de trocar. Até lá o nome não aparece hardcoded fora
do `pyproject.toml` e dos imports.

**Alternativas descartadas.** Registrar o nome no PyPI imediatamente para reservar, descartada
porque reservar nome de pacote que ainda não existe é ruído no índice público e não custa nada
adiar.

**Consequência.** Uma verificação a mais no critério de pronto da v1.0. Nenhum prazo do
projeto volta a ser ancorado em número de versão.

---

## D010. A razão de diluição tem duas formas, e `02` passa a declarar qual

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** A seção 1.6 de `02` escrevia a diluição por períodos vazios como
`1 / sqrt(1 + (1 - p) * s^2)`. O teste de recuperação sob dado sintético, item 4 de `04`,
reprovou essa forma. A derivação correta a partir de `media_grade = p * mu_a` e
`var_grade = p * sigma_a^2 + p * (1 - p) * mu_a^2` dá `sqrt(p) / sqrt(1 + (1 - p) * s^2)`
para a comparação por período. O fator `sqrt(p)` só cancela quando cada série é anualizada
pela própria taxa de chegada, ou seja, quando a série de ativos é escalada por `sqrt(p * q)`
em vez de `sqrt(q)`. A especificação não declarava qual das duas comparações era o critério
de aceitação, logo a fórmula não estava errada por acaso: estava subespecificada.

**Decisão.** Implementar as duas como funções separadas, `dilution_ratio_per_period` e
`dilution_ratio_annualised`, com um teste que garante que diferem exatamente por `sqrt(p)`.
A seção 1.6 de `02` passa a declarar que o critério de aceitação usa a comparação por período,
que é a que o código calcula.

**Alternativas descartadas.** Corrigir a fórmula e manter uma só. Descartada porque a forma
anualizada é a que o praticante aplica mentalmente ao ler um Sharpe de série ativa, e é
exatamente onde o erro de leitura acontece. Ter as duas nomeadas torna a diferença
verificável em vez de tácita.

**Consequência.** A forma fechada esteve errada por duas rodadas e passou por revisão sem ser
pega. Quem pegou foi o teste de recuperação sob dado sintético, não a cobertura, que já estava
acima da meta. Fica registrado como argumento de que o item 4 de `04` é o que garante
correção, e os outros três garantem outra coisa.

---

## D011. A grade é aparada ao log, e `periods_per_year` tem origem declarada por degrau

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 1.1 define a escada mas não diz sobre que intervalo a grade é
construída nem de onde sai `periods_per_year` em cada degrau. As duas lacunas mudam número.
Se a grade herdasse a extensão do calendário materializado, `active_fraction` e `n_periods`
passariam a depender de uma escolha arbitrária da camada de adaptadores: o mesmo log julgado
contra um calendário de dez anos e contra um de um ano receberia vereditos diferentes.

**Decisão.** Três partes.

1. A grade vai do primeiro período que contém trade ao último, inclusive, com períodos vazios
   carregados como zero. Toda quantidade reportada passa a ser função apenas do log.
2. `periods_per_year` na grade diária vem de `TradingCalendar.sessions_per_year`, medido sobre
   o vão inteiro do calendário e não sobre a grade aparada, porque a taxa é propriedade do
   venue e não de quando a estratégia operou. Nas grades semanal e mensal a taxa é propriedade
   do calendário gregoriano, `365.25 / 7` e `12` exatos, logo nada é estimado. Semana de
   feriado continua sendo semana de capital alocado.
3. A condição de posse é a duração mediana em nanossegundos sobre o comprimento mediano do
   período em nanossegundos, comparada a `MAX_HOLDING_TO_PERIOD` como a razão que a constante
   declara ser.

**Alternativas descartadas.** Grade com a extensão do calendário, descartada pelo motivo do
contexto. Contar quantos períodos da grade cada intervalo de posse atravessa, descartada
porque um trade overnight de dezessete horas atravessa dois períodos diários e dura menos de
um: a forma por contagem expulsaria da grade diária quase toda estratégia que carrega posição
para a abertura, por artefato de fronteira de bucket. A forma em nanossegundos também
continua definida quando `entry_ts` precede o calendário, que é o caso de posição herdada.

**Consequência.** Os dois primeiros e o último período da grade são ativos por construção, o
que eleva `active_fraction` em amostra curta por no máximo `2 / n_periods`, efeito que se
extingue bem antes de `MIN_PERIODS`. O relatório passa a receber o diagnóstico dos três
degraus, não só do vencedor, porque engrossar a grade não é monótono nas três condições ao
mesmo tempo: sobe `active_fraction` e baixa a razão de posse, mas corta `n_periods` por volta
de cinco, e uma amostra pode ser inviável em todos os degraus por motivos diferentes em cada.

---

## D012. Calendário que não cobre o log levanta exceção própria

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `TradeLog` e `TradingCalendar` são contratos independentes, validados
separadamente na fronteira. Nada impedia que descrevessem intervalos de tempo diferentes.
Sob a regra de atribuição por `exit_ts`, um trade que sai depois do último fechamento do
calendário seria grudado no período de fronteira, fabricando um pico exatamente onde a
amostra é menos confiável. `GridSparsityError` não serve: ela afirma que a escada é inviável
sobre um calendário que cobre a amostra, que é coisa diferente.

**Decisão.** Adicionar `CalendarCoverageError` a `exceptions.py` e à tabela de `04`. Ela
deriva de `ThresholdViolation`, logo carrega o instante observado e o limiar violado. A
checagem roda uma vez em `period_returns`, antes de qualquer degrau, e vale para os três.

**Alternativas descartadas.** Grampear os trades fora do intervalo no período de fronteira,
descartada pelo motivo do contexto. Reutilizar `SchemaError`, descartada porque `SchemaError`
é sobre a forma de um contrato e aqui os dois contratos estão bem formados; o que está errado
é a relação entre eles.

**Consequência.** Todo adaptador que emitir calendário precisa cobrir o log que acompanha,
com folga de pelo menos um espaçamento mediano de sessão na ponta inicial. Em troca, o modo
de falha mais silencioso da projeção deixa de existir.

---

## D013. Recuperação de `eta(q)` é julgada por viés limitado, não por erro amostral

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 1.6 exigia que o fator por variância de longo prazo recuperasse
`eta(q)` sob AR(1) "dentro do erro amostral". Medido: com coeficiente 0,4 e 40 réplicas, a
razão `sigma/sigma_LR` tem viés de mais 6,7 por cento em `T = 500`, mais 3,3 por cento em
`T = 2000` e mais 1,9 por cento em `T = 8000`. Em `T = 2000` isso é cerca de sete erros
padrão de Monte Carlo. O critério como escrito é inatingível, e a única forma de fazê lo
passar seria afrouxar a tolerância, que é exatamente a proibição de `04`.

**Decisão.** O critério de aceitação passa a ser triplo e verificável: direção correta em
relação a `sqrt(q)` conforme o sinal do coeficiente, viés abaixo de 10 por cento, e viés
monotonicamente decrescente numa escada de `T`. O viés medido e sua taxa de decaimento ficam
escritos na docstring de `bartlett_long_run_variance`, com os números, não como adjetivo.

**Alternativas descartadas.** Afrouxar a tolerância até passar, proibida por `04`. Trocar por
um estimador pré branqueado de Andrews e Monahan (1992), que reduz o viés, descartada por
agora: acrescenta um modelo AR auxiliar e uma regra de truncamento de raiz, ou seja, mais
superfície de escolha não declarada, num módulo cujo papel na v0.1 é ser descritivo. Fica
registrada como candidata para quando `overfit` depender de `p` valores HAC.

**Consequência.** O Sharpe corrigido por HAC é sistematicamente mais próximo do ingênuo do
que a verdade justifica, quando há autocorrelação positiva. A direção é conservadora no
sentido de que a correção é subestimada, e isso agora está declarado em vez de descoberto por
quem for comparar com uma implementação de referência.

---

## D014. A convenção de graus de liberdade é declarada e as duas formas são reportadas

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Os dois casos fechados de `02` seção 1.6 exigem convenções incompatíveis. A
identidade de diluição só é exata sob variância populacional, denominador `T`. O caso
degenerado de trade único só dá exatamente `1/sqrt(T)` sob variância amostral, denominador
`T - 1`; sob `T` ele dá `1/sqrt(T - 1)`. A especificação não declarava nenhuma das duas, e
qualquer escolha única faria um dos dois critérios de aceitação falhar por definição.

**Decisão.** Estimativa pontual reportada sob `T - 1`, que é a convenção do praticante e a que
`02` 1.6 assume no caso degenerado. Método delta e verificação contra Mertens sob `T`, porque
as derivadas são tomadas em relação a `E[r]` e `E[r^2]` e porque a redução a Mertens é
identidade exata nessa convenção. `SharpeEstimate` carrega as duas, `per_period_sample` e
`per_period_population`.

**Alternativas descartadas.** Usar `T - 1` em tudo, que quebraria a exatidão da redução a
Mertens e transformaria o teste de consistência em teste estatístico. Usar `T` em tudo, que
exigiria reescrever o caso degenerado de `02` para `1/sqrt(T-1)`, número que ninguém reconhece.

**Consequência.** Estimativa pontual e erro padrão descrevem estimadores que diferem por
`sqrt(T/(T-1))`, 0,85 por cento em `MIN_PERIODS` e caindo como `1/(2T)`. A diferença é de
ordem menor que o próprio erro padrão em qualquer amostra utilizável, e agora está escrita.

---

## D015. Mínimos declarados são aviso, e o parâmetro de seleção de defasagem não é a largura de banda

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Duas ambiguidades apareceram juntas ao implementar o HAC. Primeira: `02` seção
1.4 manda "reportar métricas com aviso" abaixo de `MIN_TRADES` e `MIN_PERIODS`, mas a tabela
de exceções de `04` lista `InsufficientSampleError` para "menos trades ou períodos que o
mínimo declarado". As duas instruções se contradizem. Segunda: a derivação de `MIN_PERIODS`
chama `4 * (T/100)^(2/9)` de largura de banda. Essa expressão é o parâmetro de seleção de
defasagem `n` de Newey e West (1994), não a largura de banda `L`, que sai de
`L = floor(1.1447 * (alpha * T)^(1/3))` e é tipicamente várias vezes maior.

**Decisão.** `MIN_TRADES` e `MIN_PERIODS` produzem aviso carregado no objeto de resultado, e a
supressão de seções é decisão da camada de relatório, não da função de cálculo.
`InsufficientSampleError` fica reservada para quando a estatística não pode ser formada de
jeito nenhum, como menos de dois períodos, onde não existe dispersão. `02` 1.4 passa a chamar
`n` pelo nome certo e a derivação continua válida, porque a exigência de observações por
defasagem se aplica a `n` e em `T = 60` dá `n = 3`, razão 20.

**Alternativas descartadas.** Levantar exceção nos mínimos declarados, descartada porque
apagaria justamente o caso que o painel de evidência de `02` seção 7 precisa mostrar como
presente e não confiável, em vez de ausente. Ausência de evidência e evidência fraca são
coisas diferentes e o veredito depende de distinguí las.

**Consequência.** Toda função de cálculo passa a devolver `warnings` no próprio resultado.
`ValidationReport` herda esses avisos, e a regra adicional de `02` seção 7, de que teste
suprimido nunca entra no ordenamento como aprovado, ganha o insumo de que precisa.

---

## D016. O mapeamento de colunas é arquivo versionado, não argumento de chamada

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** O importador de CSV precisa saber qual coluna é qual. Três formas eram
razoáveis: esquema canônico fixo, argumentos nomeados na chamada, ou arquivo declarativo. A
questão que decide não é ergonomia, é reprodutibilidade. Qual coluna foi lida como preço de
saída, qual fuso foi assumido para timestamp ingênuo e qual convenção de sinal a coluna de
custo usava mudam o resultado, e `01` exige que o relatório seja reproduzível.

**Decisão.** Mapeamento declarativo em YAML, validado por pydantic com `extra="forbid"`, uma
entrada por fonte. Mapa de symbology idem, com símbolo canônico, multiplicador, tick mínimo,
moeda, calendário e identificador por fonte. Pydantic aqui e não em `contracts.py` porque
`04` reserva pydantic para objetos escalares de configuração, que é exatamente o caso: são
pequenos, lidos uma vez, e mensagem de erro por campo é o que um YAML mal configurado precisa.

**Alternativas descartadas.** Esquema canônico fixo, descartada porque exige renomear coluna
na mão a cada importação e joga fora o trabalho que TradingView e NinjaTrader vão precisar na
v0.7. Argumentos nomeados na chamada, descartada porque a procedência do mapeamento não fica
versionada nem entra no `ValidationReport`, o que quebra reprodutibilidade por outra pessoa.
Detecção automática de coluna por heurística de nome, descartada pelo mesmo motivo de D004:
seria fabricar o insumo que determina o resultado.

**Consequência.** Toda importação exige dois arquivos além do CSV. Em troca, o par de arquivos
é a procedência, e `extra="forbid"` faz um erro de digitação em chave de YAML falhar em vez de
ser ignorado em silêncio. O importador não deduplica, não preenche lacuna e não descarta linha:
toda linha do arquivo vira trade ou a importação falha, porque log parcialmente importado é
pior do que log recusado, já que as estatísticas resultantes parecem normais.

---

## D017. Sinal de custo é detectável, líquido contra bruto não é

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Ao escrever o teste do ponto cego de custo, a hipótese era que declarar
convenção de sinal errada passasse despercebida sob o piso de uma tick. O teste reprovou a
hipótese. Declarar `NEGATED` num arquivo cujos custos já são magnitudes produz custo negativo,
e o invariante de não negatividade de `01` reprova o arquivo inteiro antes de a identidade de
coerência sequer rodar. As duas direções do erro de sinal são pegas, e sem depender de
tolerância nenhuma.

O que de fato não é detectável é outra coisa, que estava confundida com essa: se a coluna de
P&L do arquivo é líquida ou bruta de custos. Declarar líquida sobre coluna bruta deixa resíduo
igual a exatamente um custo por trade. Em ES, um round turn de 4,20 fica muito abaixo da
tolerância absoluta de uma tick, 12,50 por contrato. A importação passa e todo trade fica
superestimado pelo custo integral de operar, que é precisamente o erro que transforma
estratégia perdedora em vencedora no papel.

**Decisão.** Separar as duas em enumerações distintas. `FeeConvention` continua obrigatória,
mas documentada como declarada por clareza e não por poder se esconder. `PnlConvention`, com
`NET` e `GROSS`, é obrigatória e sem padrão porque é a única que a identidade não verifica.
O teste que antes afirmava o ponto cego no lugar errado foi reescrito para afirmá lo no lugar
certo, e mede o piso: o erro só aparece quando o custo excede a resolução de preço do
instrumento.

**Alternativas descartadas.** Inferir a convenção comparando a coluna de P&L com a identidade
e escolhendo a que der resíduo menor. Descartada porque escolheria sempre a que passa, ou seja,
transformaria o teste em tautologia e apagaria o único sinal de erro disponível.

**Consequência.** O ponto cego declarado na rodada anterior estava localizado no lugar errado.
A documentação em `validate_trade_log` continua correta quanto ao piso de uma tick, mas o
exemplo que ela dava, custo dobrado, é na verdade detectável. Fica registrado como caso de
hipótese plausível reprovada por teste, que é o mesmo padrão de D010.

---

## D018. O comprimento de bloco é validado contra o minimizador do EQM, não contra o paper

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** A regra de seleção automática de comprimento de bloco de Politis e White (2004)
é uma sequência de constantes tabuladas e janelas. Transcrever a fórmula e afirmar que a
transcrição está certa não é verificação: é a mesma afirmação duas vezes. Sem acesso ao paper
durante a implementação, o risco de erro silencioso de constante era real.

**Decisão.** Validar contra a quantidade que a regra existe para otimizar. Sob AR(1) com
coeficiente 0,5 e `n = 1000`, a variância de longo prazo verdadeira é `1/(1-rho)^2 = 4`.
Varrendo `b` por força bruta sobre o estimador exato de variância do bootstrap estacionário
de Politis e Romano,

    w(k) = ((n-|k|)/n) * (1-1/b)^|k| + (|k|/n) * (1-1/b)^(n-|k|)

o argmin do erro quadrático médio observado é `b = 10`, e a regra plug in devolve 10,56 nos
mesmos caminhos. Duas propriedades adicionais foram medidas e viraram teste: monotonicidade em
`rho`, com 1,39, 5,44, 10,86, 17,56 e 32,49 para `rho` de 0 a 0,8 em `n = 2000`; e escala
`n^(1/3)`, com razões observadas de 1,251, 1,307, 1,315 e 1,297 entre tamanhos que dobram,
contra 1,260 previsto.

O estimador exato de referência está escrito no arquivo de teste, não importado do módulo.
Teste que reutiliza a implementação que está checando prova apenas autoconsistência.

**Alternativas descartadas.** Usar `arch` ou outra biblioteca com a rotina pronta, descartada
porque a dependência traz o mesmo problema de verificação um nível acima e porque o objetivo
do repositório inclui demonstrar a implementação. Assumir a transcrição correta, descartada
pelo motivo do contexto.

**Consequência.** Se algum dia a constante estiver errada, o teste de força bruta pega. Ele é
lento, cerca de um segundo, e é o único teste do módulo que justifica esse custo.

---

## D019. `b = 1` é o bootstrap i.i.d., e não existe segunda função

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 2.1 exige comparar o bootstrap em blocos com o bootstrap i.i.d. e
mostrar que o primeiro preserva melhor a autocorrelação de primeira ordem. Escrever duas
funções tornaria a comparação vulnerável a medir diferença de implementação em vez de
diferença de método.

**Decisão.** Com comprimento de bloco esperado igual a 1, a probabilidade de reinício é 1 e
todo passo sorteia âncora nova, o que é exatamente reamostragem com reposição. Uma função só,
e o teste de comparação passa `block_length=1.0` contra o `b` estimado no mesmo caminho de
código. Medido: sob AR(1) com `rho = 0,6` e autocorrelação observada de 0,642, o bootstrap em
blocos devolve 0,591 e o i.i.d. devolve 0,003.

**Consequência.** Não existe `iid_bootstrap` na superfície pública. Quem quiser reamostragem
i.i.d. passa `block_length=1.0` e o relatório registra que foi escolha, não estimativa.

---

## D020. Caminhos são níveis de equity, construídos pela mesma regra do caminho observado

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `EquityPaths` podia guardar retornos reamostrados ou níveis de equity. Drawdown,
barreira absorvente e risco de ruína, que são v0.3, só existem sobre nível.

**Decisão.** Níveis absolutos em moeda de conta, `(n_paths, n_steps + 1)`, coluna zero igual ao
capital inicial, construídos pela mesma regra de base que `metrics.equity_curve`, aditiva sob
`FIXED_INITIAL` e multiplicativa sob `CURRENT_EQUITY`. Um teste compara caminho simulado com
caminho observado passo a passo para garantir que a regra é literalmente a mesma.

O contrato de `01` **não** ganha campo. O comprimento de bloco viaja em `BootstrapResult`, ao
lado dos caminhos, seguindo o padrão já usado por `GridSelection` e `ImportResult`: o contrato
fica como `01` define e a procedência anda junto.

**Alternativas descartadas.** Guardar retornos e converter na v0.3, descartada porque duas
regras de construção, uma para o observado e outra para o simulado, tornariam o drawdown
observado incomparável com a distribuição da qual ele deveria ser um quantil. Acrescentar
`block_length` a `EquityPaths`, descartada por churn de contrato sem ganho.

**Consequência.** Sob `CURRENT_EQUITY` um caminho reamostrado pode compor para equity não
positiva, porque a reamostragem concatena perdas que o histórico nunca mostrou seguidas. Isso
não é erro: é ruína, é o evento que a v0.3 mede, e o caminho é mantido com a contagem de
caminhos arruinados no aviso.

**Correção posterior.** A afirmação desta entrada de que a base é recuperável dos caminhos por
serem níveis absolutos está errada. Ver D023.

---

## D021. Amostra abaixo de `MIN_BLOCK_SAMPLE_RATIO` observações é recusada, e o teto de banda é o que torna a recusa tipada

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** O caso degenerado de amostra mínima quebrou com `IndexError`. Com `n = 2`, a
largura de banda calculada é 7 e o vetor de autocovariâncias tem 2 elementos. O erro aparecia
antes da guarda de razão de `02` 2.1, então o usuário recebia um erro de índice do NumPy em
vez da mensagem que diz o que fazer.

Vale notar a aritmética: a guarda recusa `b > n / MIN_BLOCK_SAMPLE_RATIO`, e o menor `b`
possível é 1, logo toda amostra com menos de 10 observações é recusada por construção,
qualquer que seja a estrutura de dependência. Isso é correto e agora está escrito.

**Decisão.** Limitar a largura de banda a `n - 1`, o número de defasagens que existem. A
recusa passa a chegar como `InsufficientSampleError` com valor observado e limiar, conforme
`04`. Um teste parametrizado varre `n` de 2 a 21 e exige que toda entrada devolva resposta
tipada ou erro tipado, nunca exceção do NumPy.

**Consequência.** Nenhuma mudança de comportamento em amostra utilizável, porque `n - 1` só
morde quando `n` é minúsculo. Em troca, a fronteira inferior do módulo deixou de ter um modo
de falha não tipado. Foi um teste de caso degenerado que achou, não revisão.

---

## D022. Barreira monitorada em tempo discreto exige a forma fechada corrigida

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 5 exigia que a probabilidade de ruína simulada batesse com "a solução
fechada" dentro do erro de Monte Carlo, sem dizer qual. A solução fechada óbvia, pelo princípio
da reflexão, vale para barreira monitorada continuamente. O motor verifica a barreira uma vez
por período, e um caminho pode furá la e voltar entre dois passos sem ser observado. Medido com
300 mil caminhos, a forma contínua fica 10 a 43 erros padrão de Monte Carlo longe do simulado,
ou seja, o critério como escrito era inatingível.

**Decisão.** O critério passa a ser contra a forma corrigida por continuidade de Broadie,
Glasserman e Kou (1997), que desloca a barreira por `beta * sigma * sqrt(dt)` com
`beta = -zeta(1/2)/sqrt(2*pi) = 0,5826`. Contra ela o simulado bate dentro de 1,3 erro padrão
nos quatro casos testados. Ambas as formas ficam expostas na biblioteca, em
`brownian_ruin_probability`, com o argumento `continuity_corrected`, porque a diferença entre
as duas é diagnóstico e não erro.

Descoberta paralela, obtida por medição e não presente no paper de Magdon-Ismail et al. (2004):
para o drawdown máximo esperado a mesma correção entra **em dobro**, porque drawdown é a
distância entre o máximo corrente e o nível atual, e as duas fronteiras são monitoradas. Com o
fator dois, as razões entre simulado e forma fechada ficam entre 0,996 e 1,0003 de `T = 60` a
`T = 4000`. Sem ele, 0,876 a 0,986.

**Alternativas descartadas.** Afrouxar a tolerância até a forma ingênua passar, proibida por
`04` e, neste caso, exigiria tolerância de 40 erros padrão. Monitorar a barreira em subpassos
interpolados, descartada porque a conta real é marcada uma vez por dia e o estimador deve
modelar a conta, não o processo idealizado.

**Consequência.** É a quarta vez que uma forma fechada da especificação foi reprovada por
medição, depois de D010, D013 e D017. O padrão comum às quatro: a especificação nomeava um
objeto matemático sem declarar a convenção sob a qual ele vale.

---

## D023. Absorção na barreira não é conservadora, e a base não é recuperável dos níveis

**Data.** 2026-08-04
**Status.** aceita
**Corrige.** D020

**Contexto.** Dois enganos apareceram ao construir `core/risk.py`, e os dois eram meus.

Primeiro, D020 afirmou que a base é recuperável dos caminhos por serem níveis absolutos. É
falso. Dados os níveis, o retorno por passo é `diff / L_0` sob `FIXED_INITIAL` e `diff / L_{t-1}`
sob `CURRENT_EQUITY`, e nada nos níveis distingue qual gerou a série. Uma função que adivinhasse
produziria silenciosamente o segundo momento errado, que é o insumo da fração de Kelly.

Segundo, escrevi um teste afirmando que absorver na barreira só pode reduzir o retorno
terminal. O teste reprovou. Absorção congela o caminho na barreira, então ela baixa o terminal
de quem furou e se recuperou, mas **sobe** o terminal de quem terminou abaixo da barreira,
porque tal conta teria sido encerrada ali.

**Decisão.** A base entra como argumento tipado em toda função que precisa de retorno por
passo, e entra no relatório. A absorção permanece opcional e nunca aplicada por padrão, e a
docstring passa a declarar que ES absorvido **não** é o número conservador, somando a isso a
hipótese de execução exatamente na barreira, sem gap e sem derrapagem. Um teste fixa a direção
dos dois efeitos em vez de deixá la para a intuição.

**Consequência.** `EquityPaths` continua sem campo de base, mas a documentação de D020 fica
corrigida em vez de apagada. Quem ler os caminhos sem a base declarada não tem como calcular
retorno por passo, e isso agora é explícito na assinatura.

---

## D024. `TrialMatrix` torna estrutural a pré condição de `02` seção 3

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 3 abre exigindo que todo Sharpe da seção venha de `PeriodReturns` na
mesma grade e com o mesmo `periods_per_year` para todas as configurações comparadas. Comparar
Sharpes de grades diferentes é erro de unidade. `05` lista definir o formato de entrada da
matriz de tentativas como pré requisito da implementação.

**Decisão.** Contrato `TrialMatrix` em `contracts.py`, guardando a matriz
`(n_periods, n_configs)` de retornos mais **uma única** declaração de `period`,
`periods_per_year`, `calendar_id`, `basis` e `initial_capital`. Não existe estado
representável em que duas configurações da mesma matriz vivam em grades diferentes, logo a pré
condição deixa de depender de checagem. O método `column` extrai uma configuração como
`PeriodReturns` carregando a grade da matriz.

**Alternativas descartadas.** Uma sequência de `PeriodReturns` verificada por igualdade de
grade, descartada porque a garantia passaria a depender de alguém chamar a verificação. É o
mesmo raciocínio de D006: a proibição vira estrutura em vez de disciplina.

**Consequência.** `01` ganha um contrato. Todo adaptador que quiser alimentar a seção 3 precisa
emitir a matriz inteira, não só a vencedora, o que é exatamente o que D004 já exigia por outro
caminho.

---

## D025. O que a medição do módulo de sobreajuste mostrou, e três erros meus

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `05` chama o par de testes desta versão de o mais importante do projeto. Medir
antes de fixar limiar era obrigatório, e a medição encontrou três coisas que eu tinha errado.

**Erro 1, sinal do recentramento do SPA.** Escrevi
`max_k max(sqrt(n)(dbar*_k - mu_k)/omega_k, 0)`. A forma correta é
`max_k max(sqrt(n)(dbar*_k - dbar_k + mu_k)/omega_k, 0)`, ou seja, subtrair a média amostral e
**somar** a média estimada sob a nula. Com o sinal errado o teste tinha poder zero e ainda
assim parecia funcionar. Foi a medição de tamanho e poder que pegou, não a revisão.

**Erro 2, teto do logit do PBO.** Assertei que a mediana do logit sob edge forte passa de 4,0.
É impossível: o logit é limitado por `log(N)`, porque o melhor posto relativo é `N/(N+1)`. Com
cinquenta configurações o teto é 3,912 e as quatro sementes batiam exatamente nele. A
consequência que ficou registrada na docstring importa mais que o teste: a magnitude do logit
**não** é comparável entre universos de tamanhos diferentes.

**Erro 3, dispersão nula.** Repeti em `overfit` o mesmo bug que D014 já tinha corrigido em
`metrics`: testar `dispersion <= 0.0` em vez do piso numérico. Série constante de valor não
representável em binário passava direto. Agora usa `dispersion_is_negligible`.

**Decisão.** Os limiares dos testes vêm da medição sobre quatro sementes, não de uma execução.
Sob `T = 1000`, `N = 50`, `S = 16`:

| efeito, Sharpe por período | PBO médio | faixa        | DSR médio | faixa        |
|----------------------------|-----------|--------------|-----------|--------------|
| 0,00                       | 0,475     | 0,26 a 0,58  | 0,483     | 0,27 a 0,65  |
| 0,12                       | 0,166     | 0,07 a 0,30  | 0,825     | 0,54 a 0,97  |
| 0,25                       | 0,000     | 0,00 a 0,001 | 1,000     | 0,999 a 1,00 |

**Achado que vale mais que os limiares.** Na linha do meio os três instrumentos **discordam**.
A validação cruzada e o teste de superioridade encontram o edge; o Sharpe deflacionado varia de
0,54 a 0,97 e é o menos decisivo. Não é defeito: a deflação responde pergunta mais dura, se o
**nível** do Sharpe sobrevive ao máximo esperado de cinquenta tentativas, e nesse tamanho de
efeito ele mal sobrevive. É a justificativa empírica do painel de evidência de `02` seção 7:
colapsar três instrumentos que discordam numa nota única apagaria exatamente a informação que
interessa.

**Consequência.** Ficou escrito na docstring que o PBO sob ruído tem faixa de 0,26 a 0,58 mesmo
com 12870 combinações, logo uma execução única não caracteriza o estimador. E que o SPA
estudentizado rejeita 9 por cento sob nominal 5 em `n = 500`, então exigir zero falso positivo
em quatro sementes contradiria a própria medição.

---

## D026. O rótulo de regime usa janela terminando no período anterior

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 4 exige janela estritamente passada mas não diz se o próprio período
entra. Incluí lo não é look ahead no sentido estrito, já que ambos são conhecidos ao fim do
período. O problema é outro: cria correlação mecânica. Para uma estratégia comprada, um período
de alta seria simultaneamente rotulado como tendência de alta e creditado com lucro, e a
atribuição por estado passaria a medir a direção da posição em vez do regime.

**Decisão.** A janela termina em `t - 1`, logo o rótulo é conhecido **antes** de o período
começar. Aquecimento explícito de `estados_por_eixo * MIN_STATE_OBS`, isto é 60 numa grade 3
por 3, durante o qual o rótulo é `UNDEFINED_STATE` e não um balde forçado. Períodos indefinidos
saem da atribuição com contagem e P&L reportados, de modo que a soma fecha.

**Alternativas descartadas.** Incluir o período corrente, pelo motivo do contexto. Forçar os
períodos de aquecimento no balde do meio, descartada porque injetaria ruído indistinguível de
estado real.

**Consequência.** Rótulos mais defasados, e um giro de regime dentro do período é atribuído ao
estado antigo. Em troca, a atribuição responde a pergunta que interessa. Verificado por dois
testes exatos: rotular um prefixo reproduz o prefixo dos rótulos, e perturbar a série depois de
`k` não altera nenhum rótulo antes de `k`. O segundo é estritamente mais forte, e um teste
adicional mostra o estimador proibido, quantil sobre a amostra inteira, reprovando nele.

---

## D027. Igualdade de médias entre regimes vai por Welch, não pelo ANOVA padrão

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 4 pede teste de igualdade de média entre estados. O ANOVA padrão supõe
variâncias iguais, e nesta grade essa hipótese é falsa **por construção**: um dos eixos é
volatilidade, logo os estados diferem em variância por desenho. As contagens também diferem, e
tipicamente o estado de maior variância é o mais populoso.

**Decisão.** Welch (1951), implementado no módulo. Medido com médias verdadeiramente iguais,
desvios 1, 3 e 9, 2000 réplicas:

| cenário                                | Welch  | `f_oneway` | nominal |
|----------------------------------------|--------|------------|---------|
| contagens iguais                       | 0,0580 | 0,0870     | 0,05    |
| contagens 40, 100, 300                 | 0,0410 | **0,0005** | 0,05    |

A segunda linha é o argumento inteiro. Com o `n` maior no estado de maior variância, que é o
arranjo típico aqui, o teste de variância igual praticamente nunca rejeita, e reportaria
"nenhuma diferença entre regimes" por motivo que nada tem a ver com regimes.

**Consequência.** Uma implementação a mais para manter, contra uma dependência de `f_oneway`
que estaria errada exatamente no caso de uso do projeto.

---

## D028. Reamostragem markoviana mora em `regimes.py`, e `02` precisa mover o cabeçalho

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 2.2 aparece sob `core/resample.py` e `05` v0.5 lista a reamostragem
markoviana no escopo de `core/regimes.py`. Os dois documentos discordam.

**Decisão.** `regimes.py`. A cadeia não compartilha nada com o bootstrap estacionário além da
construção de `EquityPaths`, e manter rótulos, matriz de transição e cadeia no mesmo módulo
evita que `resample` passe a depender de `RegimeLabels`. `02` precisa mover o cabeçalho da
seção 2.2.

**Consequência.** `core/resample.py` continua sem conhecer regimes, o que preserva a
possibilidade de reamostrar sem rotular. O identificador de método em `EquityPaths` distingue
os dois esquemas, e ganha o sufixo `+collapsed` quando a grade foi colapsada.

---

## D029. `ValidationReport` mora em `report`, e a raiz de composição é `pipeline.py`

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `01` lista `ValidationReport` entre os contratos canônicos, o que sugere
`contracts.py`. Não pode: ele agrega `PeriodMetrics`, `PboResult`, `RegimeAttribution` e
outros, todos de `core`, e `core` importa `contracts`. Colocá lo lá fecharia um ciclo.

Além disso, a orquestração dos dez passos de `01` precisa de `adapters`, `core` e `report` ao
mesmo tempo, e nenhuma das três camadas pode conhecer as outras duas.

**Decisão.** `ValidationReport` vai para `report/model.py`. A dependência aponta para dentro,
`report` importa `core` importa `contracts`, exatamente como `01` exige. A orquestração vai
para `qvalid/pipeline.py`, a raiz de composição, único módulo autorizado a importar as três
camadas. `cli.py` fica fino: analisa argumentos, chama o pipeline, escreve arquivo, traduz erro
tipado em código de saída.

**Consequência.** `01` passa a registrar que o contrato que agrega resultados pertence à camada
que os consome, e que existe uma raiz de composição. A regra de dependência continua intacta e
o pipeline é testável sem subprocesso.

---

## D030. Gráficos em SVG escrito à mão, para o determinismo ser por construção

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** O critério de pronto da v0.6 é duro: duas execuções com a mesma seed produzem
relatórios idênticos byte a byte, exceto o timestamp. Um SVG do matplotlib embute um elemento
`dc:date` nos metadados, então o arquivo mudaria a cada execução sem que nada no código
parecesse errado. Some se que matplotlib **não** está em `pyproject.toml`, e acrescentar uma
pilha de plotagem por causa de três gráficos é caro.

**Decisão.** Emitir a marcação em `report/svg.py`, com primitivas para linha, histograma e
barras. Toda coordenada passa por um formatador que arredonda a três casas e normaliza `-0.0`
para `0.0`, porque os dois são iguais como número e diferentes como texto.

**Alternativas descartadas.** Configurar matplotlib para suprimir o metadado, descartada porque
a propriedade passaria a depender de configuração cuidadosa e de uma versão futura não
acrescentar outro carimbo. Omitir gráficos, descartada porque `01` pede relatório autocontido
com gráficos embutidos.

**Consequência.** Os gráficos são feios comparados a matplotlib e o módulo não escala para nada
elaborado. Aceito: o JSON é a saída de referência, e uma interface depois da v1.0 desenha a
partir dele. Zero dependência nova, e o determinismo vale por construção em vez de por
disciplina.

---

## D031. Ausência de evidência é estado tipado, não campo vazio

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 7 proíbe que teste suprimido entre no veredito como se tivesse sido
aprovado, e exige que ausência de evidência apareça como ausência. Um relatório em que a seção
simplesmente não aparece produz exatamente a leitura errada: quem procura sinal de alerta e não
encontra nenhum conclui que não havia.

**Decisão.** Cada seção do painel é uma entrada que carrega **ou** resultado **ou** motivo de
ausência, e o tipo recusa ter nenhum dos dois ou ambos. Os motivos são enumerados:

- `RAN`, com o resultado;
- `SUPPRESSED`, condição de invalidez de `02` 1.4 atingida, com observado e limiar;
- `NOT_REQUESTED`, insumo que só o usuário pode dar não foi dado, que é o caso de D004;
- `FAILED`, erro tipado durante a execução, mantido como evidência em vez de derrubar a
  execução inteira.

Seção ausente do painel é diferente de seção presente com `NOT_REQUESTED`: a primeira é bug do
pipeline, a segunda é ausência declarada. `entry` levanta `KeyError` para uma e devolve a
evidência para a outra.

**Consequência.** O relatório mostra quantas seções não rodaram, com a frase de que teste
ausente não é teste aprovado, em HTML e em LaTeX. O veredito da v0.9 lê o mesmo painel e pode
recusar pontuar o que não rodou, que é a regra adicional de `02` seção 7.

---

## D032. A série de referência de regime alinha por timestamp, nunca por posição

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** O pipeline precisa ler a série de mercado de referência que define a grade de
regimes. A implementação óbvia lê a coluna de retornos e trunca para o número de períodos da
grade. Está errada: uma referência que começa uma sessão antes, ou que inclui um feriado que o
calendário do venue exclui, deslocaria **todos** os rótulos em um período.

`core/regimes.py` já recusa desalinhamento que consegue ver, comparando instantes de
fechamento, mas uma leitura posicional entregaria a ele uma série que **parece** alinhada.

**Decisão.** O arquivo de referência precisa carregar timestamps tz aware, e o alinhamento é
por igualdade exata de instante de fechamento contra a grade. Período faltando levanta
`SchemaError` dizendo quantos e qual o primeiro. Timestamp ingênuo é recusado, conforme `01`.

**Consequência.** Um arquivo de referência a mais para preparar. Em troca, o modo de falha mais
silencioso da camada de regimes deixa de existir. Foi encontrado ao montar o exemplo da v0.6,
não por revisão.

---

## D033. A rede fica atrás de um protocolo injetável, e é isso que compra a suíte offline

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Três exigências pareciam incompatíveis. `04` proíbe dependência de rede em teste.
`03` exige que todo download passe por cache com manifesto. `05` v0.7 pede prova de que o cache
evita o segundo download. Provar uma propriedade sobre downloads sem baixar nada parece
contraditório.

**Decisão.** O cache recebe um objeto que satisfaz um protocolo `Fetcher` e nunca importa
cliente HTTP. Chama no máximo uma vez por recorte, grava os bytes verbatim e acrescenta linha
ao manifesto. O teste passa um buscador que conta chamadas e afirma que o contador ficou em um.
A propriedade fica provada, e provada offline.

Dois detalhes que mudam o comportamento e ficam registrados. O arquivo bruto é nomeado pelo
hash da chave, não pelo símbolo, porque dois recortes do mesmo símbolo são dois arquivos e
nomear por símbolo obrigaria a inventar convenção de sufixo. E o manifesto registra **todo**
evento, inclusive requisição que encontrou o recorte presente, porque omitir os acertos faria o
log dizer que um recorte foi buscado uma vez quando foi usado quarenta.

**Alternativas descartadas.** Marcar os testes de rede para pular em CI, descartada porque um
teste que pula não é teste. Gravar respostas em cassete, descartada porque acrescenta
dependência e o cassete envelhece sem ninguém perceber.

**Consequência.** Todo buscador real vive em um módulo só, que é o único ponto do pacote que
toca rede. O cache verifica os arquivos contra o hash gravado, de modo que um arquivo bruto
editado à mão é detectado antes de contaminar um relatório.

---

## D034. Diferença de nome é arquivo, diferença de forma é código

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** D016 apostou que importadores novos seriam arquivos de mapeamento e não código
novo. A v0.7 testa a aposta contra duas plataformas reais. NinjaTrader exporta uma linha por
round turn e é mapeamento puro, como previsto. TradingView exporta **duas linhas por trade**,
uma de entrada e uma de saída ligadas por um número, o que é pareamento e não renomeação.

**Decisão.** A aposta de D016 vale para nome de coluna e não para forma de linha. Acrescentar
`row_layout` ao mapeamento, com `ONE_ROW_PER_TRADE` e `TWO_ROWS_PER_TRADE`, e no segundo caso
declarar a coluna de ligação, a coluna de perna e os marcadores de cada perna. O pareamento
vira um caminho de código genérico e as duas plataformas continuam sendo arquivos.

Sob `TWO_ROWS_PER_TRADE` os quatro campos de perna deixam de ser obrigatórios no mapeamento,
porque vêm do pareamento: exigi los seria exigir que o mapeamento aponte para algo que a fonte
não tem.

O lado vem da perna de **entrada**. A saída de uma compra é uma venda, e ler direção na saída
inverteria todo trade.

**Verificação.** O mesmo conjunto de trades, exportado nos dois formatos, produz `TradeLog`
idêntico campo a campo. É o teste que prova que as duas rotas concordam, e sem ele o mapeamento
declarativo estaria comprando menos do que afirma.

**Consequência.** Trade com número de pernas diferente de dois é recusado, e não reparado. Meio
trade pareado é trade faltando, e descartá lo mudaria toda estatística enquanto a importação
continuaria parecendo limpa.

**Limitação declarada.** Os mapeamentos de TradingView e NinjaTrader foram escritos sem um
export real à mão. Vão marcados como modelo a verificar contra o próprio arquivo do usuário. Um
mapeamento errado é o modo de falha que D017 documenta e a identidade de coerência não pega
tudo.

---

## D035. Calendário real vem de YAML versionado, e a previsão da v0.1 fica confirmada

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Substituir `WEEKDAYS_UTC` exige feriados. As opções eram uma biblioteca de
calendários, que acrescenta dependência e não estava instalada, ou um arquivo por venue.

**Decisão.** Arquivo. `VenueCalendarSpec` em YAML, com fuso, fechamento regular, feriados e
fechamentos antecipados, validado com pydantic e `extra="forbid"`. Mesma lógica de D016: o dado
que muda o resultado fica versionado ao lado do código, e nenhuma dependência entra por causa
dele. Meia sessão é mantida como sessão com fechamento mais cedo, porque removê la subestimaria
a contagem.

**Medição que fecha uma previsão de duas versões atrás.** A derivação de `WEEKDAYS_PER_YEAR`,
escrita na v0.1, afirmava que usar 252 com o calendário sentinela, que conta feriados,
subestimaria o fator de anualização em cerca de 1,8 por cento no Sharpe. Com uma lista real de
feriados de CME em mãos, sobre 2024 e 2025:

| calendário | sessões | sessões por ano |
|------------|---------|-----------------|
| sentinela  | 522     | 261,04          |
| CME real   | 502     | 251,36          |

O efeito no Sharpe anualizado é **-1,87 por cento**, contra 1,8 previsto. A taxa real também
cai onde deveria, perto da convenção de 252. A previsão era um argumento; agora é medição, e
virou teste.

**Alternativas descartadas.** Biblioteca de calendários, descartada por dependência nova e por
tirar de vista o dado que muda o resultado. Derivar feriados por regra, descartada porque
feriado móvel de bolsa não segue regra simples e um erro de regra é invisível.

**Consequência.** A lista de feriados precisa de manutenção, e um feriado faltando deixa na
grade uma sessão que o venue não teve. O `calendar_id` entra no `ValidationReport` desde a v0.1,
então qual lista produziu um número é sempre legível. A limitação fica declarada na docstring
em vez de ser descoberta.

---

## D036. A ordem de checagem das barreiras é o modelo, não detalhe de implementação

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 6 lista as barreiras de uma mesa proprietária mas não diz em que ordem
checá las dentro de um dia. Um dia que fura o limite diário e ao mesmo tempo atinge o alvo tem
dois desfechos possíveis, e a escolha muda a probabilidade de aprovação.

**Decisão.** Ordem fixa e declarada: limite diário, depois perda máxima, depois alvo, depois
limite de calendário. Consequências, todas intencionais:

1. Um dia que fura os dois limites é registrado como furo **diário**, que é o que a mesa diria.
2. Um dia que atinge o alvo enquanto fura um limite é **falha**, não aprovação. Mesas divergem
   nisso e a escolha fica declarada em vez de suposta.
3. O motivo da morte é guardado por caminho, não só o veredito. Uma mesa cujas falhas são todas
   limite diário é um problema diferente de uma cujas falhas são todas drawdown móvel, e uma
   probabilidade de aprovação sozinha esconde qual.

**Consequência.** `PropFirmResult` carrega a contagem por desfecho, e um teste exige que a soma
bata com o número de caminhos e que o estado interno `running` nunca chegue ao relatório.

Medido sobre 4000 caminhos, mesmas trajetórias: a mesa estática reprova 2830 por limite diário e
283 por perda máxima; a mesa móvel reprova 2644 e **588**. Mesmo alvo, mesma perda máxima, regra
mais dura, e a diferença aparece exatamente onde deveria.

---

## D037. Capital da estratégia não entra na simulação de mesa

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Os caminhos de `resample` são níveis absolutos que começam no capital inicial da
estratégia. A conta da mesa tem outro tamanho. Aplicar os níveis diretamente misturaria duas
coisas: o tamanho com que a estratégia foi operada e o tamanho da conta que a mesa oferece.

**Decisão.** Ler apenas as **diferenças diárias** dos caminhos e aplicá las a uma conta que
começa no tamanho declarado pela mesa. A pergunta respondida passa a ser "esta estratégia, neste
tamanho, numa conta daquele tamanho", que é a pergunta que o trader tem. Escalar a estratégia se
faz reamostrando com outro capital, nunca por um fator escondido aqui.

**Consequência.** Um teste desloca todos os níveis em um milhão e exige que a probabilidade de
aprovação não mude. Efeito colateral útil: o simulador aceita qualquer caminho diário, venha de
bootstrap estacionário ou de cadeia markoviana.

---

## D038. Percentis de tempo são condicionais ao evento, e o valor esperado não é

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** Duas quantidades do relatório de mesa podem ser calculadas sobre populações
diferentes, e a escolha errada em qualquer uma delas produz um número lisonjeiro.

**Decisão.** Dias até aprovar e dias até o primeiro saque são percentis **condicionais** aos
caminhos onde o evento aconteceu, e o dicionário vem vazio quando nunca aconteceu, porque
percentil de conjunto vazio não é zero. O valor esperado líquido é a média sobre **todos** os
caminhos, incluindo os que falharam e pagaram a taxa sem chegar a lugar nenhum.

A assimetria é deliberada. Uma mediana incondicional de tempo até o saque, sobre uma variável
indefinida na maioria dos caminhos, é um número que parece tranquilizador pelo motivo errado. Um
valor esperado calculado só sobre os sobreviventes responde a pergunta "quanto ganha quem passa",
quando a pergunta é "vale a pena tentar".

**Consequência.** O relatório também traz percentis do valor líquido por caminho, porque a média
de um payoff muito assimétrico não é a experiência de uma tentativa típica.

---

## D039. Ausência bloqueia o ordenamento por construção, e o requisito é declarável

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `02` seção 7 exige que nenhum teste suprimido entre no ordenamento como se
tivesse sido aprovado. A implementação óbvia é calcular o equivalente certeza sempre e esconder
quando faltar evidência. Isso deixa o número disponível para quem for olhar, e o ponto da regra
é que ele não deveria existir.

**Decisão.** `rank` devolve **duas listas**, ordenados e não ordenáveis, e para um candidato sem
seção obrigatória o equivalente certeza **não é calculado**. `Verdict` recusa o estado
inconsistente: o número existe exatamente quando o candidato é ordenável, e um veredito não
ordenável sem motivo escrito levanta erro. As duas listas nunca se misturam, porque interlear é
precisamente a lavagem que a regra proíbe.

**Descoberta ao integrar.** Com a lista estrita, o pipeline **nunca** produz veredito. O Sharpe
deflacionado precisa da matriz de todas as configurações testadas, e um log de trades sozinho
não pode fornecê la. Não é defeito: é D004 propagando até o fim. O relatório de exemplo mostra
simulação completa, risco completo, regimes com p de 1,6e-11, e mesmo assim veredito
**suprimido**, nomeando `deflated_sharpe` como a seção que bloqueia.

**Consequência.** A lista de requisitos é campo da configuração, com o padrão estrito.
Encurtá la é **declaração**, não brecha: a lista usada entra no relatório, no hash da
configuração, e o painel marca `requirements_are_default` como falso. Comparar duas estratégias
que ambas carecem da correção é legítimo desde que se diga; comparar uma com e uma sem é o que
a regra impede.

---

## D040. Equivalente certeza sob CPT em vez de nota, e por quê exatamente

**Data.** 2026-08-04
**Status.** aceita

**Contexto.** `01` lista nota única de A a F entre os não objetivos. A objeção não é imprecisão,
é ausência de interpretação: um "A" não responde pergunta nenhuma.

**Decisão.** Ordenar por equivalente certeza sob Teoria do Prospecto Cumulativa. Ele responde
uma pergunta definida: qual quantia certa um agente com aquela utilidade e aquela ponderação de
probabilidade aceitaria no lugar da distribuição de resultados da estratégia. As preferências
são impressas ao lado do número, então "esta é melhor" passa a significar "um agente com estas
preferências prefere esta".

CPT e não utilidade esperada porque os dois traços que distinguem CPT são exatamente os dois que
importam aqui: aversão a perda, de modo que drawdown não é ganho negativo, e ponderação de
probabilidade, de modo que a cauda não é descontada pela frequência dela.

**Verificações exatas.** Com `alpha = beta = lambda = gamma = delta = 1` o aparato inteiro se
reduz **exatamente** à média aritmética, a doze casas. Resultado certo tem a si mesmo como
equivalente certeza. Aposta simétrica de mais e menos um tem equivalente certeza negativo, que é
a razão de não ordenar pela média. Dominância estocástica de primeira ordem é respeitada.

**Achado numérico.** A probabilidade cumulativa construída somando pesos iguais ultrapassa 1 por
alguns épsilons no último passo, `1 - p` fica negativo, e base negativa em potência fracionária
é `nan`. O equivalente certeza saía `nan` para a entrada mais banal que existe, um resultado
certo. Corrigido por truncamento em `[0, 1]`, e o caso virou teste parametrizado.

**Limitação declarada.** Os parâmetros padrão são as estimativas de Tversky e Kahneman (1992),
médias populacionais de apostas de laboratório, não as preferências de nenhum trader. Quem for
dimensionar posição de verdade deve fornecer as próprias e ver quanto a ordem se mexe. Se ela se
mexe muito, o ordenamento nunca foi sobre as estratégias.

Os pesos de decisão **não** somam 1, e isso é correto: sob ponderação subaditiva a diferença é o
efeito certeza. Um teste fixa que sob parâmetros neutros eles somam 1 e sob os estimados somam
entre 0,8 e 1.

---

## D041. Proibir o BLAS dentro de `core`

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** A v1.0 exige que alguém clone o repositório e reproduza o exemplo. D030 fixa
igualdade byte a byte, mas o que a suíte verificava era que duas execuções **na mesma máquina**
coincidem. Máquina diferente é outra afirmação, e ela nunca tinha sido testada.

**Erro de método, registrado porque quase virou achado falso.** O primeiro experimento comparou
o hash do relatório sob 1, 2 e 8 threads de BLAS e deu três hashes distintos. Conclusão aparente:
o relatório não é reprodutível. Errado. O hash incluía o `executed_at`, que muda entre execuções
de qualquer forma. Refeito com o timestamp excluído, os seis relatórios, duas execuções por
contagem de threads, saíram idênticos em JSON e em HTML. Lição: experimento de determinismo que
não controla o campo declaradamente volátil não mede determinismo, mede o relógio.

**Achado real, medido.** `a @ b` despacha para o BLAS, que divide a redução entre threads acima de
um limiar. Medido em OpenBLAS 0.3.29: idêntico até 10⁴ elementos, **divergente a partir de 10⁵**.
O exemplo enviado tem 760 trades e por isso reproduz; um log com cem mil observações não
reproduziria entre máquinas com contagens de núcleo diferentes.

**Decisão.** Nenhum produto matricial dentro de `core`. `inner_product` e `quadratic_form` fazem
o mesmo com `numpy.sum` do produto elementar, cuja soma é pareada, de thread única, e cuja ordem
depende só do comprimento do vetor. Um teste lê a árvore sintática de `core/*.py` e falha diante
de qualquer nó `MatMult`.

**Alternativas descartadas.** Fixar variáveis de ambiente de threading dentro do pacote: precisa
acontecer antes do import de numpy e uma biblioteca não tem esse direito sobre o processo de
quem a usa. `einsum` com `optimize=False`: determinístico, mas soma ingênua.

**Consequência.** A troca **não custa exatidão, ganha**. Contra `math.fsum` sobre dez milhões de
elementos: erro relativo 1,4e-16 para `numpy.sum`, 2,3e-15 para o BLAS, 1,1e-15 para `einsum`. O
BLAS era o menos exato dos três. O custo é um array temporário do tamanho da entrada. Efeito
lateral: `bartlett_long_run_covariance` agora é simétrica **exatamente**, com tolerância zero, o
que o `dgemm` não garantia.

---

## D042. Proveniência guarda o nome do arquivo, nunca o caminho

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** `RunProvenance.input_path` guardava o caminho absoluto. Dois checkouts do mesmo
repositório em pastas diferentes produzem relatórios diferentes, o que torna o critério da v1.0
inalcançável em qualquer máquina que não seja a que gerou a referência.

**Decisão.** O campo passa a ser `input_name` e guarda apenas o nome do arquivo. O
`input_sha256` já identifica o dado, então largar o diretório não perde informação nenhuma.

**Consequência.** Segundo motivo, que não estava no critério e é mais importante que ele: o
caminho absoluto colocava o diretório pessoal de quem rodou dentro de um relatório feito para ser
entregue a outra pessoa. A correção fecha um vazamento pequeno e silencioso.

---

## D043. Igualdade byte a byte entre sistemas operacionais fica com a matriz de CI

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** Removido o BLAS, resta `math.log` e `math.exp`, que o padrão C não exige
corretamente arredondadas e que diferem entre glibc, a libm da Apple e a da Microsoft. Aqui só
existe Linux, então a afirmação "reproduz em qualquer máquina" não pode ser medida.

**Decisão.** A comparação exata contra a referência commitada roda em toda a matriz de CI, três
sistemas operacionais e duas versões de Python. Não é marcada como esperada para falhar em
nenhuma delas.

**Alternativas descartadas.** Restringir a comparação exata ao Linux e usar tolerância nos
demais: `04` proíbe tolerância escolhida para o teste passar, e essa seria exatamente isso.
Afirmar a igualdade sem testá la: é o que o projeto inteiro existe para não fazer.

**Consequência.** Se a matriz ficar vermelha no macOS ou no Windows, o que está errado é o
parágrafo de reprodutibilidade do README, não o teste. O resultado dessa matriz é o único
pedaço da v1.0 que não foi verificado antes de fechar, e está declarado como tal.

---

## D044. Dependência declarada é dependência importada

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** Ao preparar o empacotamento, uma varredura das importações revelou que
`statsmodels`, `pyarrow` e `duckdb` estavam em `dependencies` desde a v0.1 e **nunca foram
importados**. Nove versões cobrando da ordem de duzentos megabytes de quem instalasse o pacote,
por nada.

**Decisão.** Os três saem. Um teste lê `pyproject.toml`, lê a árvore sintática de `src/qvalid` e
falha nas duas direções: declarada e não importada, importada e não declarada.

**Consequência.** Metadado de empacotamento é a única parte de um projeto que nada exercita, e
por isso é onde a deriva não faz barulho. O erro não apareceu em nenhuma das nove revisões de
fechamento de versão porque nenhuma delas tinha motivo para olhar. A verificação estrutural
custa um arquivo de teste e remove a classe inteira.

---

## D045. Nome fixado em `qvalid`, porque `qval` está ocupado

**Data.** 2026-08-05
**Status.** aceita
**Cumpre.** D009

**Contexto.** D009 adiou a checagem de disponibilidade para o critério de pronto da v1.0,
argumentando que é o momento em que o nome fica caro de trocar. A checagem foi feita e deu
resposta negativa nos dois candidatos naturais: `qval` está publicado desde 2018, versão 0.4.2,
biblioteca de validação de query params sob licença MIT; e `quantify`, o nome que o projeto usa
informalmente, é um framework de computação quântica com release ativo em julho de 2026.

**Decisão.** `qvalid`, verificado livre no PyPI. Troca mecânica: 212 ocorrências, feitas por
substituição com fronteira de palavra, mais o prefixo de variável de ambiente
`QVAL_FRED_API_KEY` para `QVALID_FRED_API_KEY`. Suíte verde depois, sem ajuste manual.

**Alternativas descartadas.** `quantify-trading`, livre, mas o prefixo `quantify-` já é o
namespace de quem publica `quantify-core` e `quantify-scheduler`, e vizinhança confusa é um
custo permanente. Reivindicar `qval` por PEP 541: processo lento e o pacote tem dono
identificável.

**Consequência.** D009 acertou no gatilho e errou na estimativa implícita. O adiamento foi
barato porque o nome não aparecia hardcoded fora do `pyproject.toml` e dos imports, exatamente
como D009 previu, mas o resultado mostra que a checagem podia ter sido feita na v0.1 ao custo de
um minuto. Prazo ancorado em evento resolve o problema de D005, e ainda assim adiar uma
verificação gratuita não tem defesa.

---

## D046. Normalizar caminho na fronteira, e deixar o mypy limpo para que ele sirva

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** Ao ligar o mypy na CI apareceram 39 erros. Um deles não era ruído:

    pipeline.py:560: Item "str" of "str | Path" has no attribute "name"

A assinatura pública de `run_validation` promete `str | Path` desde a v0.6, e a mudança de D042
passou a chamar `log_path.name`. Chamada com string, a função **quebra** com `AttributeError`.
Nenhum teste pegou porque todo chamador do repositório por acaso segura um `Path`.

**Decisão.** `log_path` e `config_path` viram `Path` na primeira linha do corpo. Um teste passa
strings e confere que o relatório sai idêntico. Os 38 erros restantes foram corrigidos, não
silenciados, e a CI roda `mypy src` como etapa que barra o merge.

**Como eram os 38.** Doze eram stubs ausentes de `pandas`, `yaml` e `scipy`, resolvidos
adicionando os pacotes de stub ao grupo de desenvolvimento. Dezenove eram um único padrão em
`propfirm.py`: `np.full` infere forma literal `tuple[int]` e `np.where` devolve `tuple[int, ...]`,
então declarar os nove acumuladores com os aliases de `contracts` resolveu o bloco inteiro. O
resto eram `ndarray` sem argumentos de tipo, um `any(axis=1)` que tipa como escalar ou array, e
duas chamadas passando array onde `report/svg.py` pede `Sequence[float]`, corrigidas convertendo
na chamada para manter o `svg` livre de numpy.

**Consequência.** O mypy estava rodando localmente e sendo ignorado, e por isso não valia nada.
Um verificador com 39 erros conhecidos não distingue o quadragésimo. O ganho não é a tipagem: é
que o próximo erro real vai aparecer sozinho. Registro também que o bug de D042 foi **introduzido
por mim hoje** e sobreviveu à suíte inteira, ao exemplo ponta a ponta e à verificação por clone
limpo. Só o verificador de tipos viu.

Aliases novos em `contracts.py`: `BoolArray` e `SideArray`. O segundo nomeia o int8 que
`TradeLog.side` já usava, e a razão está no docstring: lado é mais ou menos um, então int8 é a
largura honesta e oito vezes menor.

---

## D047. Em `core`, array devolvido é array anotado

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** Com o mypy limpo localmente, a CI reprovou com **um** erro:

    core/metrics.py:330: Returning Any from function declared to return "ndarray[...]"

O erro não reproduz aqui. A causa é banal e vale registrar porque vai acontecer de novo: os
stubs do numpy resolvem `array / int` ora para `ndarray`, ora para `Any`, dependendo da versão,
e `qvalid.core.*` roda em modo estrito, onde devolver `Any` é erro. A CI resolve o numpy mais
recente; este ambiente está preso em 2.2.6 porque o interpretador é 3.10.

**Tentativa de medir, e por que falhou.** Antes de adivinhar, tentei baixar apenas os arquivos
`.pyi` do numpy que a CI usa, para checar contra os stubs dela sem rodar aquele numpy. O índice
alcançável daqui está congelado em 2.2.6 e as versões 2.5.x exigem 3.12 até para o sdist. O
caminho de medição estava fechado, e isso está declarado em vez de disfarçado.

**Decisão.** Função de `core` que devolve array anota a variável devolvida com o alias de
`contracts`. A anotação não muda o comportamento e torna o veredito do verificador independente
da versão dos stubs. Aplicado nos três pontos que uma varredura da árvore sintática identificou
como dependentes de chamada com retorno ambíguo: `bartlett_long_run_covariance`,
`probability_weight` e os dois `_require_paths`.

**Alternativas descartadas.** Fixar a versão do numpy no grupo de desenvolvimento: tornaria o
verificador reprodutível ao custo de checar contra um numpy que nenhum usuário terá, trocando
sinal real por conveniência. Desligar `warn_return_any` em `core`: é exatamente a checagem que
achou o bug de D046.

**Consequência.** Duas idas e voltas com a CI por um erro de uma linha. Aceitável, e o registro
existe para que a terceira não aconteça. Lição mais geral: um verificador cujo veredito depende
de versão de stub não fixada vai discordar entre local e CI, e a autoridade é sempre a CI.

---

## D048. A resolução do timestamp é declarada, nunca herdada do parser

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** Passado o mypy, a CI reprovou 26 testes com a mesma mensagem: a série de
referência não alinhava com a grade. O diagnóstico estava na diferença impressa:

    - 2024-01-02
    + 1970-01-20

Dois módulos escreviam `series.dt.tz_convert("UTC").astype("int64")` e tratavam o resultado como
nanossegundos. Isso é verdade quando o pandas guarda a coluna em resolução de nanossegundo e
**falso** quando guarda em microssegundo, que é o que versões novas inferem de strings ISO. O
mesmo arquivo passava a produzir timestamps mil vezes menores.

**Por que não passou despercebido.** Porque D032 já tinha trocado o alinhamento de regime de
posicional para casamento exato de timestamp. Com alinhamento posicional isso teria deslocado
silenciosamente todos os rótulos e produzido um relatório plausível e errado. Foi o alinhamento
exato que transformou um erro de unidade de fator mil em uma recusa barulhenta. A decisão de
D032 pagou por si aqui, oito meses de trabalho depois de tomada.

**Decisão.** A conversão vive em `adapters/timestamps.py`, uma função com um trabalho só, e a
resolução é dita: `.dt.as_unit("ns")` antes do cast. Um teste lê a árvore sintática e proíbe
`astype("int64")` em qualquer outro módulo do pacote.

**Verificação, e o problema que ela resolve.** O bug só aparece na versão de pandas da CI, então
esperar por ela deixaria a correção sem prova. Os testes forçam cada resolução explicitamente com
`.dt.as_unit` e afirmam que a resposta não se move. Restaurando o código antigo, a
parametrização falha em `s`, `ms` e `us` e **passa em `ns`**, que é exatamente por que o defeito
sobreviveu a 671 testes: o pandas local escolhia `ns`. Um teste vale o que vale a sua capacidade
de falhar, e este foi visto falhando.

**Consequência.** Terceira reprovação seguida da CI, e a mais séria das três: um erro real, que
atingiria qualquer usuário com pandas recente, e que nenhuma das verificações locais podia ver
porque todas rodavam sobre a mesma versão. É a resposta empírica à ressalva que a v1.0 declarou
sobre não conseguir verificar contra o ambiente da CI. A ressalva estava certa em existir.

---

## D049. Igualdade byte a byte vale dentro de um ambiente, não entre versões

**Data.** 2026-08-05
**Status.** aceita
**Substitui.** D043

**Contexto.** Corrigido D048, a CI finalmente chegou até a comparação com a referência e reprovou
com **dois** valores movidos, de 144:

    skewness              0.019949755745033305  vs  0.01994975574503329     relativo 7,0e-16
    equality_of_means_p   1,578929090929324e-11 vs 1,5789290909293596e-11   relativo 2,3e-14

Um a dois ULP, e os dois em Linux. D043 tinha atribuído o risco a bibliotecas C diferentes entre
sistemas operacionais. A previsão estava certa no mecanismo e **errada no alcance**: quebra muito
antes disso, numa troca de versão de numpy e scipy no mesmo sistema. Os dois valores são
reduções cuja ordem de soma a biblioteca escolhe: um terceiro momento, e uma função de
sobrevivência da F que passa por `log` e `exp` da plataforma.

**Decisão.** O critério da v1.0 muda de "byte a byte" para duas afirmações, cada uma verificada
onde é verdadeira:

1. Duas execuções **no mesmo ambiente** produzem bytes idênticos, excluído o timestamp. É a
   afirmação sobre a semente governar tudo, é incondicional, e continua exata em
   `test_report.py::TestByteForByte`.
2. Contra a referência commitada, os números batem a `1e-9` relativo e **todo o resto bate
   exatamente**.

**Por que 1e-9, e por que isso não é tolerância frouxa.** `04` proíbe tolerância escolhida para o
teste passar, então o número é derivado, não escolhido. O relatório renderiza **seis algarismos
significativos**. Duas execuções que concordam melhor que isso produzem o mesmo relatório para
quem lê, e é essa a propriedade que vale defender. `1e-9` fica três ordens abaixo do último
dígito que alguém vê, e cinco ordens acima do drift medido. Confirmado nos dois lados: o critério
passa no drift real de 2,3e-14 e em 1e-10, e **pega** 1e-8, 1e-6 e 1 por cento. Texto não tem
tolerância nenhuma, porque arredondamento explica um número se mover e nunca explica uma palavra
se mover.

**Alternativas descartadas.** Commitar o `uv.lock` e comparar sob ambiente travado: tornaria a
igualdade exata verdadeira, mas ao preço de a referência só valer para quem usa o lock, e a
pergunta que interessa é se o relatório que **o usuário** produz é o mesmo, com as versões que
ele tem. Continua sendo uma opção se um dia a exatidão bit a bit for exigida por auditoria.
Remover a comparação: perderia a verificação mais forte que a v1.0 tem.

**Consequência.** A quarta reprovação seguida da CI, e a única das quatro que não era defeito.
Era a afirmação que estava errada. O texto do README dizia mais do que o projeto podia sustentar,
e agora diz o que foi medido.

---

## D050. Fim de linha é convenção de plataforma, não é dado

**Data.** 2026-08-05
**Status.** aceita

**Contexto.** A matriz de CI passou em Ubuntu e **em macOS**, nas duas versões de Python, e
falhou só no Windows, com uma diferença única:

    .provenance.config_sha256: '2f915ab4...' != '5b7cfad2...'

O git no Windows faz checkout de texto com CRLF, os bytes do YAML mudam, e `sha256_of` mudou
junto. Um campo de proveniência cujo valor depende do checkout não consegue responder "rodamos a
mesma configuração", que é a única pergunta para a qual ele existe. E não é peculiaridade de CI:
quem editar uma configuração num editor do Windows e mandar para um colega no Linux cai nisso
sem ter uma matriz para avisar.

**Decisão.** `sha256_of` normaliza `\r\n` e `\r` solto para `\n` antes de somar. Um `.gitattributes`
com `text=auto eol=lf` mantém os arquivos do próprio repositório consistentes, e marca CSV como
binário para que uma fixture seja idêntica byte a byte em qualquer checkout. Duas defesas: a
segunda cobre um arquivo que o usuário trouxe de fora.

**Achado ao corrigir.** `trades_long.csv` já estava commitado **com CRLF**. É por isso que o
Windows discordou da configuração e não do log: o log era CRLF nos dois lados, e o hash batia por
acaso. Um teste passando pela razão errada, que só apareceu porque a correção mudou o hash do
log e não o da configuração.

**Consequência.** O hash do log muda com esta versão, então a referência foi regerada de
propósito, que é o único uso honesto de `regenerate_expected.py`. Um teste novo fixa a cadeia
inteira: bytes da fixture, regra de hash, e o valor no relatório commitado. E fixa também que
normalizar não normaliza conteúdo: `seed: 20260804` e `seed: 20260805` continuam com hashes
diferentes, e uma quebra de linha final continua contando, porque essa é conteúdo.

Resposta a D049 completada pela mesma execução: macOS passa. A concordância a `1e-9` sobrevive à
troca de glibc pela libm da Apple, então das três diferenças de plataforma possíveis, a que
quebrou não era numérica.

---

## Modelo para novas entradas

    ## D0XX. Título curto no imperativo

    **Data.** AAAA-MM-DD
    **Status.** proposta | aceita | substituída | revogada

    **Contexto.** Qual problema forçou a decisão.

    **Decisão.** O que foi decidido, em uma frase verificável.

    **Alternativas descartadas.** O que foi considerado e por que caiu.

    **Consequência.** O que passa a ser verdade no projeto por causa disso, inclusive o custo.
