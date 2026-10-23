# 00 — Contexto inicial (documento de transferência)

> **Natureza deste arquivo:** registro histórico da fase de concepção, transferido entre agentes de IA em 23/10/2026.
> Não é documento normativo. Quando houver divergência, prevalecem os documentos formais numerados (01 a 08).
> Conteúdo preservado sem alterações a partir daqui.

---

# CONTEXTO COMPLETO — PROJETO DE TRADING ALGORÍTMICO COM INTELIGÊNCIA ARTIFICIAL

**Tipo:** Documento de transferência de contexto entre agentes de IA  
**Data:** 23 de outubro de 2026  
**Status:** Concepção e planejamento — documentação ainda não iniciada  
**Nome do projeto:** Ainda não definido  
**Próxima atividade:** Elaborar a documentação formal, começando pelo PRD.

---

# 1. CONTEXTO DO USUÁRIO

O usuário é estudante de Engenharia de Software, com experiência e interesse em Python, desenvolvimento backend, arquitetura de software, infraestrutura cloud e AWS.

Seu ambiente principal de desenvolvimento é um MacBook Air M2, com 16 GB de RAM, 256 GB de armazenamento e macOS ARM64.

Tem interesse em inteligência artificial, estatística, automação, negócios e construção de produtos tecnológicos.

Possui ambições empreendedoras e busca desenvolver projetos tecnicamente interessantes com potencial de aplicação comercial.

O usuário tem experiência anterior, ainda que limitada, operando no mercado financeiro e deseja compreender como sistemas profissionais tomam decisões de compra e venda.

**Preferência de trabalho:** deseja estruturar e documentar o projeto adequadamente antes de começar a implementação. Não pretende simplesmente copiar código e experimentar sem uma arquitetura definida.

# 2. ORIGEM DO PROJETO

A conversa começou com uma dúvida:

> "Day trade e scalping é real? Ou é só invenção de vendedor de curso?"

Discutimos que day trading e scalping são modalidades legítimas de operação financeira, mas que conseguir rentabilidade consistente é extremamente difícil.

Foram mencionadas pesquisas sobre a elevada proporção de perdas entre traders individuais brasileiros.

Também discutimos a diferença entre:

- Day trading: abertura e encerramento de posições no mesmo pregão.
- Scalping: operações intradiárias de curtíssima duração, buscando pequenos movimentos de preço.
- Swing trading: posições mantidas por vários dias ou semanas.

A conclusão foi que essas modalidades existem e são utilizadas profissionalmente, mas isso não significa que sejam lucrativas para a maioria dos participantes.

## 2.1 Experiência anterior na Toro

O usuário relatou que acompanhava profissionais operando pela Toro Investimentos e que chegou a operar junto com esses analistas.

Os analistas apresentavam operações e o usuário replicava manualmente suas entradas e saídas.

O usuário confirmou que eles utilizavam principalmente **gráficos de candles**.

Essa experiência levou à investigação de como analistas profissionais identificam oportunidades.

Foram discutidos:

1. Análise técnica.
2. Price Action.
3. Suporte e resistência.
4. Tendências.
5. Padrões de candles.
6. Volume de negociação.
7. Fluxo de ordens.
8. Gerenciamento de risco.

Um exemplo discutido foi um rompimento de resistência seguido de reteste do nível, usado como possível sinal de entrada.

Ressaltamos que identificar padrões visuais não comprova, por si só, a existência de vantagem estatística.

## 2.2 Surgimento da ideia de IA

O usuário perguntou:

> "Uma IA bem treinada consegue fazer as 3 análises simultaneamente, não?"

As três análises mencionadas eram:

- Análise técnica.
- Fluxo de ordens.
- Análise de volume.

A resposta foi que sistemas computacionais podem combinar múltiplos conjuntos de dados e processá-los mais rapidamente que humanos.

Isso levou à proposta de desenvolver um sistema de trading utilizando inteligência artificial e dados de mercado.

**Hipótese central do projeto:**

Um sistema que combina diferentes dimensões de informação de mercado poderá apresentar capacidade de decisão superior à de estratégias que consideram apenas candles.

Essa hipótese ainda precisa ser comprovada experimentalmente.

# 3. VISÃO DO PRODUTO

Desenvolver um sistema autônomo de trading algorítmico capaz de:

1. Receber dados financeiros continuamente.
2. Interpretar informações técnicas e microestruturais.
3. Identificar padrões e oportunidades.
4. Estimar probabilidades de movimentos de preços.
5. Avaliar risco, liquidez e custos de execução.
6. Decidir entre comprar, vender ou aguardar.
7. Executar operações automaticamente, inicialmente em ambiente simulado.
8. Monitorar posições abertas.
9. Gerenciar ordens, stops, exposição e perdas.
10. Registrar todas as decisões e seus fundamentos.
11. Avaliar estatisticamente o desempenho.
12. Incorporar futuramente o contexto macroeconômico e geopolítico.

