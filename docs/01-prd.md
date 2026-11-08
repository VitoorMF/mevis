# 01 — Product Requirements Document (PRD)

| Campo | Valor |
|---|---|
| Projeto | **MEVIS** — Market Evidence Intelligence System |
| Documento | 01 — PRD |
| Versão | 0.19 — rascunho |
| Status | Decisões até DEC-40; aguardando confirmação da conta AWS e revisão final (§19) |
| Data | 23/10/2026 |
| Responsável pelo produto | *a preencher* |
| Fonte | [00 — Contexto inicial](00-contexto-inicial.md) |
| Documento seguinte | 02 — Software Requirements Specification (SRS) |

---

## 0. Como ler este documento

### 0.1 Classificação das afirmações

Para separar o que sabemos do que supomos, as afirmações relevantes recebem uma etiqueta:

| Etiqueta | Significado |
|---|---|
| **[DECISÃO]** | Escolha já acordada com o responsável pelo produto. Só muda por revisão formal. |
| **[PROPOSTA]** | Recomendação deste documento. Vale como direção até ser aprovada ou rejeitada. |
| **[HIPÓTESE]** | Afirmação a ser testada experimentalmente. Pode ser refutada. |
| **[A VERIFICAR]** | Fato externo (API, mercado, custos) que precisa ser confirmado na data da implementação. |
| **[PENDENTE]** | Questão em aberto, registrada com identificador `PD-xx` em §17.2. |

### 0.2 Identificadores

| Prefixo | Uso | Detalhado em |
|---|---|---|
| `OBJ-xx` | Objetivos do produto | §5 |
| `H-xx` | Hipóteses de pesquisa | §4 |
| `US-xx` | Histórias de usuário | §6.4 |
| `PP-xx` | Princípios do produto | §7 |
| `F-xx` | Funcionalidades | §9 |
| `EX-xx` | Exclusões de escopo | §10 |
| `PR-xx` / `RS-xx` | Premissas / Restrições | §11 / §12 |
| `IS-xx` | Indicadores de sucesso | §13 |
| `CA-xx` | Critérios de aceitação | §14 |
| `R-xx` | Riscos | §15 |
| `DEC-xx` / `PD-xx` | Decisões tomadas / pendentes | §17 |
| `B0`–`B8` | Baselines e ablações | Apêndice B |

No SRS (doc 02), cada requisito `RF-xxx`, `RNF-xxx` e `RN-xxx` deverá apontar para uma ou mais funcionalidades `F-xx` deste documento.

### 0.3 Prioridade (MoSCoW)

**Must** — sem isso a versão não é entregue. **Should** — importante, mas a versão pode ser aceita sem. **Could** — desejável se houver tempo. **Won't (MVP)** — fora do MVP, previsto para depois.

---

## 1. Visão do produto

Um sistema autônomo de trading algorítmico que coleta continuamente dados de mercado, transforma-os em informação utilizável por modelos estatísticos, decide entre comprar, vender ou não operar sob a supervisão de um gerenciador de risco independente, executa operações (no MVP, apenas em simulação) e registra cada decisão de forma auditável.

Antes de ser um robô de operações, o produto é um **instrumento de pesquisa**. Sua primeira função é responder, com rigor e reprodutibilidade, se a combinação das informações de mercado disponíveis contém vantagem estatística líquida de custos. A execução autônoma é o objetivo de longo prazo; a evidência vem antes dela.

> **Declaração de visão:** descobrir, com evidência reprodutível, se uma decisão automatizada que combina preço, volume, fluxo de negócios e livro de ofertas supera alternativas simples depois de custos — e só então permitir que ela opere.

**[DECISÃO] Princípio central:** automatizar uma estratégia não a torna lucrativa. Nenhuma operação com capital real acontece antes de evidência estatística, e capital real está fora do escopo do MVP.

---

## 2. Problema

### 2.1 Contexto

Day trading e scalping são modalidades legítimas e usadas profissionalmente, mas obter rentabilidade consistente é raro. Pesquisas com traders individuais brasileiros — por exemplo, Chague, De-Losso e Giovannetti, *Day Trading for a Living?* (2019), sobre minicontratos de índice na B3 — mostram que a grande maioria dos que persistiram na atividade perdeu dinheiro.

O projeto nasceu da experiência do responsável pelo produto acompanhando analistas da Toro Investimentos e replicando manualmente suas entradas e saídas, baseadas sobretudo em gráficos de candles. Nessa experiência não havia como saber se os padrões usados (rompimento, reteste, suporte e resistência) tinham vantagem estatística ou se os resultados vinham de sorte e de viés de seleção.

### 2.2 Problemas a resolver

| ID | Problema |
|---|---|
| P-01 | Decisões de compra e venda costumam se basear em padrões visuais sem validação estatística, sem como distinguir habilidade, sorte e viés. |
| P-02 | Um humano não consegue processar, de forma simultânea, consistente e contínua, candles, volume, livro de ofertas e fluxo de negócios em tempo real. |
| P-03 | Não existe, para o responsável pelo produto, um ambiente que meça o ganho incremental de cada fonte de informação, líquido de custos e fora da amostra. |
| P-04 | Automatizar uma estratégia sem validação apenas amplifica seus erros; a execução automática exige controles de risco que não dependam do próprio modelo. |

### 2.3 Pergunta de pesquisa

> Um modelo que combina candles, volume, fluxo de negócios e livro de ofertas produz decisões com expectativa positiva, líquida de custos e ajustada ao risco, fora da amostra, e superior à de estratégias que usam apenas parte dessas informações?

A resposta pode ser "não". Um "não" bem demonstrado é um resultado válido do MVP (ver PP-08).

---

## 3. Justificativa

1. **Valor técnico independente do lucro.** Um pipeline de dados em tempo real, pesquisa de ML com séries temporais e um motor de execução com gestão de risco formam um conjunto de competências de engenharia de software, dados e finanças quantitativas que tem valor mesmo que a estratégia não seja lucrativa.
2. **Hipótese falsificável.** O projeto transforma uma intuição ("uma IA consegue fazer as análises ao mesmo tempo") em hipóteses mensuráveis com critério de refutação (§4).
3. **Custo de entrada baixo.** O mercado de criptomoedas oferece dados públicos gratuitos, negociação contínua e ambiente de testes, enquanto o mercado brasileiro exige licenciamento, custos e integração Windows x86-64 incompatível com o ambiente atual (ver RS-01).
4. **Infraestrutura reaproveitável.** Se a hipótese central for refutada, a mesma infraestrutura serve para testar outras hipóteses, ativos e mercados.
5. **Base para o objetivo de longo prazo.** A execução autônoma com capital real, se um dia for aprovada, dependerá exatamente da evidência e dos controles construídos no MVP.

---

## 4. Hipóteses de pesquisa

Todas as hipóteses são **[HIPÓTESE]** e podem ser refutadas. As comparações usam os baselines do Apêndice B.

| ID | Hipótese | Evidência que a confirmaria | Versão |
|---|---|---|---|
| H-01 | Variáveis derivadas de candles (indicadores técnicos) têm capacidade preditiva que se converte em expectativa líquida superior aos baselines ingênuos. | B4 supera B0, B1 e B2 fora da amostra, depois de custos. | V0.2 |
| H-02 | Acrescentar variáveis de volume gera ganho incremental sobre candles. | B5 supera B4 na mesma janela, regras e custos. | V0.2 |
| H-03 | Acrescentar fluxo de negócios e, depois, livro de ofertas gera ganho incremental sobre candles e volume. | B6 supera B5 e B7 supera B6 na mesma janela. | V0.2 |
| H-04 | **(central)** O modelo com os quatro conjuntos de dados produz resultado líquido positivo fora da amostra, ajustado ao risco e superior aos baselines. | B7 atende aos critérios pré-registrados de G2 (§8.4). | V0.2 |
| H-05 | O desempenho observado em backtest se mantém em paper trading com dados ao vivo, dentro de uma degradação tolerada. | Diferença paper × backtest no mesmo período dentro da faixa definida no doc 07. | V0.3 |
| H-06 | Contexto macroeconômico e de notícias, usado como filtro de risco, melhora o resultado ajustado ao risco. | B8 supera B7 em métricas ajustadas ao risco. | V0.4 (pós-MVP) |

**Observação:** candles e volume são derivados dos negócios executados, portanto não são fontes independentes. As ablações medem o valor incremental de cada *representação* da informação, não a independência das fontes.

---

## 5. Objetivos

