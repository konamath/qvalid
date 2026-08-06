# 08. Plano do ciclo v2

Segunda versão. A primeira recomendava "validador, não plataforma" e tratava o QuantPad como
concorrente. **Isso estava errado, e o motivo é preciso**, não é ajuste de ênfase. Ver a seção
final, que guarda o erro em vez de apagá lo.

---

## O que muda quando a ferramenta é de uso pessoal

O argumento que sustentava metade da versão anterior era licença de redistribuição: entregar uma
ferramenta que serve dado pago a terceiros é ser revendedor de dado. **Em uso pessoal isso não
existe.** Comprar Databento com a sua chave, para a sua pesquisa, guardado no seu disco, é uso
normal e é como a empresa vende. A viga caiu, e com ela a recomendação que ela sustentava.

Restam duas perguntas, e só elas:

1. Quanto do QuantPad é software que você consegue construir?
2. Quanto é dado que você teria que comprar de qualquer jeito?

---

## O que os 300 dólares por mês compram, item por item

| Componente do QuantPad | Você já tem? | Custo real para você |
|---|---|---|
| Dados de mercado: 16 anos de futuros, 8 mil ações, cadeias OPRA e CME, L1, L2 | **Não** | O único item que custa dinheiro de verdade |
| FRED e SEC EDGAR | Sim, catalogados em `03`, FRED já implementado | Zero |
| Agente de IA que escreve e testa estratégia | **Sim.** É esta conversa | Já pago na sua assinatura |
| Código em Pine, NinjaScript, PowerLanguage | **Sim**, o agente escreve | Zero. O diferencial deles é linter, não geração |
| Monte Carlo de mesa proprietária | **Construído e testado**, nunca ligado ao relatório | Uma versão de encanamento |
| Regimes, reamostragem, drawdown, ruína, VaR | **Construído e testado**, no relatório | Zero |
| Veredito A a F | Você tem equivalente certeza sob CPT, que responde mais | Zero |
| Nuvem e cota mensal | Seu Mac | Zero |
| Comunidade e clonagem | Irrelevante para uso pessoal | Zero |

**Conclusão que reordena tudo: dos nove itens, seis você já tem, dois são irrelevantes, e um
custa dinheiro.** O que os 300 por mês compram é o **dado**, mais a conveniência de o agente já
estar ligado nele.

Isso não torna "copiar tudo" um projeto grande. Torna um projeto **pequeno e bem definido**, e
muda o que vem primeiro.

O próprio site deles diz que você pode conectar os dados ao Claude Code por MCP. Ou seja: o
produto que eles vendem é o dado com um MCP na frente. **Isso é construível.**

---

## As versões

### v2.1 A matriz de tentativas sai da busca — ENTREGUE

`qvalid trials var_*.csv -c run.yaml -o trials.csv`, e o campo de matriz no navegador. O veredito
deixa de ser inalcançável pela interface. Ver D072. 928 testes.

---

### v2.2 Ligar o que já está construído

**Escopo.** `core/propfirm.py` e `superior_predictive_ability` chegam ao pipeline e ao painel. É a
seção 6 inteira de `02` mais o teste de Hansen (2005) da seção 3, escritos e testados, que hoje
nenhum usuário alcança. Terceira e quarta ocorrência do defeito que D052 e D056 já acharam.

**Critério de pronto.** Seção `propfirm` com probabilidade de aprovação, probabilidade de saque,
valor esperado líquido do custo da avaliação e percentis de dias até o primeiro saque.
`NOT_REQUESTED` sem arquivo de regra, `SUPPRESSED` com observado e limiar quando a grade não é
diária, conforme D036. Seção `spa` roda com série de comparação, `NOT_REQUESTED` sem.

**O que não faz.** Não empacota regra de mesa alheia. Entra a mecânica de carregar regra de
arquivo mais um exemplo; a regra que vale é a da mesa que você usa, com campo `verified_on`.

**Por que primeiro.** É a versão com melhor razão entre valor entregue e código escrito em todo o
repositório: entrega uma manchete inteira do concorrente com matemática que já passou nos testes.

---

### v2.3 A camada de dados

**Escopo.** O catálogo de `03` sai do papel. Cache imutável com manifesto, conforme D033, que já
definiu o protocolo `Fetcher` injetável e comprou a suíte offline. Fontes gratuitas primeiro,
porque são a maioria do que pesquisa individual consome: FRED já existe, mais yfinance para
protótipo, exchanges de cripto para microestrutura e Dukascopy para tick de câmbio. Databento com
a **sua** chave para o que compensa pagar.

**Critério de pronto.** `qvalid fetch` traz um recorte, grava no cache, escreve linha no
manifesto, e o segundo pedido do mesmo recorte não abre socket, verificado por um buscador que
conta chamadas. Todo arquivo bruto conferido contra o hash gravado. Chave por variável de
ambiente, nunca em arquivo versionado, conforme `03` e D033.

