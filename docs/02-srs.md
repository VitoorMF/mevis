# 02 — Software Requirements Specification (SRS)

| Campo | Valor |
|---|---|
| Projeto | **MEVIS** — Market Evidence Intelligence System |
| Documento | 02 — SRS |
| Versão | 0.8 — rascunho |
| Escopo desta versão | **V0.1 (Data Engine) detalhada**, mais os agregados por minuto da etapa 1 da V0.2 (§4.8). O resto da V0.2 e a V0.3 apenas delimitados (§9). |
| Status | Aguardando revisão |
| Data | 23/10/2026 |
| Documentos de origem | [01 — PRD](01-prd.md) v0.6; [03 — Architecture](03-architecture.md) v0.2; [06 — Data & Integrations](06-data-integrations.md) v0.2; [00 — Contexto inicial](00-contexto-inicial.md) |

---

## 0. Como ler este documento

### 0.1 Identificadores

| Prefixo | Uso | Numeração |
|---|---|---|
| `RF-xxx` | Requisito funcional | Centena = versão: `RF-1xx` V0.1, `RF-2xx` V0.2, `RF-3xx` V0.3, `RF-4xx` V0.4. Dezena = grupo. |
| `RNF-xxx` | Requisito não funcional | Sequencial, agrupado por categoria (§5). |
| `RN-xxx` | Regra de negócio | Sequencial; cada regra indica a partir de qual versão se aplica (§6). |
| `UC-xx` | Caso de uso | §7. |

Os identificadores do PRD (`F-xx`, `CA-xx`, `PD-xx`, `DEC-xx`, `PR-xx`, `RS-xx`) são citados na coluna **Origem** de cada requisito. Etiquetas `[PROPOSTA]`, `[A VERIFICAR]` etc. seguem o PRD §0.1.

### 0.2 Linguagem normativa e prioridade

| Termo | Prioridade (MoSCoW) |
|---|---|
| "O sistema **deve**" | Must |
| "O sistema **deveria**" | Should |
| "O sistema **pode**" | Could |

### 0.3 Métodos de verificação

| Sigla | Método |
|---|---|
| **T** | Teste automatizado (unitário, integração ou injeção de falha). |
| **D** | Demonstração em execução real ou de ensaio. |
| **I** | Inspeção de código, configuração ou dados. |
| **A** | Análise de medições, logs ou relatórios. |

### 0.4 Valores numéricos

Limiares marcados **[PROPOSTA]** são pontos de partida. Todos estão reunidos em §11.1 e serão confirmados no doc 06, depois de medições na primeira semana de coleta.

---

## 1. Introdução

### 1.1 Propósito

Especificar, de forma verificável e rastreável, o que o software deve fazer para entregar o MVP descrito no PRD, começando pelo Data Engine (V0.1). Este documento diz **o que** o sistema faz e com que qualidade; **como** (componentes, tecnologias, infraestrutura) é assunto do doc 03.

### 1.2 Escopo

O Data Engine coleta continuamente candles, negócios executados e livro de ofertas de BTC/USDT na Binance; detecta e registra falhas e lacunas; recupera o que for recuperável a partir de fontes oficiais; armazena dados brutos imutáveis e uma camada normalizada reconstruível; avalia a qualidade dos dados; e expõe saúde e alertas ao operador. A coleta contínua roda 24/7 na AWS (DEC-17); o desenvolvimento e a pesquisa acontecem no MacBook.

### 1.3 Referências