| ID | Tipo | Objetivo | Medido por |
|---|---|---|---|
| OBJ-01 | Técnico | Construir um pipeline confiável de coleta, validação e armazenamento dos quatro conjuntos de dados de mercado de BTC/USDT. | IS-01 a IS-04 |
| OBJ-02 | Científico | Testar H-01 a H-04 com metodologia reprodutível, sem vazamento de informação e com comparação contra baselines. | IS-09 a IS-13 |
| OBJ-03 | Técnico | Operar de forma autônoma em paper trading, com gestão de risco independente do modelo e trilha de auditoria completa. | IS-05 a IS-08 |
| OBJ-04 | Financeiro experimental | Determinar se existe vantagem estatística líquida de custos, fora da amostra, e quanto cada fonte de informação contribui. | IS-14 a IS-19 |
| OBJ-05 | Aprendizado | Compreender, na prática, como sistemas profissionais coletam dados, decidem, controlam risco e executam. | Documentação e relatórios produzidos |
| OBJ-06 | Pós-MVP | Avaliar o ganho incremental de contexto macroeconômico, geopolítico e de notícias (H-06). | Relatório de V0.4 |

### 5.1 Não objetivos

- Maximizar lucro durante o MVP.
- Prometer ou presumir qualquer retorno financeiro.
- Competir em latência com participantes de alta frequência.
- Construir um produto para terceiros (ver EX-07).

---

## 6. Público-alvo

### 6.1 Usuário primário

O próprio responsável pelo produto, atuando em três papéis:

| Persona | O que faz | Do que precisa |
|---|---|---|
| **Pesquisador quantitativo** | Formula hipóteses, monta datasets, treina modelos, conduz experimentos e interpreta resultados. | Dados confiáveis e versionados; experimentos reprodutíveis; comparações justas; relatórios padronizados. |
| **Desenvolvedor** | Constrói, testa e mantém o sistema. | Componentes com responsabilidades separadas; testes automatizados; documentação; ambiente local em macOS ARM64. |
| **Operador** | Supervisiona o paper trading e a coleta de dados. | Visão do estado (saúde dos dados, posições, resultado); alertas; kill switch; trilha de auditoria. |

### 6.2 Partes interessadas futuras (fora do MVP)

Possíveis parceiros, investidores ou avaliadores do trabalho técnico (portfólio, academia). Implicação para o MVP: documentação e relatórios devem ser apresentáveis e honestos, incluindo resultados negativos.

### 6.3 Não são público-alvo do MVP

Terceiros que receberiam sinais ou teriam recursos administrados pelo sistema. Isso tem implicações regulatórias (ver RS-07, EX-07).

### 6.4 Histórias de usuário principais

| ID | História | Funcionalidades |
|---|---|---|
| US-01 | Como **pesquisador**, quero dados de candles, negócios e livro de ofertas coletados continuamente e com lacunas identificadas, para confiar nos datasets que construo. | F-01 a F-11 |
| US-02 | Como **pesquisador**, quero que toda feature use apenas informação disponível no instante da decisão, para que os resultados não sejam inflados por vazamento. | F-12, F-13 |
| US-03 | Como **pesquisador**, quero comparar o modelo com baselines e com versões que usam menos fontes de dados, para medir o ganho de cada fonte. | F-14, F-18 |
| US-04 | Como **pesquisador**, quero reexecutar qualquer experimento e obter o mesmo resultado, para confiar nas conclusões e apresentá-las. | F-19 |
| US-05 | Como **pesquisador**, quero backtests com taxas, spread, slippage e latência, para não confundir vantagem bruta com lucro. | F-17 |
| US-06 | Como **operador**, quero que o sistema pare de abrir posições quando os dados estiverem atrasados ou inconsistentes, para que falhas técnicas não virem decisões ruins. | F-23, F-24, F-28 |
| US-07 | Como **operador**, quero um kill switch que interrompa tudo imediatamente, para retomar o controle em qualquer situação. | F-24 |
| US-08 | Como **operador**, quero entender por que cada decisão foi tomada (inclusive as de não operar), para auditar o comportamento do sistema. | F-29 |
| US-09 | Como **desenvolvedor**, quero que pesquisa e operação usem o mesmo código de features e de decisão, para que o paper trading reflita o que foi testado. | F-12, F-22, F-25 |

---

## 7. Princípios do produto

Os princípios orientam decisões não previstas neste documento.

| ID | Princípio | Consequência prática |
|---|---|---|
| PP-01 | **Evidência antes de capital.** | Capital real só é discutido depois de G3 e fora do MVP (G4). |
| PP-02 | **Risco independente da IA.** | O Risk Manager é determinístico, tem poder de veto e não pode ser contornado pelo modelo ou pelo Signal Engine. |
| PP-03 | **Na dúvida, não operar.** | Dados atrasados, lacunas, divergência de posição ou erro interno bloqueiam novas posições (fail-safe). |
| PP-04 | **Informação point-in-time.** | Toda decisão, em pesquisa ou ao vivo, usa apenas o que estava disponível naquele instante, considerando o horário de recebimento. |
| PP-05 | **Paridade pesquisa–operação.** | Features, sinais, regras de risco e simulação de execução usam o mesmo código no backtest e no paper trading. |
| PP-06 | **Auditabilidade total.** | Toda decisão é registrada com entradas, versão do modelo, saída, avaliação de risco e motivo. |
| PP-07 | **Simplicidade incremental.** | Nenhuma tecnologia complexa (microservices, Kubernetes, filas distribuídas) sem necessidade demonstrada. |
| PP-08 | **Resultado negativo é resultado.** | O MVP é aceito pela qualidade da resposta à pergunta de pesquisa, não por ser lucrativo. |

---

## 8. Escopo do MVP

### 8.1 Definição

**[PROPOSTA]** O MVP corresponde às versões **V0.1 + V0.2 + V0.3** do roadmap preliminar. A V0.4 (Macro Intelligence) é a primeira evolução após o MVP **[DECISÃO]**.

Ao final do MVP, o produto:

1. coleta e armazena continuamente candles, negócios e livro de ofertas de BTC/USDT na Binance;
2. conduz pesquisa quantitativa reprodutível sobre esses dados, comparando modelos e baselines depois de custos;
3. opera autonomamente em paper trading com dados ao vivo, sob gestão de risco independente, sem capital real.

### 8.2 Parâmetros do MVP

| Dimensão | Valor no MVP | Status |
|---|---|---|
| Mercado | Criptomoedas, mercado **spot** | Cripto: [DECISÃO]. Spot: [PROPOSTA] — decorre de "sem alavancagem" e dos endpoints spot discutidos. Spot × futuros no longo prazo: PD-11. |
| Ativo | BTC/USDT | [DECISÃO] (sugestão inicial) |
| Fonte de dados | APIs públicas da Binance (REST e WebSocket) | [DECISÃO] |
| Conjuntos de dados | Candles, volume, negócios executados (times & trades), livro de ofertas | [DECISÃO] |
| Direção das operações | Comprado ou fora (long/flat) | [DECISÃO] DEC-14 |
| Horizonte operacional | Intraday, em minutos; candle base de 1 minuto | [DECISÃO] DEC-15. Horizontes exatos: doc 04 |
| Execução | Simulada | [DECISÃO]. Forma (simulador local × testnet): PD-08 |
| Capital real | Não | [DECISÃO] |
| Alavancagem e margem | Não | [DECISÃO] |
| Chaves de API de conta real | Não existem no sistema | [PROPOSTA] — consequência de "sem capital real" |
| Linguagem | Python | [DECISÃO] |
| Modelos iniciais | Baselines simples e XGBoost | [DECISÃO] (sugestão inicial) |
| Interface de operação | Linha de comando, relatórios e logs | [PENDENTE] PD-10 |
| Ambiente de execução | MacBook para desenvolvimento; coleta contínua e paper trading 24/7 na AWS (EC2) | [DECISÃO] DEC-17; região Ohio, us-east-2 (DEC-26); instância e divisão MacBook × AWS: doc 03 |
| Orçamento | Créditos AWS (US$ 180) e Free Tier, sem despesas adicionais; depois dos créditos, teto de R$ 100/mês | [DECISÃO] DEC-16 |

### 8.3 Escopo por versão

#### V0.1 — Data Engine

- **Objetivo:** infraestrutura confiável de coleta, validação e armazenamento.
- **Funcionalidades:** F-01 a F-11.
- **Entregável:** coletor contínuo de BTC/USDT, base de dados brutos e normalizados, relatório de qualidade dos dados.
- **Observação crítica:** o histórico do livro de ofertas spot provavelmente não pode ser obtido retroativamente (PR-04). Cada dia sem coleta é um dia a menos de dados para testar H-03. Por isso a V0.1 — e os documentos de que ela depende — deve ser priorizada (ver §19).

#### V0.2 — AI & Research Engine

- **Objetivo:** testar H-01 a H-04 de forma reprodutível.
- **Funcionalidades:** F-12 a F-21.
- **Entregável:** biblioteca de features, datasets versionados, backtester com custos, experimentos de ablação e relatório de conclusão com o status de cada hipótese.
- **Observação:** experimentos que usam apenas candles e negócios podem começar com dados históricos públicos (PR-04) enquanto a coleta do livro acumula dados. A comparação entre B4–B7, porém, precisa usar a mesma janela temporal, limitada ao período em que há dados de livro.

