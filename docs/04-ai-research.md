# 04 — AI & Quantitative Research Specification

| Campo | Valor |
|---|---|
| Projeto | **MEVIS** — Market Evidence Intelligence System |
| Documento | 04 — AI & Quantitative Research |
| Versão | 1.4 — aprovado |
| Escopo desta versão | Método de pesquisa da V0.2: dados e períodos, formulação do problema, features, baselines, modelos, validação, custos, métricas, critérios de G2 a pré-registrar, registro de experimentos e plano de trabalho. |
| Status | Aprovado em 26/10/2026 (DEC-28); v1.4 aprovada em 07/11/2026 (DEC-35). Decisões PD-15 a PD-23 em §1; critérios de G2 pré-registrados (§10.2). Mudanças depois disto entram como nova versão, com motivo no histórico. |
| Data | 26/10/2026 |
| Documentos de origem | [01 — PRD](01-prd.md) v0.8; [02 — SRS](02-srs.md) v0.5; [03 — Architecture](03-architecture.md) v0.8; [06 — Data & Integrations](06-data-integrations.md) v0.8 |

---

## 0. Escopo e relação com os outros documentos

Este documento define **como** as hipóteses H-01 a H-04 (PRD §4) serão testadas. Ele não muda as hipóteses, os baselines (PRD, Apêndice B) nem os indicadores (PRD §13); detalha o que o PRD deixou para cá:

- horizontes exatos (DEC-15) e formulação do alvo;
- latência assumida para dados históricos sem horário de recebimento (RN-006; doc 06 §4.3);
- regra do baseline B3;
- esquema de validação, holdout e critérios quantitativos de G2, a pré-registrar (PD-07, RN-013).

Ficam em outros documentos: limites de risco e modelo final de preenchimento de ordens (doc 05); critérios de G3 e degradação tolerada paper × backtest (doc 07); requisitos `RF-2xx` (SRS, depois deste documento).

As etiquetas seguem o PRD §0.1. Este documento não presume que exista vantagem: "não há evidência" é um resultado aceitável (PP-08).

**O que já foi observado dos dados (para o registro honesto do pré-registro):** durante o backfill e a coleta, só foram vistos contagens de linhas por dia, estatísticas de qualidade (quarentena, ids e minutos ausentes) e métricas de operação (latência, offset). Nenhuma análise de retornos, padrões ou features foi feita até a data deste documento.

---

## 1. Decisões deste documento

Decididas pelo responsável pelo produto em 26/10/2026, depois de apresentadas as opções e os trade-offs. Cada item é detalhado na seção indicada.

| ID | Questão | Opções apresentadas | Decisão | Seção |
|---|---|---|---|---|
| PD-15 | Horizontes de previsão | (a) um só, 5 min; (b) dois, 15 e 60 min; (c) varredura de 1 a 240 min | **(b) 15 e 60 min** | §4.2 |
| PD-16 | Formulação do alvo | (a) binário: retorno líquido em H minutos > 0; (b) regressão do retorno; (c) barreiras triplas (alvo, stop, tempo) | **(a)**; (c) como extensão depois do primeiro ciclo | §4.3 |
| PD-17 | Holdout da trilha A (histórico) | (a) últimos 3 meses; (b) últimos 6 meses | **(a)** 23/07/2026 a 22/10/2026 | §3.3 |
| PD-18 | Holdout da trilha B (com livro) | (a) 4 semanas coletadas depois do congelamento; (a') 6 semanas depois do congelamento; (b) as últimas 2 semanas já coletadas | **(a') 6 semanas** depois do congelamento: com H = 60 min, 4 semanas comportam no máximo ~670 operações sem sobreposição, e um modelo seletivo pode ficar abaixo das 200 do IS-17 | §3.3 |
| PD-19 | Latência assumida para dados de arquivo | (a) 0 s; (b) 1 s após o evento; (c) p99 medido ao vivo | **(b) 1 s**, revista para (c) se o p99 medido passar de 1 s | §3.5 |
| PD-20 | Custo base | (a) 0,10% por lado (taxa padrão); (b) 0,075% (com desconto em BNB) | **(a) 0,10%**, com cenários de sensibilidade | §9 |
| PD-21 | Valores dos critérios de G2 | Propostos em §10.2 | **Aprovados como propostos** e pré-registrados | §10 |
| PD-22 | Uso dos candles de 2023 a 2025 | (a) estudo de robustez de H-01 e H-02; (b) dentro do walk-forward oficial | **(a) estudo de robustez** | §3.2 |
| PD-23 | Onde transformar negócios em agregados por minuto | (a) MacBook, lendo do S3 dia a dia; (b) host de produção; (c) EC2 dedicada e temporária | **(c) EC2 dedicada**, com o MacBook como plano B (estimativa de custo aprovada: ~US$ 0,60 por execução) | §12 |

---

## 2. Pergunta, hipóteses e escada de comparação

