# Prompt de planejamento: para onde o Quantify vai agora

Cole o bloco abaixo numa sessão nova. Ele existe porque "vamos ficar igual ao QuantPad" não é
um objetivo, é uma comparação, e a comparação esconde uma decisão de produto que só você pode
tomar.

---

## O prompt

> Você vai me ajudar a decidir o próximo ciclo do **Quantify** (pacote `qvalid`). Não escreva
> código nesta conversa. O produto desta conversa é um **plano** no formato de `docs/05`:
> versões numeradas, cada uma entregando uma capacidade, cada uma com critério de pronto
> verificável, e cada decisão de escopo com a alternativa descartada e o motivo.
>
> ### Onde o projeto está
>
> Uma biblioteca Python com CLI e interface web local, na v1.16, com 908 testes, ruff e mypy
> limpos. Ela recebe **um log de trades fechados** e decide se os números dele sobrevivem à
> correção para busca, erro amostral, dependência serial e regime. Já existem e estão
> verificados: métricas por trade e por calendário com HAC de Newey-West, seleção de grade,
> bootstrap estacionário, distribuição de drawdown, risco de ruína com correção de continuidade,
> Sharpe deflacionado, PBO por CSCV, SPA, rotulagem causal de regimes com Monte Carlo
> markoviano, simulação de mesa proprietária, e um veredito por equivalente de certeza sob CPT.
> São setenta decisões registradas em `docs/06`, e elas restringem qualquer plano.
>
> ### O que o QuantPad de fato é
>
> Verificado no site em agosto de 2026, não suposto:
>
> - Um **IDE com agente de IA** que escreve e testa estratégias junto com você.
> - **Dados de mercado incluídos**: 16 anos de futuros, 8 mil ações americanas, cadeias de
>   opções completas OPRA e CME, trades tick a tick, L1, L2 de 30 dias, mais FRED e SEC EDGAR.
> - **Geração de código em DSL** de plataforma: PineScript, NinjaScript, PowerLanguage,
>   EasyLanguage, com linter e correção iterativa.
> - **Monte Carlo de mesa proprietária** contra as regras reais de Topstep, Apex e outras.
> - **The Verdict: uma nota de A a F** em edge, robustez, risco e tamanho de amostra.
> - Regimes, caminhos reamostrados, drawdown, ruína e VaR.
> - Comunidade com clonagem de projetos, e computação em nuvem com cota mensal.
>
> A "superfície de volatilidade implícita da AAPL" que aparece no site é um **exemplo de prompt
> dentro do IDE**, não um recurso do produto. É código que o agente escreve usando os dados de
> opções que vêm no pacote.
>
> ### As três coisas que o plano precisa encarar de frente
>
> **1. A nota de A a F é uma contradição direta, não uma lacuna.** O recurso mais visível do
> QuantPad é exatamente o que a seção 7 de `02` proíbe: colapsar evidência heterogênea numa letra
> é descrito ali como o defeito que a ferramenta existe para corrigir, e D031 construiu estados
> tipados de ausência em vez disso. Copiar a nota exige revogar essas decisões. O plano tem que
> dizer, explicitamente, se isso é para ser revogado, mantido, ou se existe uma terceira forma
> que dá a legibilidade de uma nota sem apagar a distinção entre reprovado e não medido.
>
> **2. Superfície de volatilidade é problema de dado, não de código.** Ela precisa de cadeia de
> opções com bid, ask e last por strike e vencimento, solucionador de volatilidade implícita,
> ajuste livre de arbitragem em borboleta e calendário, e curva de juros e dividendos. Escrever
> a matemática é a parte barata. O caro é a licença de dado, que é justamente o modelo de negócio
> do QuantPad. O plano tem que dizer de onde viria o dado, quanto custa, e o que acontece com
> D002, que escolheu fontes gratuitas e compra por recorte em vez de assinatura.
>
> **3. Metade do que o QuantPad anuncia já existe aqui.** Mesa proprietária, regimes, Monte Carlo
> reamostrado, drawdown, ruína e VaR estão construídos e testados. O que falta ali não é
> capacidade, é apresentação e alcance. Não me deixe reimplementar o que já está pronto.
>
> ### A pergunta que o plano tem que responder antes de qualquer versão
>
> **O Quantify é um validador ou uma plataforma de pesquisa?**
>
> - Como **validador**, ele recebe um log e julga. É o que ele é hoje, é onde ele é bom, e é
>   onde ele tem algo que o QuantPad não mostra ter: deflação com número de tentativas declarado,
>   ausência tipada em vez de nota, e reprodutibilidade byte a byte com hash de proveniência.
>   Concorrer aqui é aprofundar, não alargar.
> - Como **plataforma**, ele precisaria de dado, agente, geração de código e nuvem. São quatro
>   produtos, cada um maior que o atual, e o dado é uma barreira de licenciamento antes de ser
>   de engenharia.
>
> Me apresente essa escolha com o custo honesto de cada lado antes de propor versões. Se você
> achar que existe um caminho intermediário defensável, proponha, mas nomeie o que ele sacrifica.
>
> ### Restrições que qualquer plano tem que respeitar
>
> Da própria disciplina do projeto, e elas não são negociáveis sem uma entrada nova em `docs/06`:
>
> - **Medir antes de escrever.** Nenhum limiar entra no código sem a medição que o justifica ao
>   lado dele. Quando um critério é inatingível, corrija o critério, nunca afrouxe a tolerância.
> - **Recusar em vez de adivinhar.** Um número ausente é estado tipado. Um mapeamento ambíguo é
>   relatado, não resolvido. Um parâmetro em branco é recusado, não substituído por padrão.
> - **`core` nunca importa `adapters` nem `report`.** Nenhum cálculo na camada de interface.
> - **Sem dependência de rede nos testes**, e sem dependência de arquivo fora de `tests/fixtures`.
> - **Chave de API sempre por variável de ambiente**, nunca em arquivo versionado.
> - Toda decisão de escopo vira entrada em `docs/06` com alternativas descartadas e consequência.
>
> ### O que eu quero de volta
>
> 1. A escolha entre validador e plataforma, com o custo de cada lado, e a sua recomendação com
>    o motivo.
> 2. Três a seis versões numeradas no formato de `05`, cada uma com escopo, critério de pronto
>    verificável, e o que ela **não** faz.
> 3. Para cada versão, o que ela exige que ainda não existe: dado, dependência, licença, decisão
>    minha.
> 4. Uma lista explícita do que você recomenda **não** construir, e por quê. Essa lista é tão
>    importante quanto a outra.
> 5. Se alguma versão exigir revogar uma decisão de `docs/06`, diga qual e escreva a entrada de
>    substituição.
>
> Comece lendo `docs/00` a `docs/06` para não me propor o que já está construído ou o que já foi
> descartado com motivo.

---

## Por que o prompt está escrito assim

O pedido original era: *"ainda existem várias coisas a serem implementadas para ficar igual ao
quantpad, dados tridimensionais como superfícies de volatilidade são um exemplo, vamos criar um
plano do que fazer agora"*.

Três problemas nele, e o prompt acima corrige os três.

**"Ficar igual" não é um objetivo.** É uma comparação, e comparações escondem decisões. A mais
grave aqui é que o recurso de maior destaque do QuantPad, a nota de A a F, é literalmente o que a
especificação deste projeto proíbe. Um plano que não encarar isso vai propor a nota como
"funcionalidade que falta" e revogar em silêncio a decisão mais central do produto.

**A superfície de volatilidade estava no lugar errado da conversa.** Ela foi apresentada como
exemplo de "dados tridimensionais", que soa como problema de visualização. Não é. É um problema
de aquisição de dados de opções, e o custo dele é de licença. Nomear isso muda a pergunta de
"como desenhamos uma superfície" para "de onde vem a cadeia de opções e quanto custa", que é a
pergunta que decide se vale a pena.

**Faltava dizer onde o projeto está.** Sem isso, uma sessão nova propõe reconstruir mesa
proprietária, regimes e Monte Carlo, que estão prontos e testados há muitas versões.