#### V0.3 — Trading Engine (paper trading)

- **Objetivo:** operar de forma autônoma em simulação, com segurança e auditabilidade.
- **Funcionalidades:** F-22 a F-32.
- **Entregável:** sistema que opera em paper trading com dados ao vivo, com risco independente, kill switch, diário de decisões e relatório paper × backtest.
- **[PROPOSTA] (PD-09):** a construção do motor de trading não depende do resultado de V0.2. O que depende de G2 é **qual estratégia** opera: se nenhum modelo passar em G2, o motor é validado com um baseline (por exemplo, B3) e o relatório registra que não há modelo candidato.

#### V0.4 — Macro Intelligence (pós-MVP)

- **Objetivo:** testar H-06.
- **Funcionalidades:** F-33 a F-36.
- **Direção:** contexto macro e notícias entram primeiro como filtro de risco (suspender novas operações, reduzir exposição), não como preditor de direção. O processamento de notícias não pode bloquear o processamento de dados de mercado.

### 8.4 Portões de decisão (gates)

| Gate | Libera | Condição resumida | Critérios detalhados em |
|---|---|---|---|
| **G1 — Dados aprovados** | Uso dos dados coletados na V0.2 | CA-01 a CA-06 atendidos. **Aprovado em 31/10/2026 (DEC-30).** | Doc 06, doc 07 |
| **G2 — Modelo candidato** | Um modelo específico passa a operar em paper trading | Critérios financeiros experimentais **pré-registrados** atendidos (§13.3). | Doc 04, doc 07 |
| **G3 — Fim do MVP** | Encerramento do MVP e planejamento das próximas versões | CA-01 a CA-20 atendidos. | Doc 07 |
| **G4 — Capital real** | **Fora do escopo do MVP** | Exigiria documento próprio, revisão de segurança, limites de capital e autorização explícita do responsável pelo produto. | — |

**[PROPOSTA] Pré-registro:** os critérios de G2 devem ser escritos e versionados **antes** de qualquer avaliação no conjunto de holdout. Mudar critérios depois de ver os resultados invalida a avaliação.

---

## 9. Funcionalidades

### 9.1 Ciclo de decisão (visão de produto)

```text
dados de mercado ──► validação ──► features ──► modelo ──► sinal ──► RISK MANAGER ──► execução ──► posição
      │                  │                                              │  (veto)            │
      └──────────────────┴────────────► diário de decisões ◄────────────┴────────────────────┘
```

- O **sinal** (BUY, SELL, NO TRADE) é uma intenção, não uma ordem.
- O **Risk Manager** pode aprovar, reduzir ou vetar; nunca pode ser contornado.
- Toda passagem por esse ciclo — inclusive a decisão de não operar — gera um registro no diário de decisões.
- A arquitetura que implementa esse ciclo será definida no doc 03; este diagrama descreve apenas o comportamento esperado do produto.

### 9.2 V0.1 — Data Engine

| ID | Funcionalidade | Descrição | Prioridade |
|---|---|---|---|
| F-01 | Coleta de candles | Receber candles de BTC/USDT em tempo real (intervalo base a definir no doc 06; 1 minuto como ponto de partida). | Must |
| F-02 | Coleta de negócios executados | Receber cada negócio com preço, quantidade, horário, identificador e lado agressor. | Must |
| F-03 | Coleta do livro de ofertas | Receber snapshots parciais (20 níveis a cada 100 ms) e o stream diferencial, gravado na camada bruta com snapshots completos periódicos (DEC-18). | Must |
| F-04 | Livro de ofertas local completo | Reconstruir o livro completo a partir de snapshot e atualizações incrementais, com verificação de sequência. | Could (DEC-18) |
| F-05 | Carga de histórico (backfill) | Importar histórico público de candles e negócios para um período configurável. | Must |
| F-06 | Resiliência de conexão | Detectar desconexões, reconectar automaticamente e registrar cada lacuna resultante. | Must |
| F-07 | Normalização e duplo timestamp | Padronizar estruturas e registrar o horário do evento (exchange) e o horário de recebimento (local). | Must |
| F-08 | Persistência em camadas | Manter dados brutos imutáveis, tal como recebidos, e uma camada normalizada derivada deles e reconstruível. | Must |
| F-09 | Validação de qualidade | Verificar completude, duplicatas, ordem e consistência; gerar relatório de qualidade sob demanda. | Must |
| F-10 | Monitoramento da ingestão | Expor métricas de saúde (mensagens por segundo, atraso, lacunas, uso de disco) e alertar em falhas. | Should |
| F-11 | Agregações derivadas | Gerar agregações de volume e reconstruir candles a partir dos negócios para conferir os candles recebidos. | Should |

### 9.3 V0.2 — AI & Research Engine

| ID | Funcionalidade | Descrição | Prioridade |
|---|---|---|---|
| F-12 | Biblioteca de features point-in-time | Calcular features técnicas, de volume, de fluxo de negócios e de livro de ofertas usando apenas dados disponíveis no instante da decisão. Mesmo código para pesquisa e operação. | Must |
| F-13 | Datasets versionados | Construir datasets (features e targets) identificados por versão e reconstruíveis a partir dos dados brutos. | Must |
| F-14 | Baselines | Implementar os baselines B0–B3 do Apêndice B. | Must |
| F-15 | Treinamento de modelos | Treinar modelos simples (lineares/logísticos) e XGBoost. | Must |
| F-16 | Validação temporal | Validação walk-forward com separação temporal (purga/embargo entre treino e teste) e holdout final reservado. | Must |
| F-17 | Backtester com custos | Simular decisões com taxas, spread, slippage e latência configuráveis, com análise de sensibilidade aos custos. | Must |
| F-18 | Experimentos de ablação | Avaliar B4–B7 na mesma janela, com as mesmas regras e custos, para medir o ganho de cada conjunto de dados. | Must |
| F-19 | Registro de experimentos | Registrar versão dos dados, código, configuração, semente aleatória e métricas de **todos** os experimentos, inclusive os descartados. | Must |
| F-20 | Relatórios padronizados | Gerar relatórios comparáveis entre experimentos (métricas financeiras, estatísticas e por subperíodo). | Should |
| F-21 | Modelos de redes neurais | Experimentar modelos em PyTorch, somente depois dos modelos simples. | Could |

### 9.4 V0.3 — Trading Engine

| ID | Funcionalidade | Descrição | Prioridade |
|---|---|---|---|
| F-22 | Signal Engine | Converter saídas do modelo em sinais BUY, SELL ou NO TRADE, com intensidade/confiança e justificativa. | Must |
| F-23 | Risk Manager | Componente determinístico e independente do modelo que aprova, reduz ou veta cada intenção, aplicando limites de exposição, posição, perda, frequência, liquidez, custo e qualidade dos dados. Valores definidos no doc 05. | Must |
| F-24 | Kill switch | Interrupção manual e automática que bloqueia novas ordens, cancela ordens abertas e, conforme política do doc 05, encerra posições. | Must |
| F-25 | Execution Engine com simulador | Transformar decisões aprovadas em ordens simuladas sobre dados reais de mercado, com modelo de preenchimento, preenchimento parcial, rejeição e custos. | Must |
| F-26 | Adaptador para a Binance Spot Testnet | Validar o ciclo de vida real de ordens (envio, confirmação, cancelamento, rejeição) contra a API de testes. | Should |
| F-27 | Gestão de posições e reconciliação | Manter posição, ordens e resultado; reconciliar com o ambiente de execução; impedir ordens duplicadas. | Must |
| F-28 | Paper trading contínuo | Operar autonomamente com dados ao vivo, sobrevivendo a reinícios sem perder ou duplicar estado. | Must |
| F-29 | Diário de decisões | Registrar cada decisão (inclusive NO TRADE) com entradas, versão do modelo, saída, avaliação de risco, ordem resultante e motivo. | Must |
| F-30 | Monitoramento operacional e alertas | Acompanhar saúde dos dados, latência, posições, resultado, violações de risco e erros, com alertas. | Must |
| F-31 | Shadow mode | Gerar decisões em ambiente de produção sem enviar ordens, para comparar previsões e resultados. | Could |
| F-32 | Painel visual | Painel para acompanhar estado e resultados. | Could (PD-10) |

### 9.5 V0.4 — Macro Intelligence (pós-MVP)

| ID | Funcionalidade | Prioridade |
|---|---|---|
| F-33 | Ingestão de calendário econômico, comunicados oficiais e notícias, preservando o horário real de disponibilidade. | Won't (MVP) |
| F-34 | Classificação de eventos com NLP/LLM (tipo, relevância, novidade, incerteza, ativos afetados). | Won't (MVP) |
| F-35 | Sinal contextual de risco consumido pelo Risk Manager. | Won't (MVP) |
| F-36 | Ablação com e sem contexto macro (B8 × B7). | Won't (MVP) |

### 9.6 Requisitos transversais de produto