Pergunta de pesquisa (PRD §2.3): um modelo que combina candles, volume, fluxo de negócios e livro de ofertas produz decisões com expectativa positiva, líquida de custos e ajustada ao risco, fora da amostra, e superior à de estratégias que usam só parte dessas informações?

| Degrau | Conteúdo | Hipótese | Trilha (§3.2) |
|---|---|---|---|
| B0 | Não operar | Controle | A e B |
| B1 | Comprar e segurar | Controle | A e B |
| B2 | Entradas aleatórias com a mesma frequência e duração do modelo avaliado | Controle de sorte | A e B |
| B3 | Regra de rompimento (§6) | Análise técnica sem ML | A e B |
| B4 | Modelo com candles | H-01 | A e B |
| B5 | B4 + volume | H-02 | A e B |
| B6 | B5 + fluxo de negócios | H-03 (parte 1) | A e B |
| B7 | B6 + livro de ofertas | H-03 (parte 2), H-04 | só B |

Regras do PRD (Apêndice B) que valem aqui: mesma janela, mesmo backtester, mesmo modelo de custos, mesmo tipo de modelo e mesmo processo de seleção de hiperparâmetros entre B4 e B7.

---

## 3. Dados e períodos

### 3.1 O que existe

| Conjunto | Período disponível | Origem | Horário de recebimento | Referência |
|---|---|---|---|---|
| Candles de 1 min | 01/01/2023 a 22/10/2026 | Arquivo | Não | doc 06 MD-26 |
| Negócios | 23/10/2025 a 22/10/2026 | Arquivo | Não | doc 06 MD-27 |
| Candles, negócios e livro top 20 | desde 23/10/2026, 22:44 UTC | Coleta ao vivo | Sim | doc 06 §5 |
| Diferencial do livro | desde 23/10/2026, 22:44 UTC | Coleta ao vivo | Sim | Gravado, não usado na V0.2 (doc 06 §10) |

O dia 23/10/2026 antes das 22:44 UTC fica sem negócios até a recuperação (F-06) ou é excluído.

### 3.2 Duas trilhas

O livro de ofertas só existe a partir da coleta, e as ablações exigem a mesma janela para todos os degraus. Por isso a pesquisa corre em duas trilhas:

- **Trilha A — histórico (B0 a B6):** 23/10/2025 a 22/10/2026, a janela em que há negócios. Responde H-01, H-02 e a parte 1 de H-03. Pode começar logo depois da aprovação deste documento, sem esperar G1, porque usa arquivos públicos (PRD §8.3).
- **Trilha B — com livro (B0 a B7):** de 23/10/2026 em diante. Responde a parte 2 de H-03 e H-04. Começa depois de G1 e de acumular dados suficientes (§13).

**[DECISÃO] PD-22:** os candles de 2023 a 2025 entram como **estudo de robustez** de B3, B4 e B5 (mesmo método, outra época), e não no walk-forward oficial. Servem para ver se um resultado da trilha A depende de um único regime de mercado (R-06).

### 3.3 Desenvolvimento e holdout (pré-registro)

| Trilha | Desenvolvimento | Holdout | Situação |
|---|---|---|---|
| A | 23/10/2025 a 22/07/2026 (9 meses) | **23/07/2026 a 22/10/2026 (3 meses)** [DECISÃO] PD-17 | Os dados existem; o holdout está lacrado (nenhuma consulta desde o backfill). |
| B | 23/10/2026 até a data de congelamento (mínimo 8 semanas) | **6 semanas corridas depois do congelamento** [DECISÃO] PD-18 | O holdout ainda não existe: é coletado depois que modelo e critérios estão congelados. |

Regras:

1. Nenhuma consulta, gráfico ou estatística do período de holdout antes da avaliação final, nem para "dar uma olhada". O código de construção de datasets recusa o período de holdout, a menos que a execução seja a avaliação final registrada.
2. A análise exploratória só usa o período de desenvolvimento.
3. Cada modelo candidato é avaliado no holdout **uma única vez** (IS-12). Uma segunda avaliação do mesmo holdout invalida a primeira como evidência e fica registrada.
4. O congelamento da trilha B é um commit que fixa código, configuração e critérios; a data de início do holdout B é a do primeiro minuto depois desse commit.

### 3.4 Qualidade dos dados