O objetivo de longo prazo é a **execução autônoma de operações**, não apenas um assistente que recomenda trades.

Entretanto, operações reais não fazem parte do MVP inicial.

## 3.1 Objetivo técnico

Construir um sistema de engenharia financeira e machine learning capaz de testar estratégias quantitativamente.

## 3.2 Objetivo financeiro

Investigar se o sistema consegue produzir resultados positivos consistentes, líquidos de custos e ajustados ao risco.

Isso é uma meta de pesquisa, não um resultado esperado ou garantido.

## 3.3 Princípio central

**Automatizar uma estratégia não a torna lucrativa.**

A prioridade é demonstrar a existência de uma vantagem estatística antes de permitir operações com capital real.

# 4. FONTES DE INFORMAÇÃO

A arquitetura evoluiu de três análises para quatro conjuntos de dados de mercado e um módulo adicional de contexto externo.

## 4.1 Candles — Technical Analysis

Responsável pela análise histórica dos preços.

Informações:

- Open.
- High.
- Low.
- Close.
- Intervalo temporal.

Análises possíveis:

- Tendências.
- Suportes e resistências.
- Rompimentos.
- Retestes.
- Padrões de candles.
- Médias móveis.
- Volatilidade.
- VWAP.
- IFR/RSI.
- Outros indicadores técnicos.

O modelo deverá investigar se essas variáveis têm capacidade preditiva demonstrável.

Os padrões técnicos não devem ser tratados como regras infalíveis.

## 4.2 Volume — Volume Analysis

Responsável por avaliar a atividade e a intensidade das negociações.

Análises possíveis:

- Volume por intervalo.
- Volume relativo.
- Mudanças anormais de volume.
- Concentração de negociações por região de preço.
- Volume Profile.
- Relação entre volume e variação de preço.

O volume pode ajudar a contextualizar rompimentos, tendências e eventos de volatilidade.

**Observação:** volume e candles não são necessariamente fontes independentes. Ambos podem ser derivados dos dados de negócios executados.

## 4.3 Order Book — Order Book Analysis

Responsável por analisar ofertas de compra e venda disponíveis no mercado.

Informações:

- Preços de compra (bids).
- Preços de venda (asks).
- Quantidades disponíveis.
- Spread.
- Profundidade do mercado.
- Desequilíbrio entre ofertas.
- Alterações nas ordens e liquidez.

Possíveis indicadores:

- Bid/ask imbalance.
- Spread relativo.
- Profundidade em diferentes níveis.
- Variações de liquidez.
- Pressão aparente compradora e vendedora.

É importante distinguir ofertas presentes no livro de negócios efetivamente executados.

Uma ordem exibida pode ser cancelada antes de sua execução.

## 4.4 Times & Trades — Trade Flow Analysis

Essa quarta categoria foi adicionada durante a discussão sobre Tape Reading.

Responsável por analisar os negócios efetivamente executados.

Informações:

- Preço do negócio.
- Quantidade.
- Timestamp.
- Classificação da agressão compradora ou vendedora, quando disponível ou inferível.

Análises possíveis:

- Fluxo de negócios.
- Agressão de compra e venda.
- Desequilíbrio de volume executado.
- Intensidade de negociação.
- Mudanças no ritmo de execução.

Esse conjunto complementa o Order Book.

## 4.5 News & Macro Intelligence

O quinto módulo surgiu quando o usuário perguntou sobre a análise de acontecimentos globais.

Exemplos apresentados:

- Notícias relevantes.
- Declarações de líderes.
- Ameaças.
- Guerras.
- Geopolítica.
- Decisões de bancos centrais.
- Inflação.
- Indicadores econômicos.

Identificamos quatro conceitos relacionados:

**Análise Fundamentalista:** avaliação de fatores econômicos e fundamentos dos ativos.

**Análise Macroeconômica:** juros, inflação, PIB, política monetária, emprego e indicadores relacionados.

**Análise Geopolítica:** conflitos, sanções, tensões diplomáticas e acontecimentos internacionais.

**Sentiment Analysis / Event-Driven Analysis:** identificação de sentimento e impacto potencial de eventos específicos sobre o mercado.

### Implementação proposta

O módulo poderá utilizar:

- APIs de notícias.
- RSS de fontes oficiais.
- Calendários econômicos.
- Comunicados de bancos centrais.
- Dados macroeconômicos.
- NLP.
- LLMs.
- Classificadores de eventos.

### Processamento

Para cada evento, estimar:

1. Tipo de acontecimento.
2. Fonte e confiabilidade.
3. Momento da publicação.
4. Se a informação é realmente nova.
5. Ativos potencialmente afetados.
6. Relevância.
7. Sentimento e possíveis cenários de impacto.
8. Grau de incerteza.
9. Possível necessidade de reduzir exposição ou suspender operações.

### Limitações identificadas

- Notícias podem chegar depois de o mercado já reagir.
- Informações podem estar incorretas.
- Uma notícia negativa não necessariamente implica queda.
- O impacto depende das expectativas anteriores.
- Um LLM pode interpretar incorretamente o acontecimento.
- Dados históricos de notícias precisam preservar os horários em que as informações efetivamente ficaram disponíveis.
- APIs de notícias gratuitas podem apresentar latência incompatível com estratégias de scalping.

### Decisão tomada

**O News & Macro Intelligence será introduzido depois da validação dos módulos iniciais de dados de mercado.**

A recomendação inicial foi utilizá-lo principalmente como filtro contextual e de risco.

Por exemplo, diante de uma divulgação econômica importante, o sistema poderá suspender novas operações, reduzir exposição ou exigir condições adicionais.

Essas regras precisarão ser testadas, pois interromper operações nem sempre melhora o desempenho.

# 5. FORNECEDORES DE DADOS AVALIADOS

Discutimos principalmente Binance, Alpaca, Nelogica e fontes de dados macroeconômicos.

## 5.1 Binance

**Escolha inicial recomendada para o MVP.**

Mercado: criptomoedas.

Ativo inicialmente sugerido: BTC/USDT.

Motivos:

- APIs públicas.
- WebSockets.
- Histórico de mercado.
- Dados de candles.
- Fluxo de negócios.
- Dados de profundidade.
- Ambiente de testes.
- Mercado com negociação praticamente contínua.
- Integração conveniente com Python.

Exemplos de endpoints discutidos:

**Candles de 1 minuto:**

`wss://stream.binance.com:9443/ws/btcusdt@kline_1m`

**Negócios executados:**

`wss://stream.binance.com:9443/ws/btcusdt@trade`

**Profundidade parcial do livro:**

`wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms`

O último endpoint transmite uma visão parcial da profundidade. Caso o projeto precise reconstruir um livro de ofertas local completo, será necessário implementar o procedimento correto de snapshot e atualizações incrementais previsto na documentação da Binance.

Referência:

https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams

**Importante:** esses endpoints são referências para desenvolvimento. É necessário verificar disponibilidade, limites, termos de uso, sequência de eventos e requisitos atuais antes de implementar.

## 5.2 Alpaca Markets

Alternativa para estudar ações americanas.

Possui:

- API de dados de mercado.
- Streaming.
- Dados históricos.
- Paper trading.
- API de ordens simuladas.

A cobertura gratuita em tempo real é limitada em relação aos feeds consolidados das bolsas americanas.

Referência:

https://docs.alpaca.markets/

## 5.3 Nelogica / ProfitDLL

Alternativa importante caso o sistema futuramente opere ativos brasileiros.

Mercados de interesse:

- Mini-índice (WIN).
- Minidólar (WDO).
- Outros instrumentos disponíveis conforme licenciamento.

Recursos mencionados:

- Dados de negociações.
- Informações de livro de ofertas.
- Dados tick a tick.
- Integração com infraestrutura de negociação.

Limitações:

- Licenciamento.
- Custo.
- Requisitos técnicos.
- Dependências de Windows x86-64 no caso da solução discutida.
- Maior complexidade para integração direta com o MacBook ARM64 do usuário.

## 5.4 B3

Outra possibilidade futura é o licenciamento de dados diretamente da B3 ou por distribuidores autorizados.

Isso exigirá análise de custos, autorização de uso, distribuição de market data e integração.

**Nenhum fornecedor brasileiro foi contratado ou escolhido definitivamente.**

## 5.5 GDELT

Fonte potencial para o módulo geopolítico e de notícias.

Site:

https://www.gdeltproject.org/

Possui conjuntos de informações sobre acontecimentos globais e cobertura jornalística.

Foi mencionado que determinados conjuntos de dados são atualizados em ciclos de aproximadamente 15 minutos.

Isso pode ser útil para contexto, mas não equivale a notícias instantâneas para scalping.

## 5.6 Trading Economics

Fonte potencial para calendários e indicadores econômicos.

Referência:

https://docs.tradingeconomics.com/

Foi discutida a possibilidade de receber atualizações econômicas por streaming.

Condições comerciais e limites de uso precisam ser verificados.

## 5.7 FRED

Fonte para séries macroeconômicas e dados econômicos.

Referência:

https://fred.stlouisfed.org/docs/api/fred/

É útil principalmente para dados econômicos estruturados e históricos.

Não substitui um feed de notícias instantâneas.