| Referência | Uso |
|---|---|
| [01 — PRD](01-prd.md) | Objetivos, funcionalidades, decisões e restrições. |
| [Binance Spot — WebSocket Streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams) | Streams, limites, procedimento de livro local. |
| [Binance Spot — REST API](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints) | Endpoints de dados de mercado, pesos e limites. |
| [Binance Public Data](https://github.com/binance/binance-public-data) | Arquivos históricos (data.binance.vision). |

### 1.4 Definições

Termos gerais estão no glossário do PRD (Apêndice A). Termos específicos deste documento:

| Termo | Definição |
|---|---|
| **Stream** | Fluxo de mensagens de um tipo de dado para um ativo (por exemplo, negócios de BTC/USDT). |
| **Sessão de conexão** | Intervalo entre a abertura e o encerramento de uma conexão WebSocket, com identificador próprio. |
| **Camada bruta** | Mensagens armazenadas exatamente como recebidas, com metadados de recebimento. Imutável. |
| **Camada normalizada** | Registros com esquema canônico, derivados de forma determinística da camada bruta e de fontes de recuperação. |
| **Horário do evento** | Marca de tempo atribuída pela exchange (campos `E`, `T` ou equivalentes). |
| **Horário de recebimento** | Marca de tempo do relógio do host no instante em que a mensagem chega, antes de qualquer processamento. |
| **Lacuna** | Intervalo em que um stream deveria ter dados e não tem. Pode ser **recuperável** (existe fonte oficial para preenchê-la) ou **irrecuperável**. |
| **Máscara de qualidade** | Indicação, por stream e por minuto, de que os dados daquele intervalo são válidos ou não para uso em pesquisa e decisão. |
| **Replay** | Reprodução das mensagens brutas, na ordem de recebimento, através do pipeline. |
| **Rotação de conexão** | Substituição planejada de uma conexão antes do limite de vida imposto pelo servidor. |

---

## 2. Descrição geral

### 2.1 Perspectiva do produto (contexto da V0.1)

```text
                      ┌──────────────────────────────┐
  Binance WebSocket ─►│                              │──► Armazenamento durável (S3)
  (dados de mercado)  │                              │
                      │         DATA ENGINE          │──► Monitoramento / alertas (CloudWatch)
  Binance REST ──────►│  coleta · normalização ·     │          │
  (recuperação)       │  persistência · qualidade    │          ▼
                      │                              │      Operador (e-mail / CLI)
  Arquivos públicos ─►│                              │
  (data.binance.vision)└──────────────┬───────────────┘
                                     │ dados brutos, normalizados, máscara de qualidade
                                     ▼
                         Pesquisador (MacBook) — V0.2
```

O diagrama mostra apenas sistemas externos e fluxos de informação. A divisão interna em componentes e processos será decidida no doc 03.

### 2.2 Classes de usuário

| Classe | Interação com a V0.1 |
|---|---|
| **Operador** | Inicia e para a coleta, consulta o status, recebe alertas. |
| **Pesquisador** | Executa backfill, gera relatórios de qualidade, lê dados e máscara de qualidade. |
| **Desenvolvedor** | Reconstrói a camada normalizada, executa replay e testes. |

As três classes correspondem à mesma pessoa (PRD §6.1).

### 2.3 Ambiente operacional

| Ambiente | Uso | Observações |
|---|---|---|
| MacBook Air M2, macOS ARM64 | Desenvolvimento, testes, pesquisa, backfill pontual. | RS-01 (256 GB, sem operação 24/7). |
| Amazon Linux 2023 (ARM64) em AWS EC2, Ohio (us-east-2) | Coleta contínua 24/7, só pelos endpoints `*.binance.vision` (doc 06 §2.4). | DEC-17, DEC-26; instância e rede no doc 03 §7. |
| Python | Linguagem de implementação. | DEC-06. |

### 2.4 Restrições de projeto e implementação

Herdadas do PRD: RS-01 (hardware), RS-03 (limites e termos da Binance), RS-05 (Python), RS-06 (segurança), RS-09 (orçamento). Consequências diretas para este documento:

- Nenhuma credencial da Binance é necessária ou permitida na V0.1 (RN-001).
- Consumo de CPU, memória, disco, métricas e logs precisa caber em uma instância pequena e no orçamento (RNF-008 a RNF-011, RNF-025).

### 2.5 Fatos verificados sobre a fonte de dados

Verificados na documentação oficial em **23/10/2026**. Devem ser reconfirmados no doc 06 antes da implementação.

| ID | Fato | Impacto nos requisitos |
|---|---|---|
| FV-01 | Uma conexão em `stream.binance.com` vale por **24 horas**; o servidor envia *ping* a cada **20 s** e desconecta se não receber *pong* em **1 minuto**. | RF-114, RF-115 |
| FV-02 | Limites por conexão: **5 mensagens recebidas por segundo** (incluindo ping, pong e mensagens de controle), **1024 streams**; limite de **300 tentativas de conexão a cada 5 minutos por IP**. | RF-113 |
| FV-03 | Há endpoints exclusivos de dados de mercado: `wss://data-stream.binance.vision` (WebSocket) e `https://data-api.binance.vision` (REST). | RF-109 |
| FV-04 | Marcas de tempo do WebSocket vêm em **milissegundos** por padrão; microssegundos são opcionais (`timeUnit=MICROSECOND`). Arquivos públicos de **spot a partir de 01/01/2025 estão em microssegundos**. | RF-123 |
| FV-05 | Stream `trade`: identificador do negócio (`t`), horário do evento (`E`), horário do negócio (`T`) e indicador "comprador é maker" (`m`). | RF-103, RF-104 |
| FV-06 | Stream de kline: campo `x` indica candle fechado; atualizações a cada **2 s** para intervalos diferentes de 1 s. | RF-102 |
| FV-07 | Stream de profundidade parcial: 5, 10 ou 20 níveis, a cada 100 ms ou 1000 ms. O payload contém apenas `lastUpdateId`, `bids` e `asks` — **sem horário do evento**. | RF-105, RF-124, DEC-18 |
| FV-08 | Stream de profundidade diferencial traz `E`, `U` e `u`; a documentação descreve o procedimento oficial de snapshot + buffer para manter um livro local. | RF-191 |
| FV-09 | Stream `bookTicker` (melhor oferta) não traz horário do evento. | RF-106 |
| FV-10 | REST: `historicalTrades` aceita `fromId`, até 1000 negócios por chamada, peso 25 e **não exige chave** (tipo de segurança NONE); só existe em `api.binance.com`, não no endpoint exclusivo de dados de mercado (doc 06, MD-06). `aggTrades` (peso 4) aceita `fromId`, `startTime` e `endTime`. `klines` (peso 2) retorna até 1000 candles, identificados pelo horário de abertura. `/api/v3/time` retorna o horário do servidor. | RF-125, RF-146, RF-147 |
| FV-11 | Excesso de requisições gera HTTP 429 com cabeçalho `Retry-After`; insistir gera HTTP 418 e banimento do IP de 2 minutos a 3 dias. | RF-145 |
| FV-12 | Arquivos públicos de spot (data.binance.vision) incluem `aggTrades`, `klines` e `trades` — **não incluem livro de ofertas**. Cada arquivo tem um `.CHECKSUM` (SHA-256). Arquivos diários são publicados no dia seguinte. | RF-141 a RF-143, RF-146, RN-009 |

**Não verificados:**

- **Bloqueio geográfico (HTTP 451):** não consta na documentação da API. Relatos de comunidade indicam bloqueio para IPs dos EUA e, em alguns casos, para faixas de datacenter fora dos EUA. Por isso, a conectividade a partir da região escolhida precisa ser testada antes de provisionar a coleta (PR-12, R-18).
- **Identificadores de negócio estritamente consecutivos:** sugerido pelo uso de `fromId`, mas não declarado. Será verificado empiricamente na primeira semana (RF-151). Nas amostras de 23/10/2026 não houve salto (doc 06, MD-09).

Medições complementares feitas a partir do MacBook (conectividade, unidades de tempo, volumes, relógio) estão no doc 06 §2.

---

## 3. Modelo conceitual de dados (V0.1)

Entidades e atributos mínimos. Tipos físicos, formatos de arquivo e particionamento ficam para o doc 06.

| Entidade | Atributos mínimos | Chave de unicidade |
|---|---|---|
| **MensagemBruta** | texto recebido; horário de recebimento; id da sessão; nome do stream; número sequencial local na sessão | (sessão, sequencial) |
| **Negócio** | ativo; id do negócio; preço; quantidade; horário do negócio; horário do evento; horário de recebimento; lado agressor; origem | (ativo, id do negócio) |
| **Candle** | ativo; intervalo; abertura; fechamento; OHLC; volume; volume em moeda de cotação; nº de negócios; volume comprador agressor; horário de recebimento do fechamento; origem | (ativo, intervalo, horário de abertura) |
| **SnapshotLivro** | ativo; `lastUpdateId`; níveis de compra e de venda (preço, quantidade); horário de recebimento | (ativo, `lastUpdateId`) — ver nota |
| **SessãoConexão** | id; endpoint; streams; abertura; encerramento; motivo; mensagens recebidas | id |
| **Lacuna** | stream; início; fim; causa; horário de detecção; recuperável (sim/não); status (aberta, recuperada, parcial, irrecuperável); origem da recuperação | (stream, início) |
| **MediçãoRelógio** | horário local; horário do servidor; tempo de ida e volta; offset estimado | horário local |

**Origem** assume um de: `live`, `rest`, `archive`.

**Nota sobre SnapshotLivro:** se o livro não mudar entre dois envios, a exchange pode repetir o mesmo `lastUpdateId`. Nesse caso o registro normalizado mantém o primeiro horário de recebimento; a repetição continua na camada bruta.

---

## 4. Requisitos funcionais — V0.1

### 4.1 Coleta (F-01, F-02, F-03)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-101 | O sistema deve receber continuamente os candles de 1 minuto de BTC/USDT pelo stream de klines. | Must | F-01, DEC-15 | T, D |
| RF-102 | O sistema deve considerar definitivo somente o candle marcado como fechado pela fonte (`x = true`). As atualizações intermediárias ficam apenas na camada bruta. | Must | F-01, RN-006, FV-06 | T |
| RF-103 | O sistema deve receber cada negócio executado de BTC/USDT individualmente (stream `trade`), com id do negócio, preço, quantidade, horário do evento, horário do negócio e indicador de comprador maker. | Must | F-02, FV-05, DEC-19 | T |
| RF-104 | O sistema deve derivar o lado agressor de cada negócio: comprador maker ⇒ agressor **vendedor**; comprador taker ⇒ agressor **comprador**. | Must | F-02, PR-06 | T |
| RF-105 | O sistema deve receber snapshots parciais do livro de BTC/USDT com os **20** melhores níveis de cada lado, a cada **100 ms**. | Must | F-03, DEC-18, FV-07 | T, D |
| RF-106 | O sistema pode receber o stream de melhor oferta (`bookTicker`) de BTC/USDT. | Could | F-03, FV-09 | T |
| RF-107 | O sistema pode importar negócios agregados (`aggTrades`) de BTC/USDT dos arquivos públicos, sob demanda. O stream `aggTrade` não é coletado ao vivo. | Could | F-05, DEC-19 | T |
| RF-108 | Ativos, intervalos de candle, streams e profundidade do livro deveriam ser definidos em configuração, sem alteração de código. No MVP, apenas BTC/USDT é configurado. | Should | F-01–F-03, EX-03 | I |
| RF-109 | O sistema deve usar apenas endpoints públicos de dados de mercado, sem credenciais, dando preferência aos endpoints exclusivos de dados de mercado quando oferecerem os mesmos dados. | Must | RN-001, FV-03 | I |

### 4.2 Conexão e resiliência (F-06)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-111 | O sistema deve detectar perda de conexão por: encerramento do socket, erro de protocolo ou ausência de mensagens em um stream por tempo acima de um limite configurável por stream [PROPOSTA: top 20 e diferencial 2 s; kline 10 s; negócios 30 s]. | Must | F-06 | T |
| RF-112 | O sistema deve reconectar automaticamente, com espera exponencial e variação aleatória (*jitter*) entre tentativas [PROPOSTA: início em 1 s, teto de 60 s], tentando indefinidamente e registrando cada tentativa. | Must | F-06, IS-02 | T |
| RF-113 | O sistema deve respeitar os limites de conexão da fonte (tentativas por IP, mensagens enviadas por segundo, streams por conexão). | Must | RS-03, FV-02 | T, I |
| RF-114 | O sistema deve responder aos *pings* do servidor dentro do prazo exigido pela fonte. | Must | FV-01 | T |
| RF-115 | O sistema deve fazer a **rotação planejada** de cada conexão antes do limite de vida imposto pelo servidor [PROPOSTA: com 23 h de idade]: abrir a nova conexão, confirmar que ela recebe dados, e só então fechar a antiga, eliminando duplicatas da sobreposição. A rotação não pode gerar lacuna. | Must | FV-01, RNF-005 | T, A |
| RF-116 | O sistema deve registrar cada sessão de conexão (entidade SessãoConexão). | Must | F-06 | T |
| RF-117 | Após uma reconexão não planejada, o sistema deve calcular, por stream, o intervalo sem dados, registrar a lacuna (RF-157) e acionar a recuperação aplicável (§4.5). | Must | F-06 | T |
| RF-118 | Uma falha no processamento de um stream não deve interromper a recepção nem o processamento dos demais. | Must | F-06 | T |
| RF-119 | Mensagens malformadas ou desconhecidas devem ser preservadas na camada bruta, contadas e reportadas — nunca descartadas silenciosamente. Taxa acima de um limite gera alerta. | Must | F-06, F-09 | T |

### 4.3 Tempo e normalização (F-07)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-121 | O sistema deve registrar o horário de recebimento de cada mensagem no instante da chegada, antes de qualquer processamento, a partir do relógio do host, em UTC. | Must | F-07, PP-04 | T, I |
| RF-122 | O sistema deve preservar todas as marcas de tempo da exchange presentes na mensagem (horário do evento, do negócio, de abertura e fechamento do candle). | Must | F-07 | T |
| RF-123 | O sistema deve normalizar todas as marcas de tempo para uma única representação: inteiro de microssegundos desde a época Unix, UTC (doc 06 §4.1). A unidade de cada fonte deve ser declarada em configuração e validada por faixa de valores — nunca inferida silenciosamente. | Must | F-07, FV-04 | T |
| RF-124 | Para snapshots de livro sem horário do evento, o horário de recebimento é a referência temporal, e `lastUpdateId` define a ordem. | Must | F-03, FV-07 | T |
| RF-125 | O sistema deve estimar periodicamente o offset entre o relógio do host e o horário do servidor da exchange [PROPOSTA: a cada 5 min], compensando metade do tempo de ida e volta, e registrar cada medição. | Must | PR-10, FV-10 | T, A |
| RF-126 | Preços e quantidades devem ser representados com precisão decimal exata na camada normalizada, sem conversão para ponto flutuante binário. | Must | RN-011 | T |
| RF-127 | Cada registro normalizado deve carregar: ativo, origem (`live`, `rest`, `archive`), procedência (id da sessão ou do lote de recuperação) e versão do esquema. | Must | F-07, F-08 | I |
| RF-128 | A normalização deve ser determinística: a mesma entrada bruta, na mesma versão do software, produz exatamente a mesma saída. | Must | PP-05, IS-09 | T |
| RF-129 | O esquema normalizado deve ser versionado; qualquer mudança gera nova versão e exige reprocessamento documentado. | Must | F-08 | I |

### 4.4 Persistência (F-08)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-131 | O sistema deve gravar cada mensagem recebida na camada bruta, exatamente como recebida, com horário de recebimento, id da sessão e número sequencial. A camada bruta só aceita acréscimos. | Must | F-08, RN-007 | T, I |
| RF-132 | O sistema deve manter a camada normalizada sem duplicatas, usando as chaves de unicidade do §3. | Must | F-08, IS-03 | T |
| RF-133 | O sistema deve permitir reconstruir a camada normalizada, para qualquer período, a partir da camada bruta e das fontes de recuperação. | Must | F-08 | T, D |
| RF-134 | O sistema deve permitir o **replay** das mensagens brutas, na ordem de recebimento e com velocidade configurável, através do mesmo pipeline usado ao vivo. | Must | PP-05, F-28 | T |
| RF-135 | O sistema deve gravar os dados em armazenamento persistente em intervalos que limitem a perda em falha abrupta ao valor de RNF-004. | Must | F-08 | T |
| RF-136 | O sistema deve replicar os arquivos fechados para armazenamento durável fora do host de coleta [PROPOSTA: a cada hora; S3, DEC-17] e confirmar a integridade da cópia por checksum. | Must | F-08, DEC-17 | T, D |
| RF-137 | O sistema deve comprimir os arquivos fechados. | Must | RS-01, CA-06 | I, A |
| RF-138 | O sistema deve aplicar uma política de retenção configurável por camada e por local (disco do host, armazenamento durável), sem nunca apagar do host dados ainda não replicados. | Must | CA-06, RS-09 | T |
| RF-139 | Os dados devem ser legíveis pelo ambiente de pesquisa (MacBook) por período e por stream, sem depender do processo de coleta em execução. | Must | F-08, F-13 | D |

### 4.5 Backfill e recuperação (F-05, F-06)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-141 | O sistema deve importar candles históricos de 1 minuto de BTC/USDT para um período configurável, a partir dos arquivos públicos ou da API REST. | Must | F-05, FV-10, FV-12 | T, D |
| RF-142 | O sistema deve importar negócios históricos de BTC/USDT para um período configurável, a partir dos arquivos públicos ou da API REST. | Must | F-05, FV-12 | T, D |
| RF-143 | O sistema deve verificar o checksum publicado de cada arquivo histórico antes de importá-lo e rejeitar arquivos inválidos. | Must | F-05, FV-12 | T |
| RF-144 | O backfill deve ser idempotente e retomável: interromper e reiniciar não gera duplicatas nem perde progresso. | Must | F-05 | T |
| RF-145 | Toda chamada REST deve respeitar os limites de peso da fonte; ao receber HTTP 429, o sistema deve aguardar o tempo indicado em `Retry-After` antes de tentar de novo, e nunca provocar HTTP 418. | Must | RS-03, FV-11 | T |
| RF-146 | O sistema deve recuperar automaticamente lacunas de negócios pelo intervalo de ids, via REST (`historicalTrades` com `fromId`). Se a recuperação via REST não for possível, deve agendá-la a partir do arquivo diário, quando publicado. | Must | F-06, FV-10, FV-12 | T |
| RF-147 | O sistema deve recuperar automaticamente candles fechados ausentes via REST. | Must | F-06, FV-10 | T |
| RF-148 | O sistema deve marcar lacunas de livro de ofertas como irrecuperáveis, sem preenchimento sintético. | Must | RN-008, RN-009 | T |
| RF-149 | Dados recuperados devem passar pela mesma validação dos dados ao vivo e ser marcados com sua origem; o status da lacuna correspondente deve ser atualizado. | Must | F-05, F-09 | T |

### 4.6 Qualidade dos dados (F-09)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-151 | O sistema deve detectar lacunas na sequência de ids de negócios. A premissa de ids consecutivos deve ser verificada empiricamente na primeira semana; se falhar, o critério será redefinido no doc 06. | Must | F-09, PR-06 | T, A |
| RF-152 | O sistema deve detectar minutos UTC sem candle fechado. | Must | F-09 | T |
| RF-153 | O sistema deve detectar livro desatualizado (intervalo entre snapshots acima do limite) e regressão de `lastUpdateId`. | Must | F-09 | T |
| RF-154 | O sistema deve detectar duplicatas e mensagens fora de ordem em cada stream. | Must | F-09 | T |
| RF-155 | O sistema deve validar a consistência dos registros: preço e quantidade positivos; mínimo ≤ abertura, fechamento ≤ máximo; volume não negativo; melhor compra < melhor venda; níveis de compra decrescentes e de venda crescentes; horário do negócio compatível com o horário de recebimento dentro de uma tolerância. | Must | F-09 | T |
| RF-156 | O sistema deveria comparar os candles recebidos com os candles reconstruídos a partir dos negócios (RF-171) e registrar divergências. | Should | F-09, F-11, IS-03 | T, A |
| RF-157 | O sistema deve manter um registro de lacunas (entidade Lacuna), atualizado na detecção e na recuperação. | Must | F-06, F-09 | T |
| RF-158 | O sistema deve gerar, sob demanda e para qualquer período, um relatório de qualidade com: completude por stream; lacunas por status; duplicatas; anomalias; distribuição do atraso entre horário do evento e de recebimento; offset de relógio; reconexões. Sem o registro de lacunas e sessões do período, o relatório deve falhar com mensagem clara, nunca informar "nenhuma". | Must | F-09, CA-05 | T, D |
| RF-159 | O sistema deve produzir a **máscara de qualidade** por stream e por minuto, legível pela pesquisa (V0.2) e pelo Risk Manager (V0.3). | Must | F-09, PP-03, RN-005 | T |
| RF-160 | O pipeline deve conferir, independentemente do coletor e **entre sessões e instâncias**, a continuidade dos ids de negócios e do diferencial do livro e o intervalo entre snapshots top 20, inclusive na virada de hora. Todo buraco sem lacuna correspondente deve ser registrado (RF-157) e seguir a recuperação do canal (RF-146, RF-148). | Must | F-09, IS-01 | T |

### 4.7 Monitoramento e alertas (F-10)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-161 | O sistema deve calcular métricas de saúde: por stream (mensagens por segundo, idade da última mensagem, atraso p50/p99 entre evento e recebimento, lacunas abertas, reconexões) e do host (uso de disco, memória e CPU; tempo de atividade do processo). | Must | F-10 | T |
| RF-162 | O sistema deveria publicar as métricas em um serviço de monitoramento externo ao processo [PROPOSTA: a cada 1 min; CloudWatch, DEC-17], dentro dos limites de custo (RNF-025). | Should | F-10, DEC-17 | D |
| RF-163 | O sistema deve gerar alertas para: stream silencioso acima do limite; falhas de reconexão consecutivas acima de N; disco acima de 80%; offset de relógio acima do limite; falha de replicação; processo parado. | Must | F-10 | T, D |
| RF-164 | A ausência de sinal de vida do coletor por mais de um intervalo configurável deve gerar alerta a partir de um mecanismo **externo** ao coletor (*dead man's switch*). | Must | F-10 | D |
| RF-165 | Os alertas devem chegar ao operador por um canal fora do terminal (PD-13). | Must | F-10 | D |
| RF-166 | Um alerta repetido deveria ser enviado no máximo uma vez por intervalo configurável enquanto a condição persistir, com notificação quando a condição terminar. | Should | F-10 | T |

### 4.8 Agregações derivadas (F-11)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-171 | O sistema deveria reconstruir candles de 1 minuto a partir dos negócios normalizados. | Should | F-11 | T |
| RF-172 | O sistema deve calcular, por minuto UTC: volume total, volume com agressor comprador, volume com agressor vendedor, número de negócios e VWAP (`minute_trades`, doc 06 §5.6). | Must | F-11, doc 04 §5.2 | T |
| RF-173 | As agregações devem ser determinísticas e reproduzíveis a partir da camada normalizada: as mesmas entradas e a mesma versão do código geram o mesmo conteúdo. | Must | F-11, PP-05, CA-07 | T |
| RF-174 | O sistema deve calcular, por minuto, a distribuição dos negócios em faixas de tamanho (contagem e volume por faixa), suficiente para o limite de negócios grandes de B6 (doc 06 §5.6; DEC-31). | Must | doc 04 §5.2 | T |
| RF-175 | O sistema deve calcular, por minuto, as medidas do livro top 20 (spread relativo, desequilíbrios de 1, 5, 10 e 20 níveis, distância do microprice), com média, mínimo e último valor (`minute_book`, doc 06 §5.6). | Must | doc 04 §5.2 (B7) | T |
| RF-176 | Cada minuto agregado deve ter um status: o da máscara de qualidade, para dados ao vivo; para dias de arquivo, `missing` nos minutos entre os dois negócios de um salto de id, inclusive na virada do dia, e `ok` nos demais. Todo minuto do período tem uma linha. | Must | doc 04 §3.4, RN-008 | T |
| RF-177 | O agregado ao vivo deve guardar o último horário de recebimento do minuto, para que o uso em uma decisão respeite a disponibilidade *point-in-time* (doc 04 §3.5). | Must | PP-04, RN-006 | T |
| RF-178 | O mesmo código deve agregar dias de arquivo, horas ao vivo e, na V0.3, os minutos da operação; ele recebe registros normalizados e não sabe de onde vieram. | Must | PP-05 | T, I |
| RF-179 | A agregação de negócios do período de holdout da trilha A só pode rodar com uma opção explícita, registrada no manifesto da execução, reservada à avaliação final (doc 04 §3.3; DEC-31). | Must | IS-12 | T |

### 4.9 Operação e configuração

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-181 | O sistema deve oferecer comandos de linha de comando para: iniciar e parar a coleta, consultar status, executar backfill e gerar relatório de qualidade. Deveria oferecer também: recuperar lacunas manualmente, reconstruir a camada normalizada, executar replay e consultar uso de armazenamento. | Must / Should | PD-10 | D |
| RF-182 | A parada solicitada deve ser ordenada: gravar buffers, fechar arquivos e registrar o fim da sessão, sem perda de dados recebidos. | Must | F-08 | T |
| RF-183 | O comando de status deve mostrar, por stream: estado da conexão, idade da última mensagem e lacunas das últimas 24 h; e, no geral: uso de disco e horário da última replicação. | Must | F-10, US-06 | D |
| RF-184 | A configuração deve vir de arquivo e de variáveis de ambiente, ser validada na inicialização e impedir o início, com mensagem clara, se for inválida. | Must | RNF-016 | T |
| RF-185 | Cada sessão deve registrar a versão do código e a configuração efetiva (sem segredos). | Must | IS-09 | I |
| RF-187 | O processamento pesado sob demanda (doc 03 §7.8) deve: encerrar e apagar a instância ao terminar ou ao atingir um limite de horas; gravar um manifesto com versão do código, argumentos, arquivos de entrada e de saída com SHA-256, linhas e duração; e poder ser repetido sem duplicar saídas. | Must | RNF-023, CA-07 | T, D |
| RF-186 | A troca de versão do coletor só pode parar a instância antiga depois que a nova, **iniciada nesta troca**, estiver recebendo todos os canais; dados de execuções anteriores não contam como prontidão. As duas devem se sobrepor por um tempo mínimo [PROPOSTA: 5 s]. | Must | F-06, IS-01 | T, D |

### 4.10 Livro de ofertas: diferencial e reconstrução (F-03, F-04)

| ID | Requisito | Prior. | Origem | Verif. |
|---|---|---|---|---|
| RF-191 | O sistema pode manter um livro local completo a partir do stream diferencial e de um snapshot REST, seguindo o procedimento oficial da fonte; deve validar a continuidade dos ids de atualização, ressincronizar ao detectar descontinuidade e registrar cada ressincronização como lacuna. | Could | F-04, DEC-18, FV-08 | T |
| RF-192 | O sistema deve receber o stream diferencial do livro de BTC/USDT a cada 100 ms e gravá-lo na camada bruta. | Must | F-03, DEC-18 | T |
| RF-193 | O sistema deve obter snapshots REST completos do livro (5000 níveis por lado) no início de cada sessão, periodicamente [PROPOSTA: a cada 15 min] e após cada quebra de continuidade do diferencial, gravando-os na camada bruta. | Must | F-03, F-04, DEC-18 | T |
| RF-194 | O sistema deve verificar a continuidade do diferencial (`U` igual ao `u` anterior + 1) e registrar cada quebra como lacuna do diferencial. | Must | F-09, DEC-18 | T |

---

## 5. Requisitos não funcionais

| ID | Categoria | Requisito | Origem | Verif. |
|---|---|---|---|---|
| RNF-001 | Disponibilidade | A coleta deve estar ativa em ≥ 99,5% do tempo em qualquer janela de 7 dias (DEC-30). | IS-01, CA-01 | A |
| RNF-002 | Disponibilidade | Após queda de conexão, o recebimento deve ser retomado em até 30 s no p95 (DEC-30). | IS-02 | T, A |
| RNF-003 | Disponibilidade | O coletor deve iniciar automaticamente com o host e ser reiniciado automaticamente em até 60 s após uma falha do processo [PROPOSTA]. | IS-04 | D |
| RNF-004 | Confiabilidade | Em falha abrupta do processo, a perda de mensagens já recebidas deve ser de no máximo 5 s [PROPOSTA]. | F-08 | T |
| RNF-005 | Confiabilidade | Zero lacunas atribuíveis à rotação planejada de conexões. | RF-115 | A |
| RNF-006 | Desempenho | Atraso interno entre recebimento e gravação na camada bruta ≤ 1 s no p99, em operação normal [PROPOSTA]. | F-08 | A |
| RNF-007 | Desempenho | O sistema deve suportar, por 5 minutos, picos de 10× a taxa média de mensagens medida na primeira semana, sem perda e sem atraso crescente [PROPOSTA]. | F-01–F-03 | T |
| RNF-008 | Recursos | Memória residente do coletor ≤ 300 MB [PROPOSTA], para caber em uma instância pequena. | RS-09 | A |
| RNF-009 | Recursos | O uso médio de CPU deve ficar abaixo da linha de base da instância escolhida. Em instâncias com créditos de CPU, uso sustentado acima da linha de base pode gerar cobrança adicional; o doc 03 adota o modo `standard`, que limita em vez de cobrar (ADR-07). | RS-09 | A |
| RNF-010 | Armazenamento | O crescimento diário de cada camada deve ser medido e projetado; o disco do host nunca deve passar de 80% de ocupação. | CA-06, RS-01 | A |
| RNF-011 | Armazenamento | O custo mensal projetado de armazenamento deve caber no orçamento (RS-09). | RS-09 | A |
| RNF-012 | Tempo | O relógio do host deve ser sincronizado por NTP; marcas de tempo com resolução de pelo menos 1 ms; alerta se o offset estimado em relação à exchange passar de 250 ms [PROPOSTA]. | PR-10 | T, A |
| RNF-013 | Segurança | A V0.1 não usa nenhuma credencial da Binance. | RN-001 | I |
| RNF-014 | Segurança | O acesso a serviços de nuvem a partir do host deve usar credenciais temporárias atribuídas à instância, sem chaves de longa duração gravadas no host, com privilégio mínimo (escrever somente no local de armazenamento do projeto; publicar métricas e logs). | RS-06 | I |
| RNF-015 | Segurança | Nenhuma porta de entrada aberta para a internet além do estritamente necessário para administração, por canal autenticado e restrito (definido no doc 03). | RS-06 | I |
| RNF-016 | Segurança | Segredos nunca aparecem no repositório, em arquivos de configuração versionados ou em logs. | RS-06 | I, T |
| RNF-017 | Segurança | Dados replicados devem ficar criptografados em repouso e sem acesso público. | RS-06 | I |
| RNF-018 | Observabilidade | Logs estruturados, com horário UTC, nível, componente e id da sessão; retenção definida conforme custo. | F-10 | I |
| RNF-019 | Manutenibilidade | Conexão, normalização, persistência, validação e monitoramento devem ser testáveis isoladamente. | PP-07 | I |
| RNF-020 | Manutenibilidade | Testes automatizados devem cobrir: parsing; normalização de tempo (ms e µs); derivação do agressor; detecção de lacunas; deduplicação; reconexão e rotação (contra um servidor simulado). | IS-11 | T |
| RNF-021 | Reprodutibilidade | Dependências fixadas (arquivo de lock) e ambiente reproduzível em qualquer máquina. | IS-09 | I |
| RNF-022 | Portabilidade | O mesmo código deve executar em macOS ARM64 e em Linux (ARM64 e x86_64) sem alterações. | DEC-17 | D |
| RNF-023 | Custo | Todo recurso de nuvem deve ter a tag do projeto; a estimativa de custo mensal deve estar documentada e aprovada antes do provisionamento. | RN-012, RS-09 | I |
| RNF-024 | Custo | Um alerta de orçamento deve estar ativo antes do início da coleta contínua. | R-17 | I |
| RNF-025 | Custo | Métricas personalizadas, alarmes e volume de logs devem ser dimensionados para a camada gratuita sempre que possível; cada métrica e alarme acima dela precisa de justificativa de custo. | RS-09 | I, A |
| RNF-027 | Desempenho | A agregação deve processar um dia de negócios com memória limitada (em blocos), sem carregar o dia inteiro: até 2 GiB por processo no pior dia medido (15,4 milhões de negócios, MD-27). | PD-23 | T, A |
| RNF-026 | Conformidade | O sistema deve respeitar os limites e os termos de uso da Binance. | RS-03, RN-015 | I |

---

## 6. Regras de negócio

Regras que valem para o produto inteiro, já decididas no PRD. A coluna "Desde" indica a primeira versão em que a regra tem efeito no software.

| ID | Regra | Origem | Desde |
|---|---|---|---|
| RN-001 | O sistema não pode conter, aceitar nem usar credenciais de conta real (mainnet) durante o MVP. | EX-01, DEC-11 | V0.1 |
| RN-002 | A posição nunca pode ser negativa: SELL apenas reduz ou encerra uma posição comprada. | DEC-14 | V0.3 |
| RN-003 | Sem alavancagem: o valor das posições nunca excede o capital simulado disponível. | DEC-11 | V0.3 |
| RN-004 | Toda intenção de ordem passa pelo Risk Manager; não existe caminho alternativo até a execução. | PP-02 | V0.3 |
| RN-005 | Com lacuna aberta, dados atrasados além do limite ou inconsistentes em um stream exigido pela estratégia, é proibido abrir ou aumentar posição. A V0.1 fornece os sinais para essa regra (RF-159, RF-161). | PP-03 | V0.1 (sinais) / V0.3 (efeito) |
| RN-006 | Um dado só pode ser usado em feature ou decisão a partir do seu horário de recebimento; um candle, a partir do recebimento do seu fechamento. Para dados históricos sem horário de recebimento, o doc 04 define a latência assumida. | PP-04 | V0.1 |
| RN-007 | Dados brutos são imutáveis; só podem ser removidos pela política de retenção documentada. | F-08 | V0.1 |
| RN-008 | Lacunas nunca são preenchidas por interpolação ou dados sintéticos; só por dados reais de fonte oficial, com origem marcada. | F-09 | V0.1 |
| RN-009 | Lacunas de livro de ofertas são irrecuperáveis. | FV-12 | V0.1 |
| RN-010 | Toda informação temporal é armazenada e processada em UTC. | F-07 | V0.1 |
| RN-011 | Preços e quantidades usam precisão decimal exata, sem arredondamento. | F-07 | V0.1 |
| RN-012 | Recursos de nuvem só são criados com estimativa de custo aprovada pelo responsável pelo produto e com a tag do projeto. | DEC-16 | V0.1 |
| RN-013 | Os critérios de aprovação de modelos (G2) são pré-registrados antes de qualquer avaliação no holdout. | PRD §8.4 | V0.2 |
| RN-014 | Toda decisão, inclusive NO TRADE, é registrada no diário de decisões. | PP-06 | V0.3 |
| RN-015 | É proibido contornar restrições geográficas ou de acesso da exchange (por exemplo, com proxy ou VPN para mascarar a localização). | RS-03, RS-07 | V0.1 |

---

## 7. Casos de uso — V0.1

### UC-01 — Iniciar a coleta contínua

- **Ator:** Operador.
- **Pré-condições:** configuração válida; armazenamento disponível.
- **Fluxo principal:**
  1. O operador executa o comando de início.
  2. O sistema valida a configuração e registra versão do código e configuração efetiva.
  3. O sistema abre a conexão e assina os streams configurados.
  4. O sistema registra a nova sessão de conexão.
  5. O sistema passa a gravar a camada bruta e a normalizada.
  6. O comando de status mostra todos os streams ativos.
- **Fluxos alternativos:** 2a. Configuração inválida → o sistema não inicia e explica o erro. 3a. Falha de conexão → UC-02.
- **Pós-condição:** coleta ativa e monitorada.
- **Requisitos:** RF-101–RF-105, RF-116, RF-181, RF-184, RF-185.

### UC-02 — Recuperar-se de uma queda de conexão

- **Ator:** Sistema.
- **Gatilho:** encerramento do socket, erro ou silêncio acima do limite (RF-111).
- **Fluxo principal:**
  1. O sistema detecta a queda e encerra o registro da sessão.
  2. O sistema tenta reconectar com espera exponencial.
  3. Ao reconectar, abre nova sessão e reassina os streams.
  4. Calcula, por stream, o intervalo sem dados e registra as lacunas.
  5. Aciona a recuperação aplicável (UC-05).
- **Fluxos alternativos:** 1a. Silêncio acima do limite em parte dos streams, com o socket ainda ativo → o sistema abre a nova sessão **antes** de encerrar a antiga, como na rotação (UC-03): a antiga continua gravando os streams que ainda chegam até a nova receber todos, ou até o prazo de troca; aí a nova assume e a antiga é encerrada. Se a nova sessão não abrir, segue o fluxo principal. 2a. Falhas consecutivas acima de N → alerta (UC-08). 2b. Limite de tentativas por IP próximo → aguarda.
- **Pós-condição:** coleta retomada; lacunas registradas e em recuperação; com o socket ativo, nenhum stream que ainda chegava perde dados na troca.
- **Requisitos:** RF-111–RF-119, RF-157, RF-163, RNF-002.

### UC-03 — Rotação planejada de conexão

- **Ator:** Sistema.
- **Gatilho:** idade da conexão atinge o limite configurado (RF-115).
- **Fluxo principal:**
  1. O sistema abre uma nova conexão com os mesmos streams.
  2. Confirma que a nova conexão recebe dados de todos os streams.
  3. Elimina duplicatas do período de sobreposição.
  4. Fecha a conexão antiga e registra as duas sessões.
- **Fluxos alternativos:** 1a. A nova conexão falha → mantém a antiga e tenta de novo com espera. 1b. A antiga cai antes da troca → UC-02.
- **Pós-condição:** nenhuma lacuna.
- **Requisitos:** RF-115, RF-116, RNF-005.

### UC-04 — Importar histórico (backfill)

- **Ator:** Pesquisador.
- **Fluxo principal:**
  1. O pesquisador executa o backfill informando stream e período.
  2. O sistema planeja arquivos e requisições necessários.
  3. Baixa os arquivos e verifica os checksums.
  4. Normaliza, valida e grava de forma idempotente, com origem marcada.
  5. Emite um relatório do que foi importado e do que falhou.
- **Fluxos alternativos:** 3a. Checksum inválido → rejeita o arquivo e registra. 3b. HTTP 429 → aguarda `Retry-After`. 4a. Interrupção → ao reiniciar, retoma do ponto onde parou.
- **Requisitos:** RF-141–RF-145, RF-149.

### UC-05 — Recuperar uma lacuna

- **Ator:** Sistema (automático) ou Operador (manual).
- **Fluxo principal (negócios):**
  1. Para uma lacuna entre os ids *a* e *b*, o sistema consulta a REST em lotes a partir de *a*.
  2. Valida e grava os negócios com origem `rest`.
  3. Atualiza o status da lacuna para recuperada.
- **Fluxos alternativos:** 1a. Dados indisponíveis via REST → agenda a recuperação pelo arquivo diário. 1b. Lacuna de candle → consulta klines via REST. 1c. Lacuna de livro → marca como irrecuperável.
- **Requisitos:** RF-146–RF-149, RF-157.

### UC-06 — Gerar relatório de qualidade

- **Ator:** Pesquisador.
- **Fluxo:** informa o período → o sistema calcula as métricas de RF-158 e a máscara de qualidade (RF-159) → entrega o relatório.
- **Requisitos:** RF-151–RF-159.

### UC-07 — Consultar status

- **Ator:** Operador.
- **Fluxo:** executa o comando de status → o sistema mostra as informações de RF-183.
- **Requisitos:** RF-161, RF-183.

### UC-08 — Receber um alerta

- **Ator:** Operador.
- **Gatilho:** condição de alerta (RF-163) ou ausência de sinal de vida (RF-164).
- **Fluxo:** o sistema ou o mecanismo externo detecta a condição → envia o alerta pelo canal definido → repete com limitação → avisa quando a condição termina.
- **Requisitos:** RF-163–RF-166.

### UC-09 — Reconstruir a camada normalizada

- **Ator:** Desenvolvedor.
- **Fluxo:** informa o período → o sistema reprocessa a camada bruta e as fontes de recuperação → substitui a camada normalizada do período → registra a versão usada.
- **Requisitos:** RF-128, RF-129, RF-133.

### UC-10 — Replicar e aplicar retenção

- **Ator:** Sistema (agendado).
- **Fluxo:** replica arquivos fechados → confirma checksum → aplica retenção no host apenas a dados já replicados → registra o resultado e alerta em caso de falha.
- **Requisitos:** RF-136–RF-138, RF-163.

---

## 8. Critérios de aceitação da V0.1

Desdobramento dos critérios CA-01 a CA-06 do PRD em procedimentos verificáveis.

| CA (PRD) | Requisitos | Procedimento de verificação |
|---|---|---|
| CA-01 | RF-101–RF-105, RNF-001, RNF-002, RNF-003 | Executar a coleta por 7 dias contínuos na hospedagem definitiva; o relatório de qualidade do período (RF-158) mostra RNF-001 e RNF-002 atendidos, e a conferência de continuidade (RF-160) não encontra buraco sem lacuna registrada. |
| CA-02 | RF-111–RF-117, RF-157, RNF-005 | Testes de injeção de falha (queda de rede, fim do processo, servidor que não responde a ping) com lacunas registradas e reconexão automática; ao menos uma rotação de 24 h observada sem lacuna. |
| CA-03 | RF-121–RF-127, RF-131, RF-132 | Inspeção por amostragem dos registros e testes automatizados de deduplicação e de marcas de tempo. |
| CA-04 | RF-141–RF-145, RF-149 | Importar candles e negócios de um período configurável [PROPOSTA: 90 dias], com checksums verificados e validação aprovada; repetir a importação sem gerar duplicatas. |
| CA-05 | RF-158, RF-159 | Gerar relatórios para três períodos arbitrários, incluindo um com lacuna conhecida. |
| CA-06 | RF-136–RF-138, RNF-010, RNF-011 | Medir GB/dia por camada e por local; documentar a política de compressão e retenção; demonstrar a retenção aplicada sem apagar dados não replicados. |

---

## 9. V0.2 e V0.3 — escopo a detalhar

Estes requisitos serão escritos em versão futura do SRS, depois dos documentos indicados. A tabela fixa as faixas de ids e as dependências.

| Versão | Grupo | Funcionalidades | Faixa de ids | Depende de |
|---|---|---|---|---|
| V0.2 | Features point-in-time | F-12 | RF-201–RF-219 | Doc 04 (features, horizontes, latência assumida) |
| V0.2 | Datasets versionados | F-13 | RF-221–RF-229 | Doc 04, doc 06 |
| V0.2 | Baselines, treinamento e validação | F-14, F-15, F-16 | RF-231–RF-249 | Doc 04 (targets, walk-forward, holdout) |
| V0.2 | Backtester com custos | F-17 | RF-251–RF-269 | Docs 04 e 05 (custos, modelo de preenchimento) |
| V0.2 | Ablações, registro e relatórios | F-18, F-19, F-20 | RF-271–RF-289 | Docs 04 e 07 |
| V0.3 | Signal Engine | F-22 | RF-301–RF-309 | Doc 05 |
| V0.3 | Risk Manager e kill switch | F-23, F-24 | RF-311–RF-329 | Doc 05 (limites) |
| V0.3 | Execução simulada, testnet e posições | F-25, F-26, F-27 | RF-331–RF-349 | Doc 05 (estados de ordem, reconciliação) |
| V0.3 | Paper trading, diário e monitoramento | F-28, F-29, F-30 | RF-351–RF-369 | Docs 03 e 05 |

Requisitos da V0.1 que já servem às versões seguintes: replay (RF-134) para a paridade backtest–paper; máscara de qualidade (RF-159) para datasets e para RN-005; métricas de saúde (RF-161) para o fail-safe do Risk Manager.

---

## 10. Rastreabilidade (V0.1)

| Funcionalidade (PRD) | Requisitos | Casos de uso | Aceitação |
|---|---|---|---|
| F-01 Candles | RF-101, RF-102, RF-108 | UC-01 | CA-01 |
| F-02 Negócios | RF-103, RF-104 | UC-01 | CA-01 |
| F-03 Livro de ofertas | RF-105, RF-106, RF-124, RF-192, RF-193 | UC-01 | CA-01 |
| F-04 Livro local completo | RF-191 (ao vivo); RF-192–RF-194 (dados para reconstrução) | — | — |
| F-05 Backfill | RF-107, RF-141–RF-149 | UC-04, UC-05 | CA-04 |
| F-06 Resiliência | RF-111–RF-119; RNF-001–RNF-005 | UC-02, UC-03 | CA-01, CA-02 |
| F-07 Tempo e normalização | RF-121–RF-129; RNF-012 | — | CA-03 |
| F-08 Persistência | RF-131–RF-139; RNF-004, RNF-010, RNF-011 | UC-09, UC-10 | CA-03, CA-06 |
| F-09 Qualidade | RF-151–RF-160, RF-194 | UC-06 | CA-05 |
| F-10 Monitoramento | RF-161–RF-166; RNF-018 | UC-07, UC-08 | CA-01 |
| F-11 Agregações | RF-171–RF-179, RNF-027 | — | IS-03 |
| Transversal: operação | RF-181–RF-187 | UC-01, UC-07 | — |
| Transversal: segurança e custo | RN-001, RN-012, RN-015; RNF-013–RNF-017, RNF-023–RNF-025 | — | — |

---

## 11. Questões em aberto

### 11.1 Valores propostos a confirmar no doc 06

| Parâmetro | Valor proposto | Requisito |
|---|---|---|
| Limite de silêncio por stream | Top 20 e diferencial 2 s; kline 10 s; negócios 30 s | RF-111 |
| Espera entre reconexões | Início 1 s, teto 60 s, com jitter | RF-112 |
| Idade para rotação planejada | 23 h | RF-115 |
| Intervalo de medição de offset | 5 min | RF-125 |
| Alerta de offset de relógio | 250 ms | RNF-012 |
| Representação de tempo | Inteiro de microssegundos, UTC — **decidido** (doc 06 §4.1) | RF-123 |
| Profundidade e frequência do livro | 20 níveis a cada 100 ms — **decidido** (DEC-18) | RF-105 |
| Snapshot REST completo do livro | Início da sessão, a cada 15 min e após quebra de continuidade | RF-193 |
| Perda máxima em falha abrupta | 5 s | RNF-004 |
| Replicação para armazenamento durável | A cada hora | RF-136 |
| Ocupação máxima de disco | 80% | RNF-010, RF-163 |
| Memória do coletor | 300 MB | RNF-008 |
| Disponibilidade e recuperação | 99,5% em 7 dias; p95 30 s | RNF-001, RNF-002 |
| Período de backfill para aceitação | 90 dias | CA-04 |

### 11.2 Decisões pendentes

Registradas no PRD §17.2, conforme a convenção da documentação:

Nenhuma pendência aberta que afete a V0.1. Resolvidas em 23/10/2026: PD-06 → DEC-18; PD-14 → DEC-19 (doc 06 §1); PD-13 → DEC-24 (e-mail via SNS, doc 03 ADR-12).

---

## 12. Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 0.1 | 23/10/2026 | Rascunho inicial: V0.1 detalhada; V0.2 e V0.3 delimitadas; fatos da API Binance verificados na documentação oficial. |
| 0.2 | 23/10/2026 | Alinhado ao doc 06: RF-192 a RF-194 (diferencial e snapshots do livro); RF-107 passa a backfill de `aggTrades`; unidade de tempo decidida; PD-06 e PD-14 resolvidas. |
| 0.3 | 23/10/2026 | Ambiente operacional alinhado ao doc 03 (Tóquio, Amazon Linux 2023 ARM64); RNF-009 e PD-13 referenciam as propostas do doc 03. |
| 0.4 | 23/10/2026 | PD-13 resolvida (DEC-24). |
| 0.5 | 23/10/2026 | Ambiente operacional na us-east-2 (DEC-26). Na produção, RF-146 recorre ao arquivo diário, porque `historicalTrades` responde 451 de lá (doc 06 MD-18). |
| 0.6 | 25/10/2026 | UC-02, fluxo 1a: com o socket ativo, o silêncio de parte dos streams abre a nova sessão antes de encerrar a antiga. |
| 0.7 | 31/10/2026 | Aprovado em 31/10/2026. Depois de buracos não registrados nas trocas de versão (doc 06 MD-28): RF-160 (continuidade conferida pelo pipeline, entre sessões), RF-186 (prontidão real na troca), RF-158 falha sem o registro de lacunas e sessões; CA-01 inclui a conferência. |
| 0.8 | 06/11/2026 | Aprovado em 06/11/2026 (DEC-32). Etapa 1 da V0.2: RF-172 e RF-173 passam a Must; RF-174 a RF-179 (agregados por minuto), RF-187 (processamento sob demanda) e RNF-027. |