- **Ao vivo:** um minuto é válido se a máscara de qualidade (doc 06 §8.2) dos canais exigidos pelo degrau estiver em `ok` ou `recovered`. Um ponto de decisão só é usado se forem válidos **todos** os minutos das janelas de até 60 min e da janela do alvo; nas janelas mais longas (240 min e 24 h), a feature é calculada só com os minutos válidos e exige **pelo menos 95%** deles, senão o ponto é descartado. Esse é o mesmo comportamento que o Risk Manager terá ao vivo (RN-005, PP-05).
- **Arquivo:** sem máscara (doc 06 §7, item 10). A regra equivalente: minuto sem candle ou com id de negócio ausente é inválido, com os mesmos limites das janelas curtas (estrito) e longas (≥ 95% válidos). Os 80 minutos ausentes de março de 2023 (MD-26) só afetam o estudo de robustez.
- Nenhum preenchimento ou interpolação (RN-008): nas janelas longas, as estatísticas usam só os minutos que existem.
- **Motivo da tolerância nas janelas longas:** com ~99,9% de minutos completos ao vivo (cerca de um minuto perdido por dia em algum canal), a regra estrita numa janela de 24 h descartaria um dia inteiro de pontos a cada minuto perdido, e a trilha B perderia boa parte dos dados (decidido em 26/10/2026).

### 3.5 Disponibilidade point-in-time e latência assumida

| Dado | Disponível para decisão a partir de |
|---|---|
| Negócio ao vivo | `recv_ts_us` |
| Candle ao vivo | `recv_ts_us` da mensagem com `x = true` |
| Snapshot top 20 ao vivo | `recv_ts_us` |
| Negócio de arquivo | `trade_ts_us` + **L** |
| Candle de arquivo | `close_ts_us` + **L** |

**[DECISÃO] PD-19:** L = 1 s. O atraso medido ao vivo do evento ao recebimento tem p50 de 69 ms (MD-23) e p99 por hora entre ~100 e ~250 ms nos primeiros dias da coleta (página pública de status, doc 03 §7.7); 1 s é conservador. Se o p99 medido na semana do CA-01 passar de 1 s, L passa a ser esse p99. Como as decisões são por minuto (§4.1), L afeta pouco o resultado; a regra existe para não haver exceção ao PP-04.

---

## 4. Formulação do problema

### 4.1 Momento da decisão

A decisão é tomada uma vez por minuto, em t_d = fechamento do minuto + 2 s (tempo para o candle fechado chegar e as features serem calculadas). Todas as features usam só dados disponíveis até t_d (§3.5).

### 4.2 Horizontes

**[DECISÃO] PD-15:** dois horizontes pré-registrados, **H = 15 min** e **H = 60 min**, avaliados separadamente e contados como experimentos distintos (§11).

Motivo: com custo de ~0,22% por operação completa (§9), o preço precisa andar mais que isso para haver lucro. **[HIPÓTESE]** Em poucos minutos, os movimentos típicos do BTC são da mesma ordem dos custos; 15 e 60 minutos dão mais espaço sem sair do intraday (DEC-15). A varredura de muitos horizontes (opção c) aumentaria o número de testes e a chance de achar sorte (R-02).

### 4.3 Alvo

**[DECISÃO] PD-16:** alvo binário y = 1 se o retorno da operação simulada (entrada em t_d, saída em t_d + H, pelas regras de §9) for positivo **depois dos custos**; y = 0 caso contrário.

- O modelo estima p = P(y = 1). A decisão entra comprada se p > τ, com τ escolhido na validação interna (§8.2).
- Usar o retorno líquido no alvo alinha o que o modelo aprende com o que interessa (lucro depois de custos), em vez de prever direção pura.
- Barreiras triplas (alvo de lucro, stop e tempo) ficam como extensão depois do primeiro ciclo, porque acrescentam parâmetros e testes.

### 4.4 Regra de operação

- Só long/flat (DEC-14): entrar comprado ou ficar fora.
- Saída por tempo: exatamente H minutos depois da entrada.
- Sem sobreposição: com posição aberta, novos sinais são ignorados.
- Tamanho fixo (todo o capital simulado por operação, sem alavancagem — RN-003). Resultados em percentual; o dimensionamento fica no doc 05.

---

## 5. Features

### 5.1 Regras

1. Toda feature é uma função dos dados disponíveis até t_d (§3.5); janelas terminam no último minuto fechado.
2. O mesmo código calcula as features na pesquisa e na operação (PP-05, F-12). Ele recebe uma sequência de minutos e não sabe se veio de arquivo ou do ao vivo.
3. Cada feature tem um teste automatizado de point-in-time (IS-11, CA-08): alterar qualquer dado posterior a t_d não pode mudar o valor da feature em t_d.
4. Minuto inválido numa janela de até 60 min, ou mais de 5% de minutos inválidos numa janela longa (§3.4), torna o ponto inválido; não há preenchimento.
5. Conversão para ponto flutuante só a partir daqui (doc 06 §5.3).
6. **Janelas fixas [DECISÃO] DEC-34:** toda feature usa no máximo os últimos 1.500 minutos (24 h mais a maior janela); não há médias exponenciais nem RSI de Wilder, cujo valor depende de todo o histórico desde o início da série. Assim, o cálculo em lote da pesquisa e o cálculo ao vivo sobre os últimos 1.500 minutos dão o mesmo valor.
7. **Valor indefinido [DECISÃO] DEC-34:** uma feature indefinida com dados válidos (divisão por zero, minuto do livro sem medidas) torna o ponto inválido na família, como um minuto inválido; o valor não é substituído. A regressão logística e o XGBoost veem os mesmos pontos.