Detalhados como requisitos não funcionais no SRS.

| Tema | Requisito de produto |
|---|---|
| Segurança | Nenhuma credencial de conta real no sistema durante o MVP. Credenciais de testnet fora do repositório e dos logs. |
| Reprodutibilidade | Todo resultado apresentado precisa ser reproduzível a partir de dados brutos, código versionado e configuração. |
| Observabilidade | O estado do sistema (dados, decisões, posições, erros) deve ser consultável sem depurar código. |
| Resiliência | Quedas de rede e reinícios são operação normal, não exceção; o sistema se recupera sem intervenção e sem corromper estado. |
| Portabilidade | Executar em macOS ARM64 (desenvolvimento) e em Linux na AWS (coleta e paper trading, DEC-17). |
| Custo | Consumir primeiro os créditos AWS e o Free Tier, sem despesas adicionais; depois dos créditos, teto de R$ 100/mês (DEC-16). Nenhum recurso AWS é criado sem estimativa de custo aprovada. |
| Armazenamento | Compressão e política de retenção definidas antes de a coleta contínua começar (RS-01). |

---

## 10. Exclusões de escopo

| ID | Fora do escopo do MVP | Observação |
|---|---|---|
| EX-01 | Operações com capital real e qualquer chave de API de conta real. | [DECISÃO]. Exigiria G4. |
| EX-02 | Alavancagem, margem, futuros, opções e posições vendidas reais. | [DECISÃO] para alavancagem; demais por consequência do mercado spot. |
| EX-03 | Execução em múltiplos ativos. | Pesquisa exploratória com outros pares, como teste de robustez, pode ser considerada no doc 04. |
| EX-04 | Mercados brasileiros (WIN, WDO via Nelogica/ProfitDLL ou B3) e ações americanas (Alpaca). | Avaliar após o MVP. |
| EX-05 | News & Macro Intelligence. | [DECISÃO] — V0.4. |
| EX-06 | Estratégias cuja vantagem depende de latência abaixo de 1 segundo (alta frequência, market making, arbitragem de latência). | Consequência de DEC-15; ver RS-02. |
| EX-07 | Produto para terceiros: venda de sinais, gestão de recursos de terceiros, uso multiusuário, SaaS. | Exigiria análise regulatória (RS-07). |
| EX-08 | Aplicativo móvel ou interface web completa. | Painel simples é Could (F-32). |
| EX-09 | Infraestrutura distribuída complexa como pré-requisito (microservices, Kubernetes). | A arquitetura será escolhida no doc 03 (PP-07). |
| EX-10 | Apuração e otimização tributária. | Impostos podem entrar como custo de referência na análise financeira, sem implementação fiscal. |
| EX-11 | Treinamento de redes neurais grandes ou de LLMs próprios. | — |

---

## 11. Premissas

Premissas são condições tomadas como verdadeiras para planejar. Se uma se mostrar falsa, o PRD deve ser revisto.

| ID | Premissa | Status |
|---|---|---|
| PR-01 | As APIs públicas de dados de mercado spot da Binance são acessíveis a partir do Brasil, sem autenticação, dentro de limites de uso compatíveis com a coleta contínua de um par. | Acesso do Brasil verificado em 23/10/2026 (doc 06 MD-01 a MD-03); acesso a partir da região AWS: doc 03 |
| PR-02 | A Binance Spot Testnet está disponível para validar o ciclo de vida de ordens. Seus preços e sua liquidez **não** representam o mercado real e não servem para medir desempenho. | [A VERIFICAR] doc 05/06 |
| PR-03 | BTC/USDT tem liquidez suficiente para que ordens simuladas pequenas tenham impacto de mercado desprezível. O modelo de slippage será conservador e a premissa será monitorada. | [HIPÓTESE] operacional |
| PR-04 | O histórico público da Binance oferece candles e negócios spot para download (REST e arquivos em data.binance.vision), mas **não** snapshots históricos do livro de ofertas spot. O livro precisa ser coletado pelo próprio sistema. | Verificado em 23/10/2026 (SRS FV-12) |
| PR-05 | As conexões WebSocket da Binance são encerradas periodicamente pelo servidor (a documentação indica validade de 24 horas por conexão) e podem cair a qualquer momento. | Verificado em 23/10/2026 (SRS FV-01) |
| PR-06 | Cada negócio publicado pela Binance informa se o comprador era o lado passivo (maker), o que permite classificar a agressão sem inferência. Os identificadores de negócio são sequenciais por par, o que permite detectar lacunas. | Indicador maker verificado (SRS FV-05); ids consecutivos a verificar na coleta (SRS RF-151) |
| PR-07 | A taxa padrão de negociação spot é da ordem de 0,1% por lado (cerca de 0,2% por operação completa), antes de descontos. Movimentos de preço no horizonte escolhido precisam superar esse custo somado a spread e slippage. | [A VERIFICAR] doc 04/05 |
| PR-08 | O projeto é conduzido por um único desenvolvedor, com dedicação parcial. O roadmap não tem datas fixas. | [DECISÃO] implícita |
| PR-09 | Os créditos (US$ 180) estão numa conta nova, no plano gratuito, que expira em 12/04/2027. Os créditos valem 12 meses a partir da criação da conta (~10/2027) e cobrem EC2, VPC, S3 e CloudWatch. | Plano e saldo informados pelo responsável em 23/10/2026; validade dos créditos [A VERIFICAR] (doc 03 §2.2) |
| PR-10 | O relógio do host de coleta é sincronizado; a diferença entre o horário da exchange e o local será medida e registrada. | **Falsa para o MacBook:** relógio atrasado ~0,4 s em 23/10/2026 (doc 06 MD-14). Na AWS: doc 03 |
| PR-11 | A conta configurada hoje no AWS CLI do MacBook (724772086347, criada em 07/09/2024, no modelo antigo de Free Tier, com recursos de outros projetos) **não é** a conta dos créditos. O projeto usa apenas a conta nova (doc 03 ADR-09). | Verificado em 23/10/2026 (consulta somente leitura); corrige a redação da v0.2 |
| PR-12 | A Binance bloqueia IPs dos EUA em `api.binance.com` e `stream.binance.com` (HTTP 451), mas os endpoints só de dados de mercado (`data-api.binance.vision`, `data-stream.binance.vision`) e os arquivos públicos (`data.binance.vision`) respondem a partir da us-east-2. | Medido no CloudShell em 23/10/2026 (doc 06 MD-16 a MD-21) |

---

## 12. Restrições

| ID | Restrição | Implicações |
|---|---|---|
| RS-01 | **Hardware:** MacBook Air M2, 16 GB de RAM, 256 GB de armazenamento, macOS ARM64. | (a) **Armazenamento:** só a profundidade parcial do livro a cada 100 ms gera cerca de 864 mil mensagens por dia — cerca de 1 GB/dia em JSON bruto. Medido em 23/10/2026: todos os canais coletados somam ~2,2 GB/dia brutos e ~170 MB/dia comprimidos (doc 06 §2.3). Compressão e retenção são obrigatórias. (b) **Disponibilidade:** notebook entra em repouso, muda de rede e não é servidor 24/7; por isso a coleta contínua e o paper trading rodarão na AWS (DEC-17). (c) **Compatibilidade:** dependências precisam suportar ARM64 (exclui uso nativo da ProfitDLL). |
| RS-02 | **Latência:** distância entre o ponto de coleta (MacBook no Brasil ou região AWS a definir no doc 03) e os servidores da exchange. | Latência de dezenas a centenas de milissegundos [A VERIFICAR, medir na V0.1]. Estratégias que dependem de velocidade sub-segundo não são competitivas (EX-06). |
| RS-03 | **Limites e termos da Binance:** limites de requisição, de conexões e de streams por conexão; termos de uso dos dados. | Coleta e backfill devem respeitar limites; termos precisam ser revisados no doc 06. |
| RS-04 | **Mercado spot sem margem.** | Não é possível ficar vendido. No MVP, SELL significa encerrar ou reduzir uma posição comprada (DEC-14). |
| RS-05 | **Python** como linguagem principal. | Desempenho suficiente para decisões na escala de segundos a minutos; inadequado para microssegundos. Coerente com EX-06. |
| RS-06 | **Segurança operacional.** | Nenhuma credencial de conta real; o modelo não tem acesso direto à execução — só por meio do Risk Manager. |
| RS-07 | **Regulatória.** | Uso estritamente pessoal. Oferecer sinais ou administrar recursos de terceiros exigiria análise regulatória (CVM, Banco Central, regras de criptoativos) antes de qualquer iniciativa. |
| RS-08 | **Equipe de uma pessoa.** | O escopo de cada versão deve caber em um desenvolvedor; prioridades MoSCoW são obrigatórias. |
| RS-09 | **Orçamento:** créditos AWS de US$ 180 e Free Tier, sem despesas adicionais enquanto houver créditos; depois, teto de R$ 100/mês (DEC-16). | O teto equivale a cerca de US$ 20/mês (câmbio de ~R$ 5,0). Em us-east-2 (DEC-26), depois dos benefícios gratuitos, t4g.micro custa ~US$ 13/mês e t4g.small ~US$ 19/mês (no limite), incluindo IPv4, disco e S3. Confirmação no CP-3 (DEC-23; doc 03 §10). |