# 6. ARQUITETURA CONCEITUAL

A arquitetura preliminar proposta é:

```text
                 MARKET DATA
                     |
    +----------------+----------------+
    |                |                |
 Candles           Trades         Order Book
    |                |                |
    +----------------+----------------+
                     |
            DATA INGESTION
                     |
              DATA STORAGE
                     |
          FEATURE ENGINEERING
                     |
              AI / ML ENGINE
                     |
              SIGNAL ENGINE
                     |
               RISK MANAGER
                     |
             EXECUTION ENGINE
                     |
           EXCHANGE / TESTNET
                     |
               MONITORING
```

Futuramente:

```text
NEWS / MACRO SOURCES
         |
    INGESTION
         |
  NLP / LLM ENGINE
         |
  EVENT CLASSIFIER
         |
 CONTEXT / RISK SIGNAL
         |
   SIGNAL ENGINE
         |
    RISK MANAGER
```

O processamento dos eventos de notícias não deverá bloquear o processamento dos dados de mercado.

A arquitetura precisa distinguir claramente:

- Coleta de dados.
- Tratamento.
- Engenharia de atributos.
- Inferência.
- Estratégia.
- Gestão de risco.
- Execução.
- Monitoramento.

# 7. COMPONENTES PREVISTOS

## 7.1 Market Data Engine

Responsabilidades:

- Conectar às APIs.
- Manter WebSockets.
- Receber mensagens continuamente.
- Detectar desconexões.
- Implementar reconexões.
- Identificar perdas ou inconsistências de mensagens.
- Padronizar timestamps.
- Normalizar estruturas.
- Persistir dados.
- Distribuir dados aos módulos seguintes.

É importante preservar tanto o horário do evento quanto o horário de recebimento, quando possível.

## 7.2 Feature Engineering Engine

Transforma dados brutos em variáveis utilizáveis por modelos.

Exemplos:

- Retornos.
- Médias móveis.
- RSI.
- VWAP.
- Volatilidade.
- Volume relativo.
- Order book imbalance.
- Bid-ask spread.
- Intensidade de negócios.
- Pressão de agressão.
- Características de tendências.

As variáveis precisam ser calculadas apenas com informações disponíveis no instante da decisão.

Evitar data leakage.

## 7.3 AI / ML Engine

Componente responsável pela modelagem estatística.

Tecnologias mencionadas:

- Python.
- Pandas.
- NumPy.
- Scikit-learn.
- XGBoost.
- PyTorch.

**Recomendação inicial:** começar com modelos relativamente simples, principalmente XGBoost, em vez de treinar uma rede neural grande imediatamente.

O modelo pode estimar:

- Probabilidade de movimento positivo.
- Probabilidade de movimento negativo.
- Magnitude esperada do movimento.
- Volatilidade.
- Outras variáveis úteis para decisões.

Ainda não definimos o target exato, o horizonte de previsão ou a formulação final do problema.

Essas decisões devem constar na documentação de IA.

## 7.4 Signal Engine

Recebe os resultados dos modelos e determina se existe um sinal operacional.

Possíveis estados:

- BUY.
- SELL.
- HOLD / NO TRADE.

O sinal não significa necessariamente enviar uma ordem.

Ele ainda precisa ser validado pelo gerenciamento de risco.

## 7.5 Risk Management Engine

**Componente obrigatório e independente da IA.**

Responsabilidades previstas:

- Limite de exposição.
- Limite de posição.
- Limite de perdas.
- Stop-loss.
- Controle de ordens abertas.
- Verificação de liquidez.
- Verificação de custos.
- Controle de volatilidade.
- Limites de frequência operacional.
- Interrupção de operações em situações excepcionais.
- Kill switch.

Uma IA não deve poder ignorar os limites de risco.

O sistema também deverá impedir decisões de aumento de exposição não autorizadas.

No MVP:

- Não utilizar dinheiro real.
- Não utilizar alavancagem.
- Não conceder acesso de execução a contas reais.

## 7.6 Execution Engine

Responsável por transformar decisões aprovadas em operações.

Responsabilidades:

- Criar ordens.
- Enviar ordens.
- Acompanhar estados.
- Registrar confirmações.
- Lidar com ordens rejeitadas.
- Cancelar ordens quando necessário.
- Tratar preenchimentos parciais.
- Evitar ordens duplicadas.
- Sincronizar posições com o ambiente de execução.
- Registrar custos e resultados.

Inicialmente deverá operar apenas em simulador ou testnet.

## 7.7 Monitoring & Observability

Responsabilidades:

- Registrar decisões.
- Acompanhar o comportamento do modelo.
- Monitorar conectividade.
- Medir latência.
- Acompanhar resultados.
- Registrar erros e anomalias.
- Produzir alertas.
- Manter histórico das operações.
- Permitir auditoria e análise posterior.