### 5.2 Catálogo inicial [DECISÃO]

As famílias são cumulativas, seguindo a escada de §2. Valores de janela em minutos.

| Família (degrau) | Features | Fonte |
|---|---|---|
| Candles (B4) | Retornos logarítmicos em 1, 5, 15, 60 e 240; volatilidade realizada em 15 e 60; amplitude (máxima − mínima) relativa em 1 e 15; distância às médias móveis de 15, 60 e 240; RSI de 14; posição do fechamento na amplitude dos últimos 60; hora do dia e dia da semana (UTC) | Candles de 1 min |
| Volume (B5) | Volume em 1, 5, 15 e 60 relativo à média das últimas 24 h; número de negócios em 1 e 15 relativo à média de 24 h; retorno ponderado por volume em 15 | Candles (volume, `trade_count`) |
| Fluxo de negócios (B6) | Fração do volume com agressor comprador em 1, 5, 15 e 60; desequilíbrio de volume assinado (compra − venda) em 1, 5, 15 e 60, relativo ao volume médio das últimas 24 h; fração do volume em negócios grandes (acima do percentil 95 de tamanho das 24 h anteriores) em 15 e 60; distância ao VWAP de 15 e 60 | Negócios |
| Livro de ofertas (B7) | Spread relativo; desequilíbrio do 1º nível (qtd. compra / (compra + venda)); desequilíbrio acumulado em 5, 10 e 20 níveis; distância do microprice ao preço médio; média, mínimo e último valor dessas medidas no último minuto; variação dos desequilíbrios de 1 e de 20 níveis em 1 e 5 | Snapshots top 20 |

Decisão de fronteira: o volume com agressor comprador (`taker_buy_volume`) também aparece nos candles, mas pertence ao **fluxo de negócios** (B6). B5 usa só volume total e contagem, para que o ganho de B6 meça a informação de direção da agressão.

Mudanças no catálogo durante o desenvolvimento são permitidas e registradas (§11); depois do congelamento de uma trilha, não.

**Ajustes de 07/11/2026 [DECISÃO] DEC-34**, antes da implementação:

- O desequilíbrio assinado normalizado pelo volume do próprio período seria a fração compradora reescrita (`(B − S) / (B + S) = 2·B/(B + S) − 1`). Passou a ser dividido pelo volume médio de 24 h, e assim mede a intensidade da agressão, não a proporção.
- A taxa de chegada de negócios no último minuto saiu de B6: repetia a contagem de B5 e misturaria atividade com direção na ablação de B6.
- Janelas que o catálogo não fixava: 1, 5, 15 e 60 no desequilíbrio assinado; 15 e 60 nos negócios grandes; a variação do livro mede os desequilíbrios de 1 e de 20 níveis.

### 5.3 Definições exatas [DECISÃO] DEC-34

**Notação.** t é o último minuto fechado no instante da decisão t_d = fim de t + 2 s (§4.1), e J_w são os w minutos de t − w + 1 a t. Dos candles: c, h, l, v e n (fechamento, máxima, mínima, volume e número de negócios). De `minute_trades` (doc 06 §5.6): V e B (volume total e com agressor comprador), S = V − B, Q (volume em USDT), C (fechamento), K[k] e U[k] (contagem e volume na faixa de tamanho k). r_i = ln(c_i / c_{i−1}). As médias de 24 h (v̄, n̄ e V̄) são médias por minuto nos minutos válidos de J_1440.

**Candles (B4)**, canal de candles:

| Feature | Definição | Minutos usados |
|---|---|---|
| `ret_w`, w ∈ {1, 5, 15, 60, 240} | ln(c_t / c_{t−w}) | t − w e t |
| `rvol_w`, w ∈ {15, 60} | √(Σ r_i², i ∈ J_w), sem anualizar | t − w a t |
| `range_w`, w ∈ {1, 15} | (máx. de h − mín. de l em J_w) / c_t | J_w |
| `sma_dist_w`, w ∈ {15, 60, 240} | c_t / média de c em J_w − 1 | J_w |
| `rsi_14` | 100 · G / (G + P), com G e P as somas das altas e das baixas de c_i − c_{i−1} em J_14 (médias simples) | t − 14 a t |
| `range_pos_60` | (c_t − mín. de l) / (máx. de h − mín. de l), em J_60 | J_60 |
| `hour_sin`, `hour_cos` | seno e cosseno de 2π · h / 24, com h a hora UTC do fim de t em fração (14h30 → 14,5) | — |
| `dow_sin`, `dow_cos` | seno e cosseno de 2π · d / 7, com d o dia da semana UTC do fim de t (segunda = 0) | — |