---

## 13. Indicadores de sucesso

O sucesso é avaliado em três camadas. As metas marcadas **[PROPOSTA]** são valores iniciais para discussão e serão confirmadas nos docs 04, 05, 06 e 07.

### 13.1 Sucesso técnico

| ID | Indicador | Meta |
|---|---|---|
| IS-01 | Completude dos dados: proporção de minutos com candle, de intervalos de negócios sem lacuna de identificador e do tempo com livro atualizado. | ≥ 99,5% em janela de 7 dias contínuos; 100% das lacunas detectadas e registradas. [DECISÃO] DEC-30 |
| IS-02 | Tempo de recuperação após queda de conexão. | p95 ≤ 30 s até a retomada do recebimento. [DECISÃO] DEC-30 |
| IS-03 | Integridade: duplicatas na camada normalizada; divergência entre candles recebidos e candles reconstruídos dos negócios. | Zero duplicatas; divergências documentadas e explicadas. [PROPOSTA] |
| IS-04 | Estabilidade: operação contínua sem intervenção manual. | ≥ 7 dias na V0.1; ≥ 30 dias na V0.3. [PROPOSTA] |
| IS-05 | Conformidade de risco. | 100% das intenções avaliadas pelo Risk Manager; zero violações de limites. |
| IS-06 | Resposta do kill switch. | Novas ordens bloqueadas em ≤ 1 s após o acionamento. [PROPOSTA] |
| IS-07 | Ordens duplicadas. | Zero. |
| IS-08 | Auditabilidade. | 100% das decisões, inclusive NO TRADE, reconstruíveis a partir do diário. |

### 13.2 Sucesso científico

| ID | Indicador | Meta |
|---|---|---|
| IS-09 | Reprodutibilidade. | Reexecutar um experimento com os mesmos dados, código, configuração e semente produz as mesmas métricas (ou dentro de tolerância documentada). |
| IS-10 | Cobertura das ablações. | B0–B7 avaliados na mesma janela, com as mesmas regras e custos. |
| IS-11 | Ausência de vazamento. | 100% das features cobertas por testes automatizados de point-in-time. |
| IS-12 | Disciplina do holdout. | Holdout final avaliado uma única vez por modelo candidato, depois de congelados modelo e critérios. |
| IS-13 | Registro completo. | 100% dos experimentos registrados, inclusive os descartados, permitindo corrigir resultados por múltiplos testes. |

### 13.3 Sucesso financeiro experimental

Os critérios abaixo definem se um modelo passa em **G2**. Eles **não** definem se o MVP foi bem-sucedido (PP-08). Os valores finais de G2 estão pré-registrados no doc 04 §10.2 desde 26/10/2026 (DEC-27); a tabela abaixo é a referência que os originou.

| ID | Indicador | Referência inicial [PROPOSTA] |
|---|---|---|
| IS-14 | Expectativa líquida por operação, depois de todos os custos. | Positiva, com intervalo de confiança de 95% (por exemplo, por bootstrap) acima de zero. |
| IS-15 | Desempenho ajustado ao risco, corrigido para o número de experimentos realizados. | Sharpe corrigido (por exemplo, Deflated Sharpe Ratio) acima do limiar definido no doc 04. |
| IS-16 | Comparação com baselines. | Superar B0, B1, B2 e o melhor baseline de regras (B3) em métricas ajustadas ao risco. |
| IS-17 | Tamanho da amostra. | Número mínimo de operações fora da amostra que torne o intervalo de confiança informativo (ponto de partida: ≥ 200). |
| IS-18 | Robustez temporal. | Resultado positivo na maioria dos subperíodos de walk-forward, sem depender de um único período. |
| IS-19 | Risco. | Drawdown máximo, exposição e turnover dentro dos limites definidos no doc 05; resultado estável sob aumento razoável dos custos. |

Para H-05, o doc 07 definirá a degradação tolerada entre paper trading e backtest no mesmo período.

---

## 14. Critérios de aceitação

O MVP é aceito quando **todos** os critérios abaixo forem atendidos, **independentemente** de H-04 ter sido confirmada ou refutada.

### 14.1 V0.1 — Data Engine (G1)

| ID | Critério |
|---|---|
| CA-01 | O coletor opera por 7 dias contínuos recebendo candles, negócios e livro de ofertas de BTC/USDT, atendendo IS-01 e IS-02. |
| CA-02 | Toda queda de conexão é detectada, registrada e seguida de reconexão automática; as lacunas ficam marcadas nos dados. |
| CA-03 | Dados brutos e normalizados são persistidos com horário do evento e horário de recebimento, sem duplicatas na camada normalizada. |
| CA-04 | Histórico de candles e negócios pode ser importado para um período configurável e passa pela mesma validação dos dados ao vivo. |
| CA-05 | Um relatório de qualidade dos dados é gerado sob demanda para qualquer período. |
| CA-06 | O consumo de armazenamento por dia foi medido e uma política de compressão e retenção está definida e aplicada. |

### 14.2 V0.2 — AI & Research Engine (G2)

| ID | Critério |
|---|---|
| CA-07 | Datasets são versionados e reconstruíveis a partir dos dados brutos. |
| CA-08 | Todas as features passam nos testes automatizados de point-in-time. |
| CA-09 | Os baselines B0–B3 estão implementados e avaliados com o mesmo backtester e o mesmo modelo de custos dos modelos. |
| CA-10 | Ao menos um modelo XGBoost foi treinado e avaliado em walk-forward, com ablação B4–B7 documentada. |
| CA-11 | Todos os experimentos são reprodutíveis (IS-09) e estão registrados (IS-13). |
| CA-12 | O relatório de conclusão da V0.2 declara, para H-01 a H-04, se cada uma foi confirmada, refutada ou ficou inconclusiva, com a evidência correspondente. |

### 14.3 V0.3 — Trading Engine (G3)

| ID | Critério |
|---|---|
| CA-13 | O sistema opera em paper trading com dados ao vivo por pelo menos 30 dias [PROPOSTA] sem intervenção manual além de manutenção planejada. |
| CA-14 | 100% das intenções de ordem passam pelo Risk Manager; testes demonstram que modelo e Signal Engine não conseguem contornar os limites. |
| CA-15 | Kill switch manual e automático testados, incluindo acionamento por falha de dados. |
| CA-16 | Com dados ausentes, atrasados ou inconsistentes, o sistema deixa de abrir posições (PP-03) — comportamento coberto por testes. |
| CA-17 | Um reinício do processo não perde nem duplica posições ou ordens simuladas. |
| CA-18 | Qualquer decisão pode ser reconstruída a posteriori a partir do diário de decisões. |
| CA-19 | O ciclo de vida de ordens foi validado contra a Binance Spot Testnet (envio, confirmação, cancelamento e rejeição; preenchimento parcial quando reproduzível). |
| CA-20 | O relatório final do MVP compara o paper trading com o backtest do mesmo período e conclui sobre H-05. |

---

## 15. Riscos