A infraestrutura AWS poderá ser avaliada posteriormente, mas nenhum serviço de nuvem foi escolhido definitivamente.

# 8. EXECUÇÃO AUTOMÁTICA

O usuário perguntou:

> "Acho que o útil é executar as operações automaticamente, não?"

Foi acordado que a execução automática faz sentido como objetivo do sistema.

Vantagens identificadas:

- Velocidade.
- Consistência.
- Operação sem intervenção humana contínua.
- Capacidade de monitorar múltiplas variáveis.
- Redução de decisões emocionais.
- Registro sistemático.
- Reprodutibilidade.

Mas nenhuma vantagem operacional garante lucro.

A execução automática deverá ser desenvolvida com segurança, controles de risco e testes progressivos.

## 8.1 Fases de validação

**Backtesting**

Avaliar estratégias em dados históricos.

**Walk-forward Validation**

Treinar em intervalos anteriores e avaliar em períodos posteriores, sem utilizar informações futuras.

**Paper Trading**

Executar decisões simuladas usando informações de mercado recebidas ao vivo.

**Shadow Mode (possível etapa adicional)**

Gerar decisões em produção sem enviar ordens reais, comparando previsões com os resultados posteriores.

**Operação real controlada**

Possibilidade futura, condicionada a validação rigorosa, segurança e autorização explícita.

Não existe decisão tomada de investir dinheiro real neste momento.

# 9. BACKTESTING E VALIDAÇÃO

O projeto não deve avaliar os resultados apenas pela quantidade de operações vencedoras.

Métricas possíveis:

- Retorno líquido.
- Retorno acumulado.
- Win rate.
- Profit factor.
- Expectativa por operação.
- Maximum drawdown.
- Sharpe ratio.
- Sortino ratio.
- Volatilidade dos retornos.
- Quantidade de operações.
- Exposição.
- Custos operacionais.
- Turnover.
- Desempenho por regime de mercado.

Devem ser considerados:

- Fees.
- Spread.
- Slippage.
- Latência.
- Liquidez disponível.
- Impacto de mercado, quando aplicável.
- Impostos, quando relevante para a avaliação financeira líquida.

**O resultado da estratégia deve ser comparado com baselines**, como:

1. Estratégia simples baseada apenas em candles.
2. Estratégia técnica com indicadores.
3. Estratégia com candles e volume.
4. Estratégia acrescentando fluxo de negócios e order book.
5. Estratégia completa com os quatro conjuntos de dados de mercado.
6. Futuramente, estratégia com News & Macro Intelligence.

O objetivo será medir o ganho incremental de cada conjunto de informações.

Não se deve presumir que adicionar mais variáveis melhorará a previsão.

O modelo precisa ser testado em períodos que não participaram de seu treinamento nem da seleção de hiperparâmetros.

# 10. NEWS & MACRO INTELLIGENCE — ESTUDO DE VIABILIDADE

O usuário perguntou se esse novo módulo seria viável.

A conclusão foi:

**Viabilidade técnica: alta.**

**Rentabilidade adicional: não demonstrada.**

Uma arquitetura possível:

```text
NEWS API
    |
ECONOMIC CALENDAR
    |
OFFICIAL STATEMENTS
    |
GEOPOLITICAL SOURCES
    |
NORMALIZATION / DEDUPLICATION
    |
NLP / LLM
    |
EVENT CLASSIFICATION
    |
RELEVANCE / NOVELTY / UNCERTAINTY
    |
CONTEXTUAL RISK SIGNAL
    |
TRADING DECISION SYSTEM
```

O módulo poderia identificar acontecimentos inesperados e sinalizar necessidade de maior cautela.

Por exemplo, em um evento de divulgação de juros, a estratégia poderia receber um sinal contextual de risco elevado.

Mas esse comportamento deverá ser validado historicamente.

O contexto pode ser útil mesmo quando não fornece uma previsão confiável de direção.

**Decisão:** desenvolver esse módulo somente após estabelecer os resultados dos modelos iniciais, como parte da versão V0.4.

# 11. ROADMAP PRELIMINAR

Foram propostas quatro versões principais.

## V0.1 — Data Engine

Objetivo: criar infraestrutura confiável para coleta, tratamento e armazenamento de informações.

Escopo:

- Conexão com Binance.
- Recebimento de candles.
- Recebimento de negócios.
- Recebimento de order book.
- Processamento de volume.
- Persistência de dados.
- Tratamento de falhas.
- Monitoramento da ingestão.

Entregável: pipeline funcional de dados.

## V0.2 — AI Engine

Objetivo: desenvolver e avaliar modelos quantitativos.

Escopo:

- Engenharia de atributos.
- Dataset de treinamento.
- Baselines.
- Treinamento.
- Validação temporal.
- Backtesting.
- Métricas.
- Comparação de estratégias.

Entregável: modelos experimentais acompanhados de resultados reproduzíveis.

## V0.3 — Trading Engine

Objetivo: desenvolver o sistema autônomo de execução simulada.

Escopo:

- Signal Engine.
- Risk Manager.
- Execution Engine.
- Integração com ambiente de simulação.
- Gerenciamento de ordens.
- Monitoramento.
- Paper trading.
- Logs e histórico.

Entregável: robô que opera autonomamente sem usar dinheiro real.

## V0.4 — Macro Intelligence

Objetivo: incluir informações econômicas e geopolíticas.

Escopo:

- APIs econômicas.
- Coleta de notícias.
- LLM e NLP.
- Classificação de eventos.
- Análise de relevância.
- Integração com o sistema de risco e decisão.
- Comparação entre versões com e sem informações externas.

Entregável: avaliação experimental do ganho incremental proporcionado pelo contexto macroeconômico.

**As versões são propostas preliminares e ainda não representam um cronograma fechado.**

# 12. DECISÃO MAIS RECENTE: DOCUMENTAÇÃO ANTES DO CÓDIGO

Depois de discutir o roadmap, o usuário interrompeu a proposta de implementação e perguntou:

> "Acho que antes é a documentação, não?"

Foi acordado que **o próximo passo é construir a documentação formal do projeto**.

Não devemos começar diretamente com WebSockets, treinamento de IA ou estruturação do backend.

Primeiro precisamos definir de maneira organizada:

- Objetivos.
- Escopo.
- Requisitos.
- Premissas.
- Restrições.
- Arquitetura.
- Interfaces.
- Regras de negócio.
- Critérios de validação.
- Estratégia de desenvolvimento.

A documentação deve orientar a implementação.

# 13. DOCUMENTOS PLANEJADOS

A estrutura sugerida contém oito documentos.

## 01 — Product Requirements Document (PRD)

Documento inicial e prioritário.

Conteúdo:

- Visão do produto.
- Problema.
- Justificativa.
- Objetivos.
- Público-alvo.
- Escopo do MVP.
- Funcionalidades.
- Exclusões de escopo.
- Premissas.
- Restrições.
- Indicadores de sucesso.
- Critérios de aceitação.

## 02 — Software Requirements Specification (SRS)

Especificação de requisitos.

Conteúdo:

- Requisitos funcionais.
- Requisitos não funcionais.
- Regras de negócio.
- Restrições.
- Casos de uso.
- Critérios de aceitação.
- Requisitos de disponibilidade.
- Requisitos de segurança.
- Requisitos de desempenho.
- Requisitos de observabilidade.

Os requisitos devem ser identificáveis e rastreáveis.

Exemplos de identificação:

- RF-001.
- RF-002.
- RNF-001.
- RN-001.

## 03 — System Architecture Document

Conteúdo:

- Visão geral da arquitetura.
- Diagrama de contexto.
- Diagrama de componentes.
- Fluxos de dados.
- Limites de responsabilidade.
- Interfaces.
- Comunicação entre módulos.
- Persistência.
- Tratamento de falhas.
- Infraestrutura.
- Trade-offs arquiteturais.

A abordagem arquitetural definitiva ainda não foi escolhida.

Não assumir prematuramente microservices, Kubernetes ou tecnologias complexas.

## 04 — AI & Quantitative Research Specification

Conteúdo:

- Definição do problema de ML.
- Definição dos targets.
- Horizontes temporais.
- Features.
- Fontes.
- Modelos.
- Baselines.
- Treinamento.
- Validação.
- Experimentos.
- Métricas.
- Prevenção de overfitting.
- Prevenção de data leakage.
- Versionamento de datasets e modelos.
- Reprodutibilidade.
- Critérios de promoção de modelos.

## 05 — Trading & Risk Engine Specification

Conteúdo:

- Estratégias.
- Sinais.
- Tipos de ordens.
- Estados das ordens.
- Regras de execução.
- Gerenciamento de posições.
- Stops.
- Limites de risco.
- Limites de perdas.
- Kill switch.
- Falhas operacionais.
- Reconciliação.
- Condições que proíbem operações.

## 06 — Data & Integrations Specification

Conteúdo:

- Fontes.
- APIs.
- Esquemas.
- Timestamps.
- Formatos.
- Frequências.
- Retenção.
- Validação.
- Normalização.
- Armazenamento.
- Recuperação de falhas.
- Reconstrução do order book.
- Tratamento de inconsistências.
- Licenciamento e limites de uso.

## 07 — Testing & Validation Plan

Conteúdo:

- Testes unitários.
- Testes de integração.
- Testes de carga.
- Testes de falhas.
- Backtesting.
- Walk-forward.
- Paper trading.
- Métricas financeiras.
- Critérios de aprovação.
- Critérios para rejeitar estratégias.
- Segurança da execução.
- Procedimentos de rollback.

## 08 — Roadmap & Milestones

Conteúdo:

- Fases do projeto.
- Entregáveis.
- Dependências.
- Prioridades.
- Milestones.
- Critérios de conclusão.
- Planejamento de evolução.

# 14. DECISÕES JÁ ESTABELECIDAS

| Tema | Decisão ou direção atual |
|---|---|
| Natureza | Trading algorítmico com IA |
| Objetivo de longo prazo | Execução autônoma |
| Mercado inicial | Criptomoedas |
| Ativo inicialmente sugerido | BTC/USDT |
| API inicial sugerida | Binance |
| Linguagem principal | Python |
| Dados | Candles, volume, order book e trades |
| Contexto externo | News & Macro Intelligence futuro |
| ML inicial sugerido | XGBoost e baselines simples |
| Execução no MVP | Simulada |
| Capital real no MVP | Não |
| Alavancagem no MVP | Não |
| Risco | Controle independente da IA |
| Primeira entrega | Documentação |
| Primeiro documento | PRD |
| Arquitetura definitiva | Não escolhida |
| Banco de dados | Não escolhido |
| Infraestrutura | Não escolhida |
| Nome do projeto | Não escolhido |

# 15. DECISÕES AINDA EM ABERTO

O agente não deve inventar respostas definitivas para questões ainda não discutidas.

Entre elas:

**Mercado e estratégia**

- Spot ou futuros no longo prazo?
- Estratégia de tendência, reversão, market making ou outra?
- Scalping, intraday ou horizonte maior?
- Quais intervalos de candles?
- Qual horizonte de previsão?
- Como definir targets?
- Como combinar sinais?
- Qual universo de ativos?

**Modelagem**

- Classificação, regressão ou ranking?
- Modelo único ou múltiplos modelos?
- Como tratar mudanças de regime?
- Frequência de treinamento?
- Métrica primária de otimização?
- Política de validação?

**Arquitetura**

- Monólito modular ou serviços separados?
- Banco relacional, time-series ou armazenamento híbrido?
- Uso de filas ou streaming?
- Tecnologias de observabilidade?
- Execução local ou cloud?
- Estratégia de deploy?

**Execução**

- Modelo de simulação de fills.
- Limites operacionais.
- Critérios de desligamento.
- Controle de ordens duplicadas.
- Tratamento de reconexões.
- Condições necessárias para eventual operação real.

Essas questões devem ser resolvidas progressivamente durante a documentação, de acordo com sua importância.

# 16. PREMISSAS E RISCOS DO PROJETO

## Riscos financeiros

- Perdas.
- Estratégias sem vantagem estatística.
- Custos elevados.
- Volatilidade.
- Liquidez insuficiente.
- Desempenho instável.
- Overfitting.

## Riscos técnicos

- WebSockets desconectados.
- Mensagens fora de ordem.
- Perda de eventos.
- Dados incompletos.
- Latência.
- Erros de inferência.
- Ordens duplicadas.
- Divergência entre posições calculadas e reais.
- Falhas no gerenciador de risco.
- Falhas de autenticação ou segurança de credenciais.

## Riscos de pesquisa

- Data leakage.
- Look-ahead bias.
- Selection bias.
- Survivorship bias.
- Data snooping.
- Escolha de parâmetros baseada excessivamente no histórico.
- Mudança de regime.
- Estratégias que funcionam apenas em simulações.
- Testes com custos irreais.

## Riscos do módulo de notícias

- Latência de publicação.
- Informações falsas.
- Notícias repetidas.
- Eventos já precificados.
- Classificações incorretas.
- Alucinações de LLM.
- Dados históricos que não representam corretamente a disponibilidade temporal da informação.

A arquitetura deverá incorporar mecanismos para minimizar esses riscos.

# 17. DEFINIÇÃO DE SUCESSO

O projeto não deve ser considerado bem-sucedido apenas porque consegue executar ordens automaticamente.

Precisamos considerar diferentes camadas.

**Sucesso técnico:**

- Dados confiáveis.
- Sistema estável.
- Inferência reproduzível.
- Execução simulada correta.
- Gerenciamento de risco funcional.
- Observabilidade adequada.

**Sucesso científico:**

- Experimentos reproduzíveis.
- Baselines definidos.
- Métricas corretas.
- Validação fora da amostra.
- Comparação de diferentes conjuntos de features.
- Conhecimento demonstrável sobre o que funciona ou não.

**Sucesso financeiro experimental:**

