# 03. Fontes de dados

## Princípio de custo

Dado sob demanda com cache local imutável, não assinatura contínua. A lógica: para pesquisa,
o uso do catálogo é esparso e concentrado em poucos ativos e poucos períodos. Pagar acesso
permanente a tudo é comprar disponibilidade que não se consome.

Regra operacional: todo download passa pelo cache. Se o recorte já existe localmente, não
baixa de novo. Custo evitado é custo controlado.

## Catálogo

### Gratuito

| Fonte              | Cobertura                                          | Uso no projeto                          |
|--------------------|----------------------------------------------------|-----------------------------------------|
| FRED               | Séries macro do Federal Reserve de St. Louis        | Rotulagem de regime macro, controles    |
| SEC EDGAR          | Submissões, fatos XBRL, busca em texto completo     | Fundamentos, estudo de evento           |
| yfinance           | Diário amplo, intradiário limitado e instável       | Protótipo e teste, nunca resultado final|
| Exchanges de cripto| Histórico integral de trades e livro de ofertas     | Microestrutura sem custo                |
| Dukascopy          | Tick de câmbio                                      | Alta frequência sem custo               |

Observação sobre yfinance: é fonte não contratual, sujeita a quebra e a revisão silenciosa de
histórico. Serve para desenvolver. Não serve para o resultado que vai no relatório final.

**Chave de API sempre por variável de ambiente**, conforme `04`. Em particular ela **não** vai
no arquivo de configuração da execução: a configuração é versionada e o hash dela entra no
relatório, e segredo que viaja junto com procedência é segredo que vaza. O adaptador do FRED lê
`QVALID_FRED_API_KEY` e recusa construir sem ela, de modo que uma execução que não pode dar certo
falha antes de começar a trabalhar.

**A rede vive num módulo só.** `adapters/market.py` é o único arquivo do pacote que abre socket,
e a chamada tem três linhas. Todo o resto, construção da URL e parsing do payload, é testável
com bytes prontos. É isso que torna a garantia offline de `04` verificável por inspeção.

### Pago sob demanda

Databento, modelo por volume consultado, sem assinatura obrigatória para histórico. Novos
usuários recebem crédito inicial. Assinatura mensal existe, mas só faz sentido para quem
precisa de dado ao vivo, o que não é o caso deste projeto.

Verificar preço vigente antes de qualquer decisão de compra, porque a tabela muda.

Regra de disciplina: antes de qualquer download pago, estimar o volume em GB e registrar a
estimativa no manifesto. Puxar dez anos de tick porque é possível é o erro clássico.

## Armazenamento

    data/
      raw/        # exatamente como veio da fonte, imutável, nunca editado
      curated/    # parquet particionado por symbol e data
      manifest.jsonl

Consulta com DuckDB direto sobre o parquet. Não subir banco de dados enquanto o volume couber
em disco local, o que cobre folgadamente dezenas de GB.

Nada dentro de `data/` vai para o git.

### Manifesto

Uma linha JSON por recorte baixado, com: fonte, símbolo, esquema, período, timestamp do
download, número de linhas, hash do arquivo, custo estimado. Sem manifesto não há
rastreabilidade de resultado, e resultado sem procedência de dado não é reproduzível.

## Symbology

Mapa canônico obrigatório, porque cada fonte nomeia diferente e resolver isso por string na
hora do uso gera erro silencioso.

Campos: símbolo canônico, venue, raiz do contrato, multiplicador, tick mínimo, moeda,
calendário de negociação, mapeamento para o identificador de cada fonte.

O calendário declarado aqui é materializado em `adapters/calendars.py`. Desde a v0.7 existe
`VenueCalendarSpec`, um YAML versionado com fuso, fechamento regular, feriados e fechamentos
antecipados. Escolhido em vez de biblioteca de calendários pelo mesmo motivo de D016, e com a
limitação declarada de que a lista de feriados precisa de manutenção. Medido sobre CME em 2024 e
2025: 251,36 sessões por ano contra 261,04 do sentinela, efeito de menos 1,87 por cento no
Sharpe anualizado, o que confirma a previsão de 1,8 por cento escrita na derivação de
`WEEKDAYS_PER_YEAR` na v0.1. Ver D035.

## Contratos contínuos de futuros

Três decisões que precisam ser explícitas e registradas junto do dado:

1. **Regra de rolagem.** Por volume, por open interest ou por dias antes do vencimento.
2. **Método de ajuste.** Ajuste por diferença, não por razão. O ajuste por razão distorce o
   P&L em pontos, que é a unidade que importa em futuros.
3. **Consequência.** Série ajustada por diferença pode ficar negativa e não admite cálculo de
   retorno percentual. Retorno percentual, quando necessário, sai da série não ajustada por
   contrato individual.

Guardar sempre a série não ajustada junto com a ajustada.

## Vieses a controlar

- **Sobrevivência.** Universo de ações montado com a lista atual de listadas exclui as
  deslistadas e infla o resultado. Se não houver universo histórico disponível, restringir o
  escopo a poucos ativos líquidos e declarar a limitação, em vez de fingir cobertura ampla.
- **Look ahead em fundamentos.** Usar a data de publicação do EDGAR, não a data de referência
  do balanço. A diferença entre as duas costuma ser de semanas.
- **Restatements.** Fatos XBRL são revisados. Guardar a versão vigente na data e não a versão
  atual.
- **Ações corporativas.** Split e provento em série não ajustada produzem falso retorno
  extremo, que contamina qualquer estimativa de cauda.
- **Horário e fuso.** Todo timestamp em UTC no armazenamento. Conversão para fuso de bolsa
  apenas na apresentação. Erro de fuso é a causa mais comum de resultado bom demais.

## Fora de escopo da v1

Cadeia completa de opções, livro de ofertas além de amostra pontual, dados de bolsa
brasileira. Cada um desses entra por decisão registrada, com justificativa de necessidade.