| ID | Risco | Categoria | Impacto | Mitigação no produto |
|---|---|---|---|---|
| R-01 | Não existe vantagem estatística nos dados disponíveis. | Pesquisa / financeiro | Alto | Gates; resultado negativo aceito (PP-08); capital real fora do escopo. |
| R-02 | Overfitting e data snooping. | Pesquisa | Alto | Walk-forward; holdout usado uma vez; registro de todos os experimentos; correção para múltiplos testes; pré-registro. |
| R-03 | Vazamento de informação (data leakage, look-ahead bias). | Pesquisa | Alto | Features point-in-time com testes automatizados; uso do horário de recebimento (PP-04). |
| R-04 | Custos irreais no backtest. | Pesquisa | Alto | Modelo de custos conservador; análise de sensibilidade; confronto com paper trading. |
| R-05 | Histórico curto do livro de ofertas limita o teste de H-03. | Pesquisa | Alto | Começar a coleta o quanto antes (§19); ablações na mesma janela. |
| R-06 | Mudança de regime de mercado. | Pesquisa | Médio/alto | Avaliação por subperíodo; monitoramento de deriva do modelo. |
| R-07 | Desconexões, mensagens perdidas ou fora de ordem. | Técnico | Médio | Reconexão automática; detecção de lacunas por sequência; fail-safe (PP-03). |
| R-08 | Coleta interrompida (notebook em repouso, falha da instância, rede). | Técnico | Médio | Coleta 24/7 na AWS (DEC-17); reinício automático; monitoramento de disponibilidade com CloudWatch. |
| R-09 | Armazenamento esgotado. | Técnico | Médio | Compressão, retenção e monitoramento de disco (CA-06). |
| R-10 | Divergência entre pesquisa e operação (training–serving skew). | Técnico | Alto | Mesmo código de features e decisão (PP-05). |
| R-11 | Ordens duplicadas ou posição divergente. | Técnico | Médio | Identificadores idempotentes de ordem; reconciliação (F-27). |
| R-12 | Vazamento de credenciais. | Segurança | Alto | Sem chaves de conta real; segredos fora do repositório e dos logs. |
| R-13 | Mudanças nas APIs ou nos termos da Binance. | Externo | Médio | Integração isolada em um adaptador; revisão periódica da documentação oficial. |
| R-14 | Escopo grande demais para um desenvolvedor. | Projeto | Alto | Versões incrementais, MoSCoW e gates. |
| R-15 | Viés de confirmação do pesquisador. | Pesquisa | Alto | Pré-registro de critérios; registro de experimentos descartados. |
| R-16 | Exposição regulatória em um uso comercial futuro. | Regulatório | Médio | Uso comercial fora do escopo; análise antes de qualquer oferta (RS-07). |
| R-17 | Créditos AWS acabam ou expiram antes do previsto, ou o custo depois dos créditos passa do teto. | Financeiro / infraestrutura | Médio | Estimativas por fase e cenário (doc 03 §10); checkpoints CP-1 e CP-3; alertas do Budgets e do plano gratuito; tags de projeto. |
| R-18 | A Binance passar a bloquear também os endpoints `*.binance.vision` a partir dos EUA (os principais já são bloqueados — PR-12). | Externo | Alto | Coleta só pelos endpoints de dados de mercado; alarmes de silêncio e sinal de vida; plano B documentado: recursos avançados + Tóquio (DEC-26), com a região como variável do Terraform. |
| R-19 | Fim do plano gratuito (12/04/2027) sem passagem ao plano pago: a conta fecha e, após 90 dias, recursos e dados são apagados — inclusive o histórico insubstituível do livro. | Infraestrutura | Alto | Checkpoint CP-2 até 15/03/2027 (doc 03 §10.4); cópia mensal offline dos dados brutos. |
| R-20 | Recursos criados na conta errada (o perfil padrão do AWS CLI aponta para outra conta, sem créditos). | Infraestrutura / financeiro | Médio | Perfil dedicado; `allowed_account_ids` no Terraform; revisão do `plan` (doc 03 ADR-09). |

---

## 16. Dependências externas

| Dependência | Uso no MVP | Alternativa / observação |
|---|---|---|
| Binance — dados públicos (WebSocket e REST) | Coleta em tempo real e backfill. | Sem alternativa no MVP; integração isolada em adaptador. |
| Binance — arquivos históricos públicos | Backfill de candles e negócios (PR-04). | REST, com limites de requisição. |
| Binance Spot Testnet | Validação do ciclo de vida de ordens (F-26). | Simulador local para medir desempenho (PD-08). |
| Bibliotecas Python (pandas, NumPy, scikit-learn, XGBoost; PyTorch opcional) | Pesquisa e modelagem. | Verificar suporte ARM64. |
| AWS (VPC, EC2, S3, CloudWatch) | Hospedagem 24/7 da coleta e do paper trading; armazenamento e monitoramento. | Créditos e teto de custo (RS-09); arquitetura no doc 03. |
| Fontes macro e de notícias (GDELT, Trading Economics, FRED e outras) | Somente V0.4. | Latência, licenciamento e custo a avaliar. |

---

## 17. Decisões

### 17.1 Decisões estabelecidas

| ID | Tema | Decisão |
|---|---|---|
| DEC-01 | Natureza | Trading algorítmico com IA. |
| DEC-02 | Objetivo de longo prazo | Execução autônoma de operações. |
| DEC-03 | Mercado inicial | Criptomoedas. |
| DEC-04 | Ativo inicial | BTC/USDT (sugestão inicial). |
| DEC-05 | Fonte inicial | Binance. |
| DEC-06 | Linguagem principal | Python. |
| DEC-07 | Dados de mercado | Candles, volume, livro de ofertas e negócios executados. |
| DEC-08 | Contexto externo | News & Macro Intelligence na V0.4, após validar os dados de mercado; prioritariamente como filtro de risco. |
| DEC-09 | Modelagem inicial | XGBoost e baselines simples. |
| DEC-10 | Execução no MVP | Simulada. |
| DEC-11 | Capital real e alavancagem no MVP | Não. |
| DEC-12 | Risco | Controle independente da IA. |
| DEC-13 | Processo | Documentação antes da implementação, começando pelo PRD. |
| DEC-14 | Direção no MVP | Long/flat no spot: a execução só abre posição comprada ou fica fora; SELL encerra ou reduz a posição. O modelo pode estimar as duas direções para pesquisa e para decidir saídas. |
| DEC-15 | Horizonte | Intraday em minutos, com candle base de 1 minuto; horizontes candidatos (ordem de 5 a 60 minutos) comparados no doc 04. Scalping de segundos e swing ficam fora do MVP. |
| DEC-16 | Orçamento | Usar primeiro os US$ 180 em créditos AWS e o Free Tier, sem despesas adicionais; depois dos créditos, teto de **R$ 100/mês** (revisto em 23/10/2026; era R$ 50). Validade, elegibilidade dos serviços e consumo estimado dos créditos são verificados antes de definir a arquitetura. |
| DEC-17 | Hospedagem | MVP hospedado na AWS, preferencialmente com VPC, EC2, S3 e CloudWatch, operando 24/7. |
| DEC-18 | Livro de ofertas | Parcial top 20 a cada 100 ms (normalizado) + stream diferencial a cada 100 ms só na camada bruta, com snapshots REST completos periódicos. Reconstrução do livro completo continua Could (F-04). |
| DEC-19 | Negócios | Só o stream `trade` ao vivo; `aggTrades` importados dos arquivos públicos quando necessário. |
| DEC-20 | Armazenamento de dados de mercado | Arquivos, sem servidor de banco: bruta em JSONL + zstd por stream e hora; normalizada em Parquet por dataset e dia; leitura com DuckDB, Polars ou pandas. |
| DEC-21 | Região AWS | Tóquio (`ap-northeast-1`); Frankfurt como alternativa se a conectividade falhar. **Substituída por DEC-26.** |
| DEC-22 | Infraestrutura como código | Terraform. |
| DEC-23 | Plano depois dos créditos | t4g.micro sob demanda (~US$ 13/mês em us-east-2), dentro do teto de R$ 100 (DEC-16). O CP-3 confirma com consumo medido e avalia se a V0.3 exige t4g.small. |
| DEC-24 | Arquitetura | ADR-01 a ADR-12 do doc 03 aprovadas, incluindo alertas por e-mail via SNS (ADR-12). |
| DEC-25 | Nome do projeto | **MERIS — Market Evidence & Research Intelligence System**. O pacote Python continua `trader` por enquanto. **Substituída por DEC-29.** |
| DEC-26 | Região AWS | **Ohio (`us-east-2`)**. A conta do projeto (nova experiência da AWS) só aceita essa região, por SCP da organização gerenciada pela AWS. A coleta usa só os endpoints `*.binance.vision`, que respondem dos EUA. Tóquio exigiria passar ao plano pago e ativar os recursos avançados (irreversível) e fica como plano B. Substitui DEC-21. |
| DEC-27 | Método da V0.2 | Decisões PD-15 a PD-23 do doc 04 §1 (26/10/2026): horizontes de 15 e 60 min; alvo binário líquido de custos; holdout da trilha A de 23/07 a 22/10/2026; holdout da trilha B de 6 semanas coletadas depois do congelamento; latência assumida de 1 s para dados de arquivo; custo base de 0,10% por lado; critérios de G2 (IS-14 a IS-19) pré-registrados no doc 04 §10.2; candles de 2023–2025 como estudo de robustez; agregados por minuto de negócios numa EC2 dedicada e temporária (~US$ 0,60 por execução, créditos). |
| DEC-28 | Doc 04 aprovado | Doc 04 v1.0 aprovado em 26/10/2026: catálogo de features, baselines B0–B3, regressão logística e XGBoost com grade fixa, validação walk-forward e plano de trabalho da V0.2; minutos com falha descartam o ponto nas janelas de até 60 min e do alvo, e nas janelas de 240 min e 24 h exigem ≥ 95% de minutos válidos. |
| DEC-29 | Nome do projeto | **MEVIS — Market Evidence Intelligence System** (26/10/2026), antes do primeiro envio ao GitHub. O pacote Python continua `trader`; recursos da AWS e o endereço da página pública não mudam. Substitui DEC-25. |
| DEC-30 | G1 — dados aprovados | **Aprovado em 31/10/2026**, com CA-01 a CA-06 atendidos na semana de 24 a 30/10/2026 (doc 06 §8.6) e os limites de IS-01 e IS-02 confirmados. Ressalva: ~22 s de livro perdidos em trocas de versão, marcados como `missing` na máscara (doc 06 §9.2). |
| DEC-31 | Agregados por minuto (etapa 1 da V0.2) | Decisões PD-24 a PD-26 (06/11/2026): histórico na instância sob demanda e horas novas no pipeline horário do host; tamanho dos negócios guardado como histograma por minuto em faixas de ~19%; holdout da trilha A agregado só na avaliação final. |
| DEC-32 | Etapa 1 da V0.2 aprovada | Requisitos (SRS v0.8: RF-172 a RF-179, RF-187, RNF-027), arquitetura da instância de processamento sob demanda (doc 03 §7.8, ADR-14) e esquema dos agregados por minuto (doc 06 §5.6), aprovados em 06/11/2026 com o tipo c7g.2xlarge verificado. |
| DEC-33 | Tipo da instância sob demanda | **c7i-flex.large** (2 vCPU x86, 4 GiB; 06/11/2026), no lugar do c7g.2xlarge de DEC-32: a primeira execução foi recusada porque o plano gratuito da conta só lança tipos elegíveis à camada gratuita, e o `--dry-run` não confere isso. ~US$ 0,09/h, dos créditos; o plano B (MacBook) e a migração para o plano pago foram descartados por ora (doc 03 §7.8). |
| DEC-34 | Biblioteca de features (etapa 2 da V0.2) | Decisões PD-27 a PD-30 (07/11/2026): cálculo com `numpy`, num grupo opcional de pesquisa até a V0.3; janelas fixas de no máximo 1.500 minutos, sem médias exponenciais nem RSI de Wilder, para o lote e o ao vivo darem o mesmo valor; catálogo ajustado (desequilíbrio assinado relativo ao volume de 24 h, taxa de chegada fora de B6, janelas que faltavam); feature indefinida torna o ponto inválido, sem substituição (doc 04 §5, SRS §4.11). |
| DEC-35 | Etapa 2 da V0.2 aprovada | Requisitos das features (SRS v0.10: RF-201 a RF-213, RNF-028, RNF-029), definições exatas das 59 features (doc 04 v1.4, §5.1 a §5.3) e arquitetura do pacote (doc 03 §5.1), aprovados em 07/11/2026. |
| DEC-36 | Datasets (etapa 3 da V0.2) | Decisões PD-31 a PD-34 (07/11/2026): preço de execução do alvo calculado por minuto a partir dos negócios, como novas colunas de `minute_trades` (primeiro negócio depois de t_d + 0,5 s e + 2 s), com os agregados refeitos pelo job sob demanda; todos os degraus de uma trilha e os baselines B2 e B3 nos mesmos pontos, os válidos no degrau mais alto e com alvo válido; uma tabela por trilha e período, com todas as features e os alvos dos dois horizontes; estudo de robustez de 2023 a 2025 fora da etapa 3, com regra de preço a definir (doc 04 §3.2, §4, §9 e §11; SRS §4.12). |
| DEC-37 | Etapa 3 da V0.2 aprovada | Requisitos dos datasets e do alvo (SRS v0.11: RF-221 a RF-229, RNF-030), alvo, pontos elegíveis, preço de execução e formato do dataset (doc 04 v1.5), arquitetura (doc 03 §5.2) e esquema versão 2 de `minute_trades` (doc 06 §5.6), aprovados em 07/11/2026. |
| DEC-38 | Simulador, baselines e registro (etapas 4 e 5 da V0.2) | Decisões PD-35 a PD-38 (07/11/2026): o dataset ganha colunas de simulação (preço de execução da latência de estresse, fechamento e máxima dos 60 minutos anteriores), que os modelos não leem; o cenário de estresse de custos do IS-19 junta taxa de 0,15%, deslizamento de 0,03% e latência de 2 s; as tentativas do IS-15 são as configurações distintas de modelo avaliadas, sem contar repetições, execuções com erro nem baselines; o drawdown é medido com o capital marcado minuto a minuto pelo fechamento. Os dois esclarecimentos de G2 vêm antes de qualquer avaliação do holdout (doc 04 §6, §9, §10 e §11; SRS §4.13). |
| DEC-39 | Etapas 4 e 5 da V0.2 aprovadas | Requisitos do simulador, dos baselines e do registro de experimentos (SRS v0.12: RF-231 a RF-234, RF-251 a RF-257, RF-271 a RF-276, RNF-031; RF-226 com as colunas de simulação), cenários de custo, métricas, baselines e registro (doc 04 v1.6) e arquitetura (doc 03 §5.3), aprovados em 07/11/2026. |
| DEC-40 | Interpretações dos agregados por minuto confirmadas | As quatro interpretações registradas na implementação de doc 06 §5.6 ficam como estão (08/11/2026): num salto de id em dia de arquivo, os minutos dos dois negócios das pontas também ficam `missing`; `last_recv_ts_us` ignora os negócios recuperados do arquivo, sem horário de recebimento; `snapshot_count` conta também os snapshots com livro cruzado, que ficam fora das medidas; `minute_trades` e `minute_book` têm a coluna `symbol`. |