Hora e dia viram seno e cosseno porque a regressão logística trataria um número inteiro como linear (23 h longe de 0 h); o XGBoost não perde nada com isso.

**Volume (B5)**, canal de candles:

| Feature | Definição | Minutos usados |
|---|---|---|
| `vol_rel_w`, w ∈ {1, 5, 15, 60} | Σ v em J_w / (w · v̄) | J_w e J_1440 |
| `count_rel_w`, w ∈ {1, 15} | Σ n em J_w / (w · n̄) | J_w e J_1440 |
| `vwret_15` | Σ v_i · r_i / Σ v_i, i ∈ J_15 | t − 15 a t |

**Fluxo de negócios (B6)**, canal de negócios:

| Feature | Definição | Minutos usados |
|---|---|---|
| `buy_frac_w`, w ∈ {1, 5, 15, 60} | Σ B / Σ V, em J_w | J_w |
| `signed_vol_w`, w ∈ {1, 5, 15, 60} | Σ (B − S) em J_w / (w · V̄) | J_w e J_1440 |
| `large_frac_w`, w ∈ {15, 60} | Σ U[k] das faixas k > k* / Σ V, em J_w; k* é a faixa que contém o percentil 95 do tamanho dos negócios, pela contagem K somada nos 1.440 minutos anteriores a J_w (a menor faixa em que a contagem acumulada chega a 95%) | J_w e os 1.440 minutos anteriores |
| `vwap_dist_w`, w ∈ {15, 60} | C_t / (Σ Q / Σ V em J_w) − 1 | J_w |

O limite de negócios grandes tem a resolução das faixas, de ~19% (DEC-31).

**Livro de ofertas (B7)**, canal top 20:

| Feature | Definição | Minutos usados |
|---|---|---|
| `book_<medida>_<estatística>`: medidas `spread_rel`, `imbalance_1`, `imbalance_5`, `imbalance_10`, `imbalance_20` e `microprice_dist`; estatísticas `mean`, `min` e `last` | o valor do minuto t em `minute_book` (doc 06 §5.6) | t |
| `imb1_chg_w`, `imb20_chg_w`, w ∈ {1, 5} | `imbalance_k_last` de t menos o de t − w (k = 1 ou 20) | t − w e t |

São 59 features: 18 de candles, 7 de volume, 12 de fluxo e 22 de livro.

**Minuto válido**, por canal:

- **Ao vivo:** status `ok` ou `recovered`: o do canal de candles na máscara, o de `minute_trades` e o de `minute_book`. O minuto 23/10/2026 22:44 UTC é inválido nos negócios e no livro (exceção do doc 06 §8.2).
- **Arquivo:** candle presente; para os negócios, status `ok` em `minute_trades` (sem id ausente).
- **Disponível em t_d (§3.5):** ao vivo, o candle pelo `recv_ts_us` da mensagem fechada e o minuto de negócios pelo `last_recv_ts_us`. Se este for nulo (minuto sem negócios ao vivo, ou só com recuperados), o minuto vale como dado de arquivo: fim do minuto + L. Os snapshots do livro são agrupados pelo recebimento, então o minuto do livro está sempre disponível no seu fim. No arquivo, o fim do minuto + L. Minuto indisponível em t_d conta como inválido.

**Ponto válido** numa família, com as regras de §3.4:

1. Todos os minutos usados por janelas de até 60 min são válidos.
2. Nas janelas de 240 e 1.440 minutos, os cálculos usam só os minutos válidos e exigem pelo menos 95% deles; os extremos de `ret_240` (t e t − 240) precisam ser válidos.
3. Todas as features da família estão definidas (regra 7 de §5.1).

As famílias são cumulativas: o ponto vale para o degrau B5 se for válido em candles e volume, e assim por diante. A janela mais longa é a de `large_frac_60` (60 + 1.440 minutos), o que dá o limite de 1.500 minutos da regra 6.

---

## 6. Baselines

| ID | Definição [DECISÃO] |
|---|---|
| B0 | Nenhuma operação: resultado zero, custo zero. |
| B1 | Compra no primeiro minuto da janela avaliada e venda no último, com os custos de uma operação. |
| B2 | Para cada modelo avaliado: 1.000 simulações com o mesmo número de operações e a mesma duração H, em minutos sorteados entre os pontos de decisão válidos, sem sobreposição. Compara-se o resultado do modelo com a distribuição. |
| B3 | **Rompimento de 60 min:** entra comprado quando o fechamento do minuto supera a máxima dos 60 minutos anteriores; sai depois de H minutos. Sem parâmetros ajustados aos dados. Representa a análise técnica de rompimento que motivou o projeto (PRD §2.1). |

---

## 7. Modelos

- **Regressão logística** com features padronizadas (média e desvio do treino de cada dobra): referência linear.
- **XGBoost** (PRD §8.2): modelo principal de B4 a B7.
- Redes neurais (F-21) só depois do primeiro ciclo completo, como Could.