- Resultados líquidos positivos fora da amostra.
- Risco controlado.
- Desempenho superior aos baselines selecionados.
- Robustez em diferentes períodos e regimes.
- Evidência de que os resultados não dependem apenas de ajuste excessivo ao histórico.

Ainda não foram definidos critérios quantitativos mínimos de aprovação.

Esses critérios deverão ser especificados nos documentos formais.

# 18. CONCEITOS IMPORTANTES DISCUTIDOS

**Price Action:** análise do movimento dos preços por meio da estrutura gráfica.

**Tape Reading:** leitura das informações de fluxo, ofertas e negócios.

**Order Book:** conjunto de ofertas pendentes de compra e venda.

**Times & Trades:** registro dos negócios efetivamente executados.

**Volume Analysis:** análise da atividade de negociação.

**Technical Analysis:** estudo de comportamento dos preços e indicadores.

**Fundamental Analysis:** avaliação de fundamentos econômicos e dos ativos.

**Macro Analysis:** interpretação de fatores macroeconômicos.

**Geopolitical Analysis:** avaliação de riscos e acontecimentos internacionais.

**Sentiment Analysis:** extração de sentimento e percepção a partir de textos e outros dados.

**Event-Driven Trading:** estratégias relacionadas a eventos relevantes.

**Quantitative Trading:** uso de modelos matemáticos, estatística e algoritmos para gerar decisões financeiras.

**Algorithmic Trading:** uso de algoritmos para implementar e executar estratégias.

**Backtesting:** avaliação histórica de uma estratégia.

**Paper Trading:** simulação de operações com dados de mercado, sem capital real.

**Walk-forward Testing:** avaliação temporal progressiva de modelos.

**Overfitting:** ajuste excessivo aos dados de treinamento, sem generalização adequada.

**Risk Management:** conjunto de regras e controles para limitar exposição e perdas.

# 19. O QUE NÃO FOI FEITO

Até este ponto da conversa:

- Nenhum nome definitivo foi estabelecido.
- Nenhum PRD foi escrito.
- Nenhuma especificação formal foi concluída.
- Nenhuma arquitetura foi aprovada.
- Nenhum repositório foi criado nesta conversa.
- Nenhum código foi implementado.
- Nenhuma API foi integrada.
- Nenhum dataset foi coletado.
- Nenhum modelo foi treinado.
- Nenhum backtest foi executado.
- Nenhum resultado financeiro foi medido.
- Nenhuma operação automatizada foi realizada.
- Nenhuma infraestrutura foi provisionada.
- Nenhum provedor comercial foi contratado.

As decisões apresentadas são conceituais e preliminares.

O trabalho está na fase de engenharia de requisitos e planejamento do produto.

# 20. INSTRUÇÕES PARA O AGENTE QUE RECEBERÁ ESTE CONTEXTO

Você está assumindo a continuidade de um projeto de engenharia de software e pesquisa quantitativa.

O usuário deseja construir um sistema de trading algorítmico com inteligência artificial, inicialmente voltado ao mercado de criptomoedas, com possibilidade futura de execução autônoma de ordens reais.

A proposta inicial envolve quatro conjuntos de dados de mercado:

1. Candles.
2. Volume.
3. Order Book.
4. Times & Trades.

Uma evolução futura prevê:

5. News & Macro Intelligence.

O usuário já discutiu análise técnica, scalping, execução algorítmica, machine learning, APIs, gerenciamento de risco e validação quantitativa.

**A decisão mais recente do usuário foi priorizar a documentação formal antes da implementação.**

Portanto, não comece escrevendo o robô.

O próximo trabalho deve ser:

**Elaborar o PRD — Product Requirements Document.**

Esse documento deverá transformar a visão apresentada em objetivos, requisitos de produto, escopo do MVP, exclusões, riscos, premissas e critérios de sucesso.

Depois do PRD, avançar para requisitos técnicos, arquitetura, estratégia de IA, dados, motor de execução, testes e roadmap.

Ao trabalhar neste projeto:

- Não presuma lucratividade.
- Não prometa retornos financeiros.
- Diferencie fatos, hipóteses e decisões ainda pendentes.
- Priorize clareza arquitetural.
- Evite complexidade desnecessária.
- Considere limitações reais de dados, custos e latência.
- Utilize métodos de pesquisa e validação reproduzíveis.
- Preserve a separação entre IA, geração de sinais, gestão de risco e execução.
- Mantenha a segurança operacional como requisito fundamental.
- Não integre contas com dinheiro real sem autorização explícita.
- Priorize construção incremental e documentação consistente.
- Discuta escolhas técnicas e seus trade-offs antes de fixá-las.

**Ponto exato de continuidade: iniciar a elaboração do PRD do projeto, preservando todas as decisões e hipóteses deste documento.**

---

**Fim do contexto de transferência — 23/10/2026.**