Permanecem **em aberto**: as pendências de §17.2.

### 17.2 Decisões pendentes

"Bloqueia" indica o que não pode avançar sem a decisão.

| ID | Questão | Opções | Recomendação | Resolver em | Bloqueia |
|---|---|---|---|---|---|
| PD-07 | Critérios quantitativos de G3 (os de G2 foram pré-registrados no doc 04 §10.2, DEC-27). | — | Pré-registrar no doc 07. | Doc 07 | G3 |
| PD-08 | Papel da testnet. | (a) paper trading pela testnet; (b) simulador local sobre dados reais. | **(b)** para medir desempenho; testnet apenas para validar integração (PR-02). | Doc 05 | Doc 05 |
| PD-09 | A V0.3 depende do resultado da V0.2? | — | A construção do motor não depende; a promoção de um modelo para paper trading depende de G2 (§8.3). | Este PRD | Doc 08 |
| PD-10 | Interface de operação. | (a) CLI, relatórios e logs; (b) painel visual. | (a) no MVP; painel como Could (F-32). | Doc 03 | Nada |
| PD-11 | Spot × futuros no longo prazo. | — | Adiar para depois do MVP. | Pós-MVP | Nada |
### 17.3 Pendências resolvidas

| Pendência | Resolvida em | Resultado |
|---|---|---|
| PD-02 | 23/10/2026 | DEC-14 — long/flat no spot. |
| PD-03 | 23/10/2026 | DEC-15 — intraday em minutos. |
| PD-04 (parcial) | 23/10/2026 | DEC-17 — hospedagem 24/7 na AWS; os detalhes continuam em PD-04. |
| PD-05 | 23/10/2026 | DEC-16 — créditos AWS e Free Tier; depois, teto de R$ 100/mês (revisto no mesmo dia; era R$ 50). |
| PD-06 | 23/10/2026 | DEC-18 — parcial top 20 + diferencial bruto com snapshots. |
| PD-12 (parcial) | 23/10/2026 | DEC-20 — arquivos JSONL + Parquet; o restante continua em PD-12. |
| PD-14 | 23/10/2026 | DEC-19 — só `trade` ao vivo. |
| PD-04 | 23/10/2026 | DEC-21 — Tóquio; instância e rede no doc 03 §7. |
| PD-12 | 23/10/2026 | DEC-24 — ADR-01 a ADR-11 do doc 03. |
| PD-13 | 23/10/2026 | DEC-24 — e-mail via SNS (ADR-12). |
| PD-01 | 23/10/2026 | DEC-25 — MERIS. |
| PD-07 (parcial) | 26/10/2026 | DEC-27 — critérios de G2 pré-registrados no doc 04 §10.2; G3 continua em PD-07. |
| PD-15 a PD-23 | 26/10/2026 | DEC-27 — método da V0.2 (doc 04 §1). |
| PD-24 a PD-26 | 06/11/2026 | DEC-31 — agregados por minuto (doc 06 §5.6; doc 03 §7.8). |
| PD-27 a PD-30 | 07/11/2026 | DEC-34 — biblioteca de features (doc 04 §5; SRS §4.11; doc 03 §5.1). |
| PD-31 a PD-34 | 07/11/2026 | DEC-36 — datasets e alvo (doc 04 §4, §9, §11; SRS §4.12; doc 03 §5.2; doc 06 §5.6). |
| PD-35 a PD-38 | 07/11/2026 | DEC-38 — simulador, baselines e registro de experimentos (doc 04 §6, §9, §10, §11; SRS §4.13; doc 03 §5.3). |

---

## 18. Rastreabilidade