**Seleção de hiperparâmetros [DECISÃO]:** grade pequena e fixa, a mesma para B4 a B7, escolhida na validação interna de cada dobra:

| Modelo | Grade |
|---|---|
| Logística | C ∈ {0,1; 1} |
| XGBoost | profundidade ∈ {2; 4}; árvores ∈ {200; 500}; taxa de aprendizado 0,05; subamostragem 0,8 |
| Limiar τ | {0,50; 0,55; 0,60; 0,65; 0,70} |

Grade pequena é deliberada: cada combinação conta como tentativa na correção por testes múltiplos (IS-15).

---

## 8. Validação

### 8.1 Walk-forward

| Trilha | Treino inicial | Teste por dobra | Dobras no desenvolvimento |
|---|---|---|---|
| A | 3 meses, janela expansível | 1 mês | 6 (meses 4 a 9) |
| B | 4 semanas, janela expansível | 1 semana | ≥ 4 |

- **Purga:** saem do treino os pontos cujo alvo termina depois do início do teste (os últimos H minutos antes do teste).
- **Embargo:** como a janela só avança no tempo, não há treino depois do teste; nenhum embargo adicional.
- Os resultados de desenvolvimento são a concatenação das dobras de teste.

### 8.2 Validação interna (dentro do treino de cada dobra)

O último mês (trilha A) ou a última semana (trilha B) do treino de cada dobra serve para escolher hiperparâmetros e τ, também com purga. O teste da dobra nunca participa dessa escolha.

### 8.3 Holdout

Depois do desenvolvimento, para cada degrau e horizonte, o modelo final é treinado com todo o período de desenvolvimento, com os hiperparâmetros e τ escolhidos pelo mesmo processo, e avaliado uma vez no holdout (§3.3).

---

## 9. Custos e simulação de execução

**[DECISÃO] PD-20:**

| Item | Valor base | Sensibilidade |
|---|---|---|
| Taxa por lado (taker) | 0,10% [A VERIFICAR: taxa padrão de spot da Binance] | 0,075% (desconto em BNB) e 0,15% (estresse) |
| Spread e deslizamento por lado | 0,01% | 0 e 0,03% |
| Latência de execução | 0,5 s depois de t_d | 2 s |
| Preço de entrada e saída | Primeiro negócio depois de t_d + latência, ajustado pelo deslizamento contra a operação | — |

Custo base por operação completa: ~0,22%. Toda estratégia (B1 a B7) usa o mesmo modelo. O resultado é sempre apresentado com os três cenários de taxa.

Ordens a mercado (taker) são a hipótese conservadora; executar como maker (ordem limitada) reduziria a taxa, mas com risco de não ser executado. Esse modelo de preenchimento é do doc 05 e deve ser o mesmo do paper trading (PP-05); este documento será revisto quando o doc 05 existir.

---

## 10. Métricas e critérios de G2

### 10.1 Métricas de todo experimento

- **Financeiras:** expectativa líquida por operação (em pontos-base); retorno líquido total; Sharpe a partir de retornos diários (anualizado por √365); drawdown máximo; número de operações; taxa de acerto; exposição (fração do tempo posicionado).
- **Diagnósticas:** AUC e log loss do classificador; importância das features. Não decidem nada sozinhas.
- **Por subperíodo:** cada dobra do walk-forward separadamente (IS-18).

### 10.2 Critérios de G2 [DECISÃO] PD-21 — pré-registrados em 26/10/2026

Um modelo passa em G2 se, **no holdout**, atender a todos:

| ID | Critério |
|---|---|
| IS-14 | Expectativa líquida por operação > 0, com limite inferior do intervalo de confiança de 95% > 0 (bootstrap em blocos de 1 dia). |
| IS-15 | Deflated Sharpe Ratio ≥ 0,95, com o número de tentativas igual ao total registrado na trilha (§11). |
| IS-16 | Sharpe maior que o de B1 e o de B3 no mesmo período; resultado acima do percentil 95 da distribuição de B2; resultado acima de B0 (zero). |
| IS-17 | Pelo menos 200 operações no holdout. Com menos, o resultado é **inconclusivo**, não aprovado. |
| IS-18 | Resultado líquido positivo em pelo menos 2/3 das dobras de teste do desenvolvimento. |
| IS-19 | Resultado positivo também no cenário de estresse de custos (§9); drawdown máximo no holdout ≤ 15% do capital simulado, até o doc 05 definir o limite. |

As ablações (H-01 a H-03) comparam degraus vizinhos pela diferença de expectativa líquida por operação e de Sharpe, com intervalo de confiança por bootstrap pareado nos mesmos dias. Ganho incremental = limite inferior do intervalo acima de zero.

Os critérios estão congelados desde 26/10/2026 (commit que registra esta versão). Alterá-los depois de qualquer avaliação de holdout invalida a avaliação (RN-013); qualquer mudança antes disso precisa de nova aprovação e fica no histórico.