**O que não faz.** Não assina nada. Não baixa dez anos de tick porque é possível: a estimativa em
GB entra no manifesto **antes** do download, que é a disciplina que `03` já escreveu.

**O que exige que não existe.** Verificar preço vigente do Databento antes de qualquer compra, e
registrar em `06` qual recorte vale pagar e por quê. Cadeia de opções deixa de estar fora de
escopo apenas quando você registrar essa decisão com a necessidade concreta que a justifica.

---

### v2.4 O agente enxerga o seu cache

**Escopo.** Um servidor MCP sobre o cache local, para que este chat e o Claude Code consultem os
seus dados diretamente. É literalmente o recurso que o QuantPad anuncia como integração, sobre
dado que é seu.

**Critério de pronto.** Ferramentas de cobertura, de recorte e de leitura, respondendo a partir do
cache. Descoberta pelo MCP; volume grande continua saindo por arquivo, como eles mesmos fazem.
Nenhuma ferramenta que escreva no cache: escrita é `qvalid fetch`, com manifesto.

**O que não faz.** Não expõe rede. Não autentica ninguém: escuta no loopback, como a interface.

---

### v2.5 Da ideia ao log de trades

**Escopo.** A ponte que fecha o laço. Hoje você produz o log em outro lugar; para varrer vinte
parâmetros e alimentar a v2.1 você precisa de um caminho curto de sinal a trades.

**Escopo apertado de propósito:** um instrumento, uma posição por vez, execução na barra
seguinte, custo explícito por trade. Nada de portfólio, nada de tipos de ordem, nada de modelo de
derrapagem.

**Critério de pronto.** Um sinal sobre uma série de barras vira log de trades que **passa na
identidade de coerência de D007 na importação**. Esse é o ponto: um defeito no gerador que viole
a identidade é pego na fronteira, o que é a resposta ao risco de a ferramenta validar a própria
saída.

**O que não faz.** Não é motor de backtest de propósito geral. Se você precisar de portfólio,
ordens ou derrapagem, use `vectorbt` ou `nautilus` e traga o log: o validador não se importa com
quem produziu.

**A decisão que ela exige.** Registrar em `06` que a ferramenta passa a poder gerar a entrada que
depois julga, e qual é a mitigação. A mitigação existe e é boa: a identidade de coerência é
verificada na fronteira contra multiplicador e tick declarados, então o gerador não pode se
autoaprovar sem bater com aritmética independente.

---

### v2.6 Comparar estratégias entre si

**Escopo.** `qvalid compare a.yaml b.yaml c.yaml`, ordenando por equivalente certeza, com os não
ordenáveis listados e o motivo de cada um, conforme D039. É a pergunta diária de quem valida as
próprias variantes, e `verdict.rank` já existe para ela.

**Critério de pronto.** Recusa comparar em grades diferentes com erro tipado citando as duas.
Preferências CPT impressas ao lado do ordenamento, conforme D040.

---

## O que eu ainda recomendo não construir, e agora são só duas coisas

**1. Nota de A a F.** Não por pureza: porque para você ela é **estritamente pior** do que o que
já existe. Equivalente certeza responde "qual quantia certa um agente com estas preferências
aceitaria no lugar desta distribuição", e imprime as preferências ao lado. Um "B" não responde
nada, e você é exatamente o leitor para quem a diferença importa. Se ainda assim quiser, é uma
entrada em `06` revogando D040, e eu implemento.

**2. Motor de backtest de propósito geral.** Não pelo argumento de independência da versão
anterior, que era sobre terceiros e não se aplica. Por custo: são meses para um problema resolvido
com bibliotecas maduras, e nada disso te torna melhor validador. A v2.5 entrega o caminho curto,
que é o que de fato falta.

Tudo o mais que a versão anterior desaconselhava volta para a mesa: dado, cadeia de opções,
superfície de volatilidade, MCP. Nenhum é problema de licença em uso pessoal.

---

## Onde eu errei, e por quê

A versão anterior deste documento recomendava validador em vez de plataforma, com dois argumentos.

**O argumento de licença estava certo sobre o mundo e errado sobre você.** Redistribuir dado pago
é problema real, e não é o seu problema, porque você não redistribui nada. Eu não segui esse fio
até o fim mesmo tendo escrito, em D071, que o argumento de independência não se aplica a uso
próprio: apliquei a exceção a um argumento e não ao outro.

**O argumento de "quatro produtos" superestimou três deles.** Agente, geração em DSL e nuvem só
são produtos para quem os vende. Para quem usa, o agente é a assinatura que você já tem, o Pine é
o agente escrevendo Pine, e a nuvem é o seu Mac. Contei como custo de construção o que é custo de
venda.

O que sobreviveu à revisão: a medição de que metade do que o concorrente anuncia já está
construída aqui e desligada, e o alerta contra a nota. Os dois continuam valendo, e a v2.2
existe por causa do primeiro.