| Objetivo | Hipóteses | Funcionalidades | Indicadores | Critérios | Detalhado em |
|---|---|---|---|---|---|
| OBJ-01 | — | F-01 a F-11 | IS-01 a IS-04 | CA-01 a CA-06 | Docs 02, 03, 06, 07 |
| OBJ-02 | H-01 a H-04 | F-12 a F-21 | IS-09 a IS-13 | CA-07 a CA-12 | Docs 02, 04, 07 |
| OBJ-03 | H-05 | F-22 a F-32 | IS-04 a IS-08 | CA-13 a CA-20 | Docs 02, 03, 05, 07 |
| OBJ-04 | H-04, H-05 | F-14, F-17, F-18, F-28 | IS-14 a IS-19 | G2 | Docs 04, 07 |
| OBJ-05 | — | Todas | — | Documentação e relatórios | Todos |
| OBJ-06 | H-06 | F-33 a F-36 | A definir | A definir | Pós-MVP |

---

## 19. Próximos passos

1. **Créditos AWS:** plano gratuito até 12/04/2027 e saldo de US$ 180 confirmados. Falta confirmar a validade dos créditos e o id da conta nova antes do primeiro `terraform apply` (doc 03 §14).
2. **Revisar e aprovar o PRD** (versão 1.0).
3. **[PROPOSTA] Ordem dos próximos documentos.** Como o histórico do livro de ofertas não pode ser recuperado retroativamente (PR-04, R-05), sugere-se priorizar o que destrava a coleta, sem abandonar o princípio de documentar antes de implementar:
   1. **02 — SRS**, com foco inicial nos requisitos da V0.1 — rascunho pronto;
   2. **06 — Data & Integrations** — rascunho pronto (DEC-18 a DEC-20);
   3. **03 — Architecture** — rascunho pronto (Data Engine e AWS; ADRs aprovadas, DEC-24);
   4. início da implementação da **V0.1** e da coleta contínua;
   5. **04 — AI & Quantitative Research**, **05 — Trading & Risk**, **07 — Testing & Validation** e **08 — Roadmap**, em paralelo ao acúmulo de dados.

---

## 20. Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 0.1 | 23/10/2026 | Rascunho inicial elaborado a partir do contexto de transferência (doc 00). |
| 0.2 | 23/10/2026 | Decididas PD-02 (DEC-14), PD-03 (DEC-15) e PD-05 (DEC-16); hospedagem na AWS (DEC-17). PR-09 reescrita; acrescentados PR-11, PR-12, RS-09, R-17 e R-18. |
| 0.3 | 23/10/2026 | PR-04, PR-05 e PR-06 verificadas na documentação oficial (SRS §2.5); PR-12 detalhada; PD-06 atualizada; acrescentadas PD-13 e PD-14. |
| 0.4 | 23/10/2026 | DEC-18, DEC-19 e DEC-20 (doc 06) resolvem PD-06, PD-14 e parte de PD-12; F-03 atualizada; PR-01 e PR-10 revistas com medições; RS-01 com volume medido. |
| 0.5 | 23/10/2026 | DEC-21 a DEC-23 (doc 03); PD-04 resolvida; PR-09 e PR-11 corrigidas (conta nova no plano gratuito; conta do CLI é outra); RS-09 com preços reais; R-17 revisto; R-19 e R-20 acrescentados. |
| 0.6 | 23/10/2026 | Teto pós-créditos revisto para R$ 100/mês (DEC-16, RS-09); DEC-23 reescrita (t4g.micro após os créditos); DEC-24 aprova ADR-01 a ADR-12 e resolve PD-12 e PD-13. |
| 0.7 | 23/10/2026 | DEC-25: nome do projeto MERIS — Market Evidence & Research Intelligence System; PD-01 resolvida. |
| 0.8 | 23/10/2026 | DEC-26: região us-east-2 (substitui DEC-21), após descobrir que a conta só aceita essa região e medir o acesso à Binance a partir dela; PR-12, R-18, RS-09 e DEC-23 revistos. |
| 0.9 | 24/10/2026 | §17.2: PD-15 a PD-23, decisões de método da V0.2 propostas no doc 04. |
| 0.10 | 26/10/2026 | DEC-27: PD-15 a PD-23 decididas (método da V0.2, doc 04 §1); critérios de G2 pré-registrados (PD-07 parcial); §13.3 aponta para o doc 04 §10.2. |
| 0.11 | 26/10/2026 | DEC-28: doc 04 aprovado (v1.0). |
| 0.12 | 26/10/2026 | DEC-29: nome do projeto MEVIS — Market Evidence Intelligence System (substitui DEC-25). |
| 0.13 | 31/10/2026 | DEC-30: G1 aprovado; limites de IS-01 e IS-02 confirmados. |
| 0.14 | 06/11/2026 | PD-24 a PD-26 propostas e decididas no mesmo dia (DEC-31): agregados por minuto da etapa 1 da V0.2. DEC-32: etapa 1 aprovada. |
| 0.15 | 06/11/2026 | DEC-33: instância sob demanda passa a c7i-flex.large (plano gratuito). |
| 0.16 | 07/11/2026 | PD-27 a PD-30 apresentadas e decididas no mesmo dia (DEC-34): biblioteca de features da etapa 2 da V0.2. DEC-35: etapa 2 aprovada. |
| 0.17 | 07/11/2026 | PD-31 a PD-34 apresentadas e decididas no mesmo dia (DEC-36): datasets e alvo da etapa 3 da V0.2. DEC-37: etapa 3 aprovada. |
| 0.18 | 07/11/2026 | PD-35 a PD-38 apresentadas e decididas no mesmo dia (DEC-38): simulador, baselines e registro de experimentos das etapas 4 e 5 da V0.2. DEC-39: etapas 4 e 5 aprovadas. |
| 0.19 | 08/11/2026 | DEC-40: interpretações dos agregados por minuto (doc 06 §5.6) confirmadas sem mudança. |

---

## Apêndice A — Glossário

Termos já definidos no [contexto inicial §18](00-contexto-inicial.md) não são repetidos aqui.

| Termo | Definição |
|---|---|
| Ablação | Experimento que remove ou acrescenta um conjunto de informações para medir sua contribuição isolada. |
| Backfill | Importação de dados históricos para preencher um período anterior ou uma lacuna. |
| Data snooping | Encontrar resultados por acaso ao testar muitas variações sobre os mesmos dados. |
| Deflated Sharpe Ratio | Ajuste do índice de Sharpe que considera quantas estratégias foram testadas antes de escolher a apresentada. |
| Drawdown máximo | Maior queda acumulada do patrimônio a partir de um pico anterior. |
| Fail-safe | Comportamento que, diante de falha ou incerteza, leva o sistema ao estado mais seguro (aqui: não abrir posições). |
| Holdout | Período de dados reservado e avaliado uma única vez, ao final, para estimar o desempenho real. |
| Kill switch | Mecanismo que interrompe imediatamente a atividade de negociação. |
| Long/flat | Política em que o sistema só pode estar comprado ou fora do mercado, nunca vendido. |
| Maker / taker | Maker é o lado que tinha a oferta no livro; taker é o lado que agrediu essa oferta. |
| Point-in-time | Propriedade de um dado ou feature de refletir apenas o que era conhecido naquele instante. |
| Pré-registro | Registro dos critérios de avaliação antes de observar os resultados. |
| Purga / embargo | Remoção de observações próximas à fronteira entre treino e teste, para evitar que rótulos sobrepostos vazem informação. |
| Slippage | Diferença entre o preço esperado e o preço efetivamente obtido em uma execução. |
| Training–serving skew | Diferença entre como os dados são processados na pesquisa e na operação, que faz o sistema real se comportar diferente do testado. |

## Apêndice B — Baselines e ablações

Interpretação da lista de baselines do contexto inicial (§9), com dois controles acrescentados (B0 e B2) e a separação de fluxo de negócios e livro de ofertas em etapas distintas, para medir cada um.

| ID | Estratégia | Função |
|---|---|---|
| B0 | Não operar. | Referência de custo zero: qualquer estratégia precisa superar "ficar fora". |
| B1 | Buy-and-hold de BTC/USDT no período. | Referência de mercado. |
| B2 | Entradas aleatórias com a mesma frequência e duração das operações do modelo avaliado. | Controle de sorte. |
| B3 | Regra simples baseada em candles (por exemplo, rompimento ou momentum), definida no doc 04. | Representa a análise técnica tradicional, sem ML. |
| B4 | Modelo com candles e indicadores técnicos. | Testa H-01. |
| B5 | B4 + volume. | Testa H-02. |
| B6 | B5 + fluxo de negócios (times & trades). | Testa a primeira parte de H-03. |
| B7 | B6 + livro de ofertas — modelo completo com os quatro conjuntos. | Testa a segunda parte de H-03 e H-04. |
| B8 | B7 + contexto macro e notícias. | Testa H-06 (V0.4, pós-MVP). |

Regras: todas as comparações usam a mesma janela temporal, o mesmo backtester, o mesmo modelo de custos e as mesmas regras de risco. B4–B8 usam o mesmo tipo de modelo e o mesmo processo de seleção de hiperparâmetros, para que a diferença venha dos dados e não do ajuste.