---

## 11. Registro de experimentos e versionamento de datasets

- **Dataset** (F-13): identificado por um resumo (hash) de: período, horizonte, família de features, commit do código de features e lista de arquivos normalizados com seus SHA-256. Gravado em Parquet em `derived/datasets/<id>/` com um manifesto JSON; reconstruível a partir dos dados normalizados (CA-07).
- **Experimento** (F-19): toda execução recebe um id e registra, **antes** de rodar, trilha, degrau, horizonte, modelo, grade, semente, dataset e commit; ao terminar, as métricas. Execuções descartadas, com erro ou repetidas continuam no registro (IS-13).
- O número de tentativas por trilha, usado no IS-15, é a contagem do registro.
- Armazenamento: SQLite local no MacBook, exportado para `derived/experiments/` no S3 a cada execução. Sem MLflow ou serviços externos (PP-07).

---

## 12. Onde roda e como os dados chegam

- **Pesquisa no MacBook** (doc 03; doc 02 §2.3). O disco tem pouco espaço livre (~7 GB em 24/10/2026), então os negócios brutos não são copiados para o Mac.
- **[DECISÃO] PD-23:** um passo prévio transforma negócios em **agregados por minuto** (RF-171 e RF-172: volume por lado do agressor, contagem, VWAP, estatísticas de tamanho) e grava o resultado em `derived/minute_trades/` no S3. 12 meses de negócios (11,9 GB) viram algumas centenas de MB. Roda numa **EC2 dedicada e temporária**, na conta e na região do projeto:
  - **Instância:** c7i-flex.large (2 vCPUs x86, 4 GiB), US$ 0,0848/h sob demanda em us-east-2 (DEC-33, 06/11/2026; antes c7g.2xlarge, recusado pelo plano gratuito). Processa um dia de negócios por núcleo; os 9 meses fora do holdout levaram ~8 min de instância (~US$ 0,01) na primeira execução, em 06/11/2026 (doc 03 §7.8).
  - **Custo:** ~US$ 0,10 por execução, pago pelos créditos (antes ~US$ 0,60, estimativa aprovada em 26/10/2026). Sem tráfego pela internet: lê do S3 pelo endpoint da VPC, na mesma região.
  - **Segurança e custo sob controle:** papel IAM só com leitura de `normalized/trades/` e gravação em `derived/minute_trades/`; a instância se encerra e se apaga sozinha ao terminar, com um limite de horas como trava (desligamento iniciado pela instância com comportamento `terminate`).
  - **Não toca o coletor:** a máquina de produção não divide CPU com o processamento.
  - **Plano B:** MacBook, lendo do S3 dia a dia (~6 h; ~12 GB de saída do S3).
  - **Plano gratuito:** só lança tipos elegíveis à camada gratuita; o c7g foi recusado na primeira execução, embora o `run-instances --dry-run` tivesse sido aceito (doc 03 §7.8).
  - A arquitetura dessa EC2 (rede, IAM, como é lançada) entra no doc 03 antes da implementação.
- O mesmo vale para o livro (agregados por minuto dos snapshots top 20), que já é pequeno.
- Dependências novas ficam num grupo opcional de pesquisa, fora do pacote do host, até a V0.3 precisar do código de features ao vivo (doc 03 §5): `numpy` para as features (DEC-34) e, na etapa 6, `xgboost` e `scikit-learn`.

---

## 13. Plano de trabalho da V0.2 [DECISÃO]

| Etapa | Entrega | Depende de |
|---|---|---|
| 1 | Agregados por minuto de negócios (PD-23, EC2 dedicada) e de livro | Seção da EC2 dedicada no doc 03; requisitos da etapa no SRS |
| 2 | Biblioteca de features com testes de point-in-time (F-12) | 1 |
| 3 | Construtor de datasets versionados e trava do holdout (F-13, §3.3) | 2 |
| 4 | Simulador de operações com custos e baselines B0 a B3 (F-14, F-17) | 3 |
| 5 | Registro de experimentos (F-19) | 3 |
| 6 | Trilha A: walk-forward de B4 a B6, ablações, relatório de desenvolvimento | 4, 5 |
| 7 | Trilha A: avaliação única do holdout | 6 e critérios congelados |
| 8 | Trilha B: desenvolvimento com B0 a B7 | G1 e ≥ 8 semanas de livro (por volta de 20/12/2026) |
| 9 | Trilha B: congelamento, coleta do holdout (6 semanas) e avaliação única | 8 |
| 10 | Relatório de conclusão da V0.2: status de H-01 a H-04 (CA-12) | 7, 9 |

Pelo calendário, a trilha B só termina por volta de meados de fevereiro de 2027, antes do CP-2 (15/03/2027, doc 03 §10.4).

---

## 14. Riscos específicos da pesquisa

| Risco | Mitigação |
|---|---|
| Custos maiores que os movimentos no horizonte escolhido (R-01, R-04) | Horizontes de 15 e 60 min; três cenários de custo; resultado negativo aceito. |
| Pouca amostra na trilha B (R-05) | Holdout de 6 semanas (PD-18); IS-17: abaixo de 200 operações, inconclusivo; possibilidade de estender a coleta antes do congelamento. |
| Testes múltiplos e snooping (R-02, R-15) | Grade pequena; registro de todas as tentativas; DSR; holdout lacrado e usado uma vez. |
| Diferença entre dados de arquivo e ao vivo | Latência assumida (§3.5); mesma normalização (doc 06 §7, item 3); comparar as duas fontes num mesmo dia quando a recuperação (F-06) existir. |
| Mudança de regime (R-06) | Avaliação por dobra (IS-18); estudo de robustez com 2023 a 2025 (PD-22). |

---

## 15. Pendências deste documento

| Item | Onde se resolve |
|---|---|
| Taxa de spot da Binance em vigor | [A VERIFICAR] antes da etapa 4 |
| Plano gratuito libera instâncias c7g (PD-23) | **Resolvido em 06/11/2026:** não libera. O `--dry-run` foi aceito, mas a execução real foi recusada; tipo trocado para c7i-flex.large (DEC-33, doc 03 §7.8) |
| Seção da EC2 dedicada (rede, IAM, lançamento) | Aprovada no doc 03 §7.8 (06/11/2026, DEC-32) |
| Franquia de saída de dados do S3 no plano gratuito | [A VERIFICAR] só se o plano B (MacBook) for usado |
| p99 do atraso ao vivo (PD-19) | **Resolvido:** 119 ms em negócios e 174 ms em candles na semana do CA-01 (doc 06 §8.6); L = 1 s mantida |
| Modelo de preenchimento final e limites de drawdown | Doc 05 |
| Degradação tolerada paper × backtest (H-05) | Doc 07 |
| Requisitos RF-201 a RF-289 | SRS, antes de cada etapa: RF-201 a RF-213 (etapa 2, features) no SRS v0.10 |
| Comparar os degraus de uma trilha nos mesmos pontos: a validade vem por família (§5.3), e o PRD (Apêndice B) pede a mesma janela; falta fixar se cada degrau usa os pontos válidos nele ou os válidos no degrau mais alto da trilha | Etapa 3 (datasets) |

---

## 16. Rastreabilidade

| Funcionalidade (PRD) | Seções |
|---|---|
| F-12 Features point-in-time | §3.5, §5 |
| F-13 Datasets versionados | §11 |
| F-14 Baselines | §6 |
| F-15 Treinamento | §7 |
| F-16 Validação temporal | §3.3, §8 |
| F-17 Backtester com custos | §9 |
| F-18 Ablações | §2, §10.2 |
| F-19 Registro de experimentos | §11 |
| F-20 Relatórios | §10.1 |
| F-21 Redes neurais | §7 |

---

## 17. Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 0.1 | 24/10/2026 | Rascunho inicial: duas trilhas (histórico e com livro), horizontes, alvo, features, baselines, validação, custos, critérios de G2 a pré-registrar e plano de trabalho; PD-15 a PD-23. |
| 0.2 | 26/10/2026 | PD-15 a PD-23 decididas (§1): 15 e 60 min, alvo binário líquido, holdout A de 3 meses, holdout B de 6 semanas depois do congelamento, L = 1 s, custo base 0,10% por lado, critérios de G2 pré-registrados, candles de 2023–2025 como robustez, agregados por minuto numa EC2 dedicada e temporária. |
| 1.0 | 26/10/2026 | Aprovado (DEC-28): catálogo de features, baselines, modelos e grade, validação e plano. §3.4 e §5.1: janelas de até 60 min e do alvo continuam estritas; janelas de 240 min e 24 h usam só minutos válidos e exigem ≥ 95% deles. |
| 1.1 | 31/10/2026 | Pendência do p99 (PD-19) resolvida com a medição da semana do G1; L = 1 s mantida. Método sem mudança. |
| 1.2 | 06/11/2026 | Pendências da PD-23 atualizadas: c7g verificado; seção da EC2 escrita no doc 03 §7.8. Método sem mudança. |
| 1.3 | 06/11/2026 | §12 e §15: c7g recusado pelo plano gratuito na primeira execução; instância passa a c7i-flex.large (DEC-33); tempo da primeira execução medido. Método sem mudança. |
| 1.4 | 07/11/2026 | Aprovado em 07/11/2026 (DEC-35), com os requisitos da etapa 2. DEC-34: §5.1, janelas fixas e valor indefinido tornando o ponto inválido; §5.2, ajustes do catálogo (desequilíbrio assinado relativo ao volume de 24 h, taxa de chegada fora de B6, janelas que faltavam); §5.3, definições exatas das 59 features, minuto válido, disponibilidade e ponto válido; §12, `numpy`. |
