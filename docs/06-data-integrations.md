# 06 — Data & Integrations Specification

| Campo | Valor |
|---|---|
| Projeto | **MERIS** — Market Evidence & Research Intelligence System |
| Documento | 06 — Data & Integrations |
| Versão | 0.12 — rascunho |
| Escopo desta versão | Dados de mercado da Binance para a V0.1, com as necessidades de dados da V0.2 e da V0.3 que já afetam o formato. Fontes macro (V0.4) apenas citadas (§11). |
| Status | Aguardando revisão |
| Data | 23/10/2026 |
| Documentos de origem | [01 — PRD](01-prd.md) v0.5; [02 — SRS](02-srs.md) v0.3; [03 — Architecture](03-architecture.md) v0.1 |

---

## 0. Escopo e relação com os outros documentos

Este documento define **quais dados** o sistema obtém, **de onde**, **com que contrato** (campos, unidades, limites) e **como ficam guardados** (camadas, formatos, esquemas, retenção, validação e recuperação). Não define:

- componentes, processos, região e instância da AWS → doc 03;
- features, targets e o modelo de latência dos dados históricos → doc 04.

Requisitos do SRS que este documento detalha estão na matriz do §14.

---

## 1. Decisões tomadas neste documento

Decididas com o responsável pelo produto em 23/10/2026 e registradas no PRD §17.

| ID | Tema | Decisão | Resolve |
|---|---|---|---|
| DEC-18 | Livro de ofertas | Coletar o **parcial top 20 a cada 100 ms** (camada normalizada) e o **stream diferencial a cada 100 ms**, gravado só na camada bruta, com **snapshots REST completos** periódicos. A reconstrução do livro completo fica para depois (F-04, Could), mas os dados para ela passam a existir desde o primeiro dia. | PD-06 |
| DEC-19 | Negócios | Coletar ao vivo só o stream **`trade`**. Negócios agregados (`aggTrades`) são importados dos arquivos públicos quando a pesquisa precisar. | PD-14 |
| DEC-20 | Formato de armazenamento | **Arquivos, sem servidor de banco.** Camada bruta em JSONL comprimido com zstd, por stream e por hora; camada normalizada em Parquet, por dataset e por dia; leitura com DuckDB, Polars ou pandas. | PD-12 (parte de dados de mercado) |

**Por que gravar o stream diferencial sem usá-lo ainda:** o histórico do livro não pode ser obtido depois (FV-12). O diferencial é o único dado que permite reconstruir o livro além dos 20 níveis e que traz horário do evento (FV-07, FV-08). Gravá-lo custa cerca de 116 MB/dia comprimido (§6.2); não gravá-lo elimina essa possibilidade para todo o período coletado.

---

## 2. Medições de referência

Feitas em 23/10/2026, a partir do MacBook no Brasil, com scripts descartáveis (fora do repositório). Servem para dimensionar; serão refeitas a partir da região AWS escolhida (doc 03) e na primeira semana da V0.1.

### 2.1 Conectividade e unidades

| ID | Medição | Resultado |
|---|---|---|
| MD-01 | REST `api.binance.com` a partir do Brasil | HTTP 200; ~0,33 s por requisição. |
| MD-02 | REST `data-api.binance.vision` a partir do Brasil | HTTP 200; ~1,3 s por requisição. |
| MD-03 | WebSocket `wss://data-stream.binance.vision` | Conecta e entrega os streams usados. |
| MD-04 | `timeUnit=MICROSECOND` no WebSocket | Funciona: `E` e `T` chegam com 16 dígitos (µs). |
| MD-05 | Cabeçalho `X-MBX-TIME-UNIT: MICROSECOND` na REST | Funciona em `data-api.binance.vision` e `api.binance.com`. |
| MD-06 | `historicalTrades` em `data-api.binance.vision` | **HTTP 404** — o endpoint só existe em `api.binance.com` (sem chave). |
| MD-07 | Alcance de `historicalTrades` (`fromId=0`) | O negócio mais antigo devolvido é de 23/10/2024: a REST guarda cerca de **2 anos** de negócios. |
| MD-08 | Limites REST (`exchangeInfo`) | `REQUEST_WEIGHT` 6000 por minuto; `RAW_REQUESTS` 300 000 a cada 5 minutos. |
| MD-09 | Ids de negócio | Consecutivos em 1000 negócios via REST e em ~3000 via WebSocket (nenhuma lacuna). |
| MD-10 | Continuidade do diferencial | 186 mensagens em 20 s com `U = u anterior + 1` em todas. |
| MD-11 | Parcial top 20 | Sem campo `E`; ~8,7 mensagens/s, abaixo dos 10/s nominais [a investigar na V0.1]. |
| MD-12 | Filtros de BTC/USDT | `tickSize` 0,01; `stepSize` 0,00001; precisão de base e cotação: 8 casas. |
| MD-13 | Snapshot REST com 5000 níveis | ~320 KB bruto, ~54 KB com zstd; cobre cerca de 0,9% de preço de cada lado. |

### 2.2 Relógio

| ID | Medição | Resultado |
|---|---|---|
| MD-14 | Relógio do MacBook × NTP da Apple | **Atrasado cerca de 0,38 s.** |
| MD-15 | Relógio do servidor da Binance × MacBook (REST, compensando metade do tempo de ida e volta de ~0,59 s) | Servidor ~0,48 s à frente. |

**Consequência:** horários de recebimento gravados no MacBook, no estado atual, estão errados por centenas de milissegundos. A coleta oficial roda na AWS, com sincronização de tempo do provedor (a confirmar no doc 03). Dados coletados no MacBook (testes) recebem a marca `low_clock_confidence` na máscara de qualidade quando o offset passar do limite (§8). Recomenda-se ativar o ajuste automático de data e hora no macOS (Ajustes do Sistema → Geral → Data e Hora).

### 2.3 Volume por stream

Amostra de 100 s; o número de negócios foi escalado para a mediana diária dos últimos 30 dias (2,95 milhões; máximo de 4,88 milhões).

| Stream | Msgs/s | Bytes/msg | Bruto (MB/dia) | Razão zstd -3 | Comprimido (MB/dia) |
|---|---|---|---|---|---|
| `trade` | 30,4 | 134 | ~395 | 25× | ~16 |
| `kline_1m` | 0,4 | 327 | ~12 | 7× | ~2 |
| `depth20@100ms` | 8,7 | 1327 | ~1000 | 33× | ~30 |
| `depth@100ms` (diferencial) | 8,7 | 1043 | ~790 | 6,8× | ~116 |
| Snapshot REST 5000 níveis, a cada 15 min | — | 320 000 | ~31 | 5,9× | ~5 |
| **Total da camada bruta** | | | **~2 200** | | **~170** |
| *Não coletados (referência):* `aggTrade` | 8,1 | 168 | ~118 | 9,7× | ~12 |
| *Não coletados (referência):* `bookTicker` | 102 | 108 | ~950 | 14× | ~68 |

Streams cujo volume depende da atividade (`trade`, diferencial) podem crescer cerca de 1,7× em dias de pico. As razões de compressão vêm de arquivos pequenos e tendem a ser conservadoras.

### 2.4 Acesso a partir da AWS (us-east-2)

Medido no AWS CloudShell da conta do projeto, em 23/10/2026, depois de descobrir que a conta só aceita a us-east-2 (doc 03 §2.2).

| ID | Medição | Resultado |
|---|---|---|
| MD-16 | REST `api.binance.com/api/v3/time` | **HTTP 451** (bloqueio geográfico). |
| MD-17 | REST `data-api.binance.vision/api/v3/time` | HTTP 200. |
| MD-18 | REST `api.binance.com/api/v3/historicalTrades` | **HTTP 451**. |
| MD-19 | Arquivos `data.binance.vision` | HTTP 200. |
| MD-20 | WebSocket `wss://data-stream.binance.vision` | Conecta e entrega negócios. |
| MD-21 | WebSocket `wss://stream.binance.com:9443` | **HTTP 451** no handshake. |

**Consequência (DEC-26):** o host de produção usa só os endpoints `*.binance.vision`, que a própria Binance oferece para dados de mercado; a recuperação imediata de negócios pela REST não está disponível de lá, e as lacunas de negócios são preenchidas pelos arquivos diários.

Medido no host de produção (EC2 t4g.small, us-east-2a), em 23/10/2026:

| ID | Medição | Resultado |
|---|---|---|
| MD-22 | `trader smoke` por 10 min: REST `data-api.binance.vision`, 5 pedidos seguidos | Ida e volta mínima de 139 ms; offset de −1,3 ms (incerteza de ±70 ms). |
| MD-23 | `trader smoke`: WebSocket `data-stream.binance.vision` | Os 4 streams chegam (trade 18,8/s; kline 0,5/s; top 20 e diferencial 10/s); atraso do evento ao recebimento p50 de 69 ms. |
| MD-24 | Coletor, 1 pedido de horário a cada 5 min | Ida e volta de ~563 ms e offset de ~+210 ms: a conexão HTTP fecha após 5 s parada e cada pedido inclui a abertura da conexão TCP e TLS. Corrigido em §4.2. |

---

## 3. Fontes e integrações

### 3.1 Sistemas externos

| Sistema | Uso | Autenticação | Crítico para |
|---|---|---|---|
| Binance WebSocket (dados de mercado) | Coleta ao vivo. | Nenhuma | V0.1, V0.3 |
| Binance REST (dados de mercado) | Snapshots do livro, recuperação de lacunas, horário do servidor, metadados do ativo. | Nenhuma | V0.1 |
| data.binance.vision (arquivos públicos) | Backfill histórico de negócios, candles e negócios agregados. | Nenhuma | V0.1, V0.2 |
| AWS S3 | Armazenamento durável — **fonte da verdade** dos dados. | Credencial temporária da instância (RNF-014) | V0.1 |
| AWS CloudWatch | Métricas, logs e alarmes. | Idem | V0.1 |
| Serviço de tempo do host | Sincronização do relógio. | — | V0.1 |

### 3.2 Endpoints

| Uso | Principal | Alternativo | Observação |
|---|---|---|---|
| WebSocket | `wss://data-stream.binance.vision` | `wss://stream.binance.com:9443` (ou `:443`) — **não em produção**: responde 451 na us-east-2 (MD-21) | Exclusivo de dados de mercado (FV-03). Único endpoint do host de coleta (DEC-26). |
| REST — snapshot, klines, horário, `exchangeInfo` | `https://data-api.binance.vision` | `https://api.binance.com` — **não em produção**: responde 451 na us-east-2 (MD-16) | No Brasil o alternativo foi ~4× mais rápido (MD-01, MD-02); na AWS só o principal funciona. |
| REST — `historicalTrades` | `https://api.binance.com` | — | Indisponível no endpoint de dados de mercado (MD-06) e bloqueado na us-east-2 (MD-18): no host de produção, lacunas de negócios só pelos arquivos diários (§9). Não exige chave. |
| Arquivos | `https://data.binance.vision` | — | Checksum SHA-256 por arquivo. |

### 3.3 Streams coletados

Uma única conexão de streams combinados (`/stream?streams=...&timeUnit=MICROSECOND`), sem mensagens de inscrição — o limite de 5 mensagens enviadas por segundo (FV-02) não é atingido.

| Stream | Camada normalizada | Prioridade | Recuperável | Decisão |
|---|---|---|---|---|
| `btcusdt@trade` | `trades` | Must | Sim — REST (~2 anos) e arquivos diários | DEC-19 |
| `btcusdt@kline_1m` | `klines_1m` | Must | Sim — REST e arquivos | DEC-15 |
| `btcusdt@depth20@100ms` | `book_top20` | Must | **Não** | DEC-18 |
| `btcusdt@depth@100ms` | — (só bruta) | Must | **Não** | DEC-18 |

**Rotação da conexão:** a nova conexão é aberta com a mesma lista de streams; durante a sobreposição, as duas gravam na camada bruta (arquivos separados por sessão, §5.2) e a deduplicação acontece na normalização pelas chaves do §5.4.

### 3.4 Chamadas REST periódicas

| Chamada | Frequência | Peso | Finalidade |
|---|---|---|---|
| `depth?limit=5000` | No início de cada sessão; a cada 15 min [PROPOSTA]; após quebra de continuidade do diferencial | 250 | Ancorar o diferencial para reconstrução futura (§10). |
| `time` | A cada 5 min | 1 | Offset de relógio (RF-125). |
| `exchangeInfo?symbol=BTCUSDT` | Na inicialização e a cada 24 h | 20 [A VERIFICAR] | Precisão e filtros do ativo; detectar mudanças de regras. |
| `historicalTrades` / `klines` | Sob demanda | 25 / 2 | Recuperação de lacunas (§9). |

### 3.5 Orçamento de peso REST

- Limite da fonte: 6000 por minuto por IP (MD-08).
- **Teto próprio [PROPOSTA]: 1200 por minuto (20%)**, controlado por um limitador local; o restante fica como folga contra outros usos do mesmo IP.
- Consumo de rotina: ~1100 por hora (snapshots e horário).
- Recuperação: 1000 negócios custam 25 de peso. Uma lacuna de 1 hora em dia de pico (~200 mil negócios) custa ~5000 de peso; com o teto, é recuperada em cerca de 5 minutos.
- HTTP 429: aguardar o `Retry-After`. HTTP 418: parar toda chamada REST, alertar e só retomar depois do prazo (RF-145).

---

## 4. Modelo temporal

### 4.1 Representação

| Regra | Especificação |
|---|---|
| Unidade | **Inteiro de 64 bits em microssegundos desde a época Unix, UTC**, em todas as camadas a partir da normalizada. |
| Fontes ao vivo | WebSocket com `timeUnit=MICROSECOND`; REST com `X-MBX-TIME-UNIT: MICROSECOND` (MD-04, MD-05). |
| Arquivos públicos | µs a partir de 01/01/2025; ms antes disso (FV-04). A unidade é declarada por arquivo, conforme a data, e validada pela faixa de valores. |
| Faixas válidas | µs: 1,5 × 10¹⁵ a 4,1 × 10¹⁵. ms: 1,5 × 10¹² a 4,1 × 10¹². Valor fora da faixa declarada → registro rejeitado. |
| Nomes de colunas | Sufixo `_ts_us` (por exemplo, `trade_ts_us`, `recv_ts_us`). |

### 4.2 Relógios

- **Horário de recebimento (`recv_ts_us`):** lido do relógio de parede do host imediatamente após a leitura da mensagem do socket, antes do parsing (RF-121).
- **Intervalos e silêncio de stream:** medidos com relógio monotônico, imune a ajustes do relógio de parede.
- **Offset:** medido a cada 5 minutos via `/api/v3/time`, compensando metade do tempo de ida e volta; gravado em `clock_offsets` (RF-125). Cada medição é uma rajada de 5 pedidos seguidos na mesma conexão, e vale a amostra de menor ida e volta: o primeiro pedido costuma incluir a abertura da conexão, que infla a ida e volta e desloca o offset (MD-24). A incerteza do offset é metade da ida e volta da amostra escolhida. Todos os pedidos ficam na camada bruta.

### 4.3 Disponibilidade *point-in-time* (RN-006)

| Dado | Disponível para decisão a partir de |
|---|---|
| Negócio ao vivo | `recv_ts_us` |
| Candle ao vivo | `recv_ts_us` da mensagem com `x = true` |
| Snapshot top 20 | `recv_ts_us` |
| Mensagem do diferencial | `recv_ts_us` |
| Dado recuperado (REST ou arquivo) | **Não tem `recv_ts_us`** (nulo). O doc 04 define a latência assumida para uso em simulação. |

---

## 5. Armazenamento (DEC-20)

### 5.1 Camadas

| Camada | Conteúdo | Formato | Mutabilidade |
|---|---|---|---|
| **Bruta** | Mensagens exatamente como recebidas, de WebSocket e REST. | JSONL; comprimido com zstd ao fechar | Imutável (RN-007) |
| **Normalizada** | Registros com esquema canônico, deduplicados e validados. | Parquet com compressão zstd | Regenerável a partir da bruta (RF-133) |
| **Derivada** | Máscara de qualidade, relatórios, agregações (F-11). | Parquet | Regenerável |
| **Quarentena** | Mensagens que falharam no parsing ou na validação; uma linha por registro, com canal e motivo. Caminho: `quarantine/binance-spot/<ativo>/date=…/hour=…/normalize.jsonl.zst`. | JSONL zstd | Regenerada junto com a hora |

A camada normalizada é sempre produzida **a partir dos arquivos brutos fechados**, por um processamento horário que usa o mesmo código de normalização do caminho ao vivo. Isso garante o determinismo (RF-128) e que o ao vivo e o histórico passem pelas mesmas regras (PP-05).

### 5.2 Camada bruta

**Envelope por linha:** o payload recebido é inserido sem alteração nem escape:

```text
{"r":<recv_ts_us>,"s":"<id da sessão>","q":<sequencial na sessão>,"c":"<canal>","m":<payload exatamente como recebido>}
```

- `c` (canal): nome do stream WebSocket (por exemplo, `btcusdt@trade`) ou da chamada REST (por exemplo, `rest:depth`).
- O id da sessão é ordenável no tempo (por exemplo, ULID ou UUIDv7).
- Como o payload é JSON válido, a linha inteira também é; os bytes do payload ficam preservados.

**Caminho:**

```text
raw/binance-spot/btcusdt/<canal>/date=AAAA-MM-DD/hour=HH/<id da sessão>.jsonl[.zst]
```

**Ciclo do arquivo:**

1. O arquivo da hora corrente é escrito sem compressão, com *flush* e *fsync* a cada 1 s [PROPOSTA] — atende RNF-004 (perda ≤ 5 s).
2. Na virada da hora UTC ou no fim da sessão, o arquivo é fechado e comprimido com zstd [PROPOSTA: nível 9].
3. A compressão é verificada (descomprimir e comparar o hash) antes de apagar o original.
4. Só arquivos fechados e verificados são replicados (§6).

### 5.3 Camada normalizada

**Caminho:**

```text
normalized/<dataset>/symbol=BTCUSDT/date=AAAA-MM-DD/<hora ou lote>.parquet
```

**Tipos numéricos (RN-011):**

| Grandeza | Tipo |
|---|---|
| Preço, quantidade | `DECIMAL(18,8)` — comporta preço até 10¹⁰ e as 8 casas da fonte (MD-12). |
| Valores em moeda de cotação e volumes agregados | `DECIMAL(38,8)` |
| Ids da exchange | `INT64` |
| Marcas de tempo | `INT64` (µs, UTC) |

A conversão para ponto flutuante só é permitida a partir das features (V0.2), nunca na camada normalizada.

**Colunas comuns a todos os datasets normalizados:**

| Coluna | Tipo | Descrição |
|---|---|---|
| `symbol` | string | Ativo (`BTCUSDT`). |
| `origin` | string | `live`, `rest` ou `archive`. |
| `provenance` | string | Id da sessão ou do lote de recuperação. |
| `schema_version` | int16 | Versão do esquema do dataset. |

### 5.4 Esquemas

#### `trades` — chave (`symbol`, `trade_id`)

| Coluna | Tipo | WebSocket | REST / arquivo |
|---|---|---|---|
| `trade_id` | INT64 | `t` | `id` / col. 1 |
| `price` | DECIMAL(18,8) | `p` | `price` / col. 2 |
| `qty` | DECIMAL(18,8) | `q` | `qty` / col. 3 |
| `quote_qty` | DECIMAL(38,8) | calculado `price × qty` | `quoteQty` / col. 4 |
| `trade_ts_us` | INT64 | `T` | `time` / col. 5 |
| `event_ts_us` | INT64, nulo | `E` | nulo |
| `recv_ts_us` | INT64, nulo | envelope `r` | nulo |
| `is_buyer_maker` | BOOL | `m` | `isBuyerMaker` / col. 6 |
| `aggressor` | `B` ou `S` | derivado: maker comprador ⇒ `S`; caso contrário `B` (RF-104) | idem |

#### `klines_1m` — chave (`symbol`, `open_ts_us`)

Somente candles fechados (RF-102).

| Coluna | Tipo | WebSocket (`k.*`) | REST / arquivo (posição) |
|---|---|---|---|
| `open_ts_us` | INT64 | `t` | 0 |
| `close_ts_us` | INT64 | `T` | 6 |
| `open`, `high`, `low`, `close` | DECIMAL(18,8) | `o`, `h`, `l`, `c` | 1, 2, 3, 4 |
| `volume` | DECIMAL(38,8) | `v` | 5 |
| `quote_volume` | DECIMAL(38,8) | `q` | 7 |
| `trade_count` | INT64 | `n` | 8 |
| `taker_buy_volume` | DECIMAL(38,8) | `V` | 9 |
| `taker_buy_quote_volume` | DECIMAL(38,8) | `Q` | 10 |
| `first_trade_id`, `last_trade_id` | INT64, nulo | `f`, `L` | nulo |
| `recv_ts_us` | INT64, nulo | envelope `r` da mensagem com `x = true` | nulo |

#### `book_top20` — chave (`symbol`, `last_update_id`)

| Coluna | Tipo | Origem |
|---|---|---|
| `last_update_id` | INT64 | `lastUpdateId` |
| `recv_ts_us` | INT64 | envelope `r` (única referência temporal, FV-07) |
| `bid_px`, `bid_qty` | LIST<DECIMAL(18,8)> (20 itens) | `bids`, do melhor para o pior |
| `ask_px`, `ask_qty` | LIST<DECIMAL(18,8)> (20 itens) | `asks`, do melhor para o pior |

Repetição do mesmo `last_update_id` mantém a primeira ocorrência (SRS §3). As listas podem ser expandidas em colunas (`bid_px_1` … `bid_px_20`) por uma visão de leitura, sem duplicar dados. A coluna `origin` é sempre `live`.

**Partição e ordem.** Negócios ficam na hora do `trade_ts_us`; candles, na hora de `open_ts_us`; snapshots do livro, na hora do primeiro recebimento. As linhas ficam em ordem de recebimento; quem lê ordena pela chave quando precisar. A máscara de qualidade fica em `derived/quality_mask/symbol=…/date=…/HH.parquet` (colunas: `symbol`, `channel`, `minute_ts_us`, `status`, `details`, `schema_version`).

#### Datasets operacionais

| Dataset | Chave | Colunas principais |
|---|---|---|
| `sessions` | `session_id` | endpoint, canais, `opened_ts_us`, `closed_ts_us`, motivo do encerramento, mensagens por canal, versão do código |
| `gaps` | (`channel`, `start_ts_us`) | `end_ts_us`, causa, `detected_ts_us`, recuperável, status, origem da recuperação; para negócios, `first_missing_id` e `last_missing_id` |
| `clock_offsets` | `local_ts_us` | `server_ts_us`, `rtt_us`, `offset_us` |
| `quality_mask` | (`channel`, `minute_ts_us`) | `status` (§8.2), detalhes |
| `hour_stats` | (`symbol`, `hour`, `metric`, `key`) | `value`: linhas por dataset (`rows`), repetições descartadas e registros em quarentena por canal (`duplicates`, `quarantined`) em cada hora normalizada; refeito quando a hora é normalizada de novo |
| `backfill_files` | `file_name` | dataset, período, status, SHA-256, linhas, quarentena, repetições, ids ausentes, minutos sem candle, saídas (§7) |

### 5.5 Versionamento de esquema

- Cada dataset tem `schema_version` próprio, começando em 1.
- Mudança de esquema: nova versão, registro no histórico deste documento e reprocessamento a partir da bruta (RF-129). Versões antigas não são misturadas no mesmo arquivo.

---

## 6. Ciclo de vida, retenção e custo

### 6.1 Fluxo

```text
recepção ─► arquivo bruto aberto (disco do host)
              │ virada da hora / fim da sessão
              ▼
          compressão + verificação ─► réplica no S3 ─► normalização da hora ─► Parquet ─► réplica no S3
                                                                   │
                                                                   └─► máscara de qualidade
```

### 6.2 Retenção [PROPOSTA]

| Local | Bruta | Normalizada / derivada |
|---|---|---|
| Disco do host | 7 dias após replicada; nunca apagar o que não foi replicado (RF-138). | 7 dias |
| S3 | **Sem exclusão durante o MVP** — é a fonte da verdade e o livro é insubstituível. | Dados da coleta ao vivo: últimos 6 meses; o restante pode ser apagado e regenerado a partir da bruta. Dados de backfill (`origin = archive`): sem exclusão, porque regenerá-los exige novo download (os arquivos originais não são guardados; nome e checksum ficam em `provenance`). |
| MacBook | Cache de períodos de pesquisa; pode ser apagado a qualquer momento. | Idem |

A transição para classes de armazenamento mais baratas no S3 (por idade) será avaliada no doc 03.

### 6.3 Estimativa de volume

| Item | Por dia | Por mês | Ao fim de 12 meses (acumulado no S3) |
|---|---|---|---|
| Bruta comprimida | ~170 MB (pico ~260 MB) | ~5,2 GB | ~62 GB |
| Normalizada + derivada [ESTIMATIVA, medir na V0.1] | ~80 MB | ~2,4 GB | ~14 GB (6 meses retidos) |
| Backfill (§7), carga única | — | — | 11,9 GB de negócios (MD-27) e ~0,1 GB de candles |
| **Total** | **~250 MB** | **~7,6 GB** | **~85 GB** |

Custo de armazenamento S3 Standard ao fim de 12 meses na us-east-2 (US$ 0,023/GB-mês, DEC-26): cerca de US$ 1,96 por mês (doc 03 §10). Requisições de gravação (dezenas de arquivos por hora) custam centavos por mês. A transferência para o MacBook entra na franquia mensal de saída para a internet [A VERIFICAR].

---

## 7. Backfill

| Dataset | Fonte | Período (aprovado em 23/10/2026) | Observações |
|---|---|---|---|
| `klines_1m` | Arquivos mensais (meses completos) e diários (mês incompleto) | 01/01/2023 a 22/10/2026 | ~3 anos para pesquisa de candles (H-01, H-02). |
| `trades` | Arquivos diários | 23/10/2025 a 22/10/2026 | ~3,8 milhões de linhas por dia em média; 11,9 GB em Parquet (MD-27). |
| `agg_trades` | Arquivos | Sob demanda | Could (DEC-19); não implementado. |

Os períodos terminam na véspera do início da coleta contínua (23/10/2026, 22:44 UTC). A REST não é usada no backfill: o dia corrente fica com a coleta ao vivo.

**MD-25 — arquivo diário de negócios de 22/10/2026:** 3,46 milhões de linhas; ZIP de 24 MB com um CSV de 263 MB, sem cabeçalho; marcas de tempo em µs. Normalizado pelo código da camada normalizada: ~93 mil linhas/s em um núcleo do MacBook M2 (~37 s por dia) e ~7,1 MB de Parquet por milhão de linhas.

**MD-26 — candles de 01/01/2023 a 22/10/2026, importados no host de produção:** 67 arquivos (45 mensais, 22 diários), 2.002.960 linhas em 1.391 dias, 81 s de CPU com `CPUQuota` de 25% (~25 mil linhas/s por núcleo, ~3,7× mais lento que o M2) e saldo de créditos de CPU subindo durante a carga. Faltam 80 minutos, todos no arquivo de março de 2023 [HIPÓTESE: a parada de negociação de spot da Binance em 24/03/2023].

**Carga de negócios 2025–2026:** rodada no MacBook, com envio direto ao S3 (decisão de 23/10/2026). No host, pela taxa de MD-26 e com o limite de 25% de CPU, levaria ~2 dias. No MacBook, ~1 min por dia de arquivo, contando download e envio. O `boto3` precisa de `botocore[crt]` para usar as credenciais de `aws login`; é dependência só de desenvolvimento e não vai para o host.

**MD-27 — negócios de 23/10/2025 a 22/10/2026, importados no MacBook:** 365 arquivos diários, 1.404.534.054 linhas (~3,8 milhões por dia, com pico de 15,4 milhões em 05/02/2026), sem quarentena, repetições ou ids ausentes dentro dos arquivos; 11,93 GB de Parquet no S3; 5h54 de execução, entre 25 e 204 s por dia. O relatório do MacBook está em `derived/ops/backfill_files-macbook.parquet`. A continuidade dos ids entre um dia e o seguinte não foi verificada.

Regras: checksum verificado antes da importação (RF-143); unidade de tempo conforme a data do arquivo (§4.1); importação idempotente (RF-144); `origin = archive`; `recv_ts_us` nulo.

**Funcionamento (`trader backfill`):**

1. **Unidade de trabalho:** um arquivo de `data.binance.vision` com o seu `.CHECKSUM`. Os downloads são feitos um de cada vez; falha de rede, HTTP 5xx ou 429 geram nova tentativa com espera (no 429, a de `Retry-After`). HTTP 404 significa arquivo ainda não publicado: entra no relatório como ausente.
2. **Checksum:** o SHA-256 é calculado durante o download. Se divergir do publicado, o arquivo é rejeitado, nada é gravado e a próxima execução tenta de novo (RF-143).
3. **Normalização:** o CSV é lido em fluxo pelo mesmo código de normalização e validação dos dados ao vivo (PP-05).
4. **Saída:** `normalized/<dataset>/symbol=BTCUSDT/date=AAAA-MM-DD/archive.parquet`, um arquivo por dia, na ordem do arquivo de origem. O arquivo mensal de candles é dividido por dia. `provenance` = `<nome do ZIP>@<SHA-256>`.
5. **Quarentena:** registros recusados vão para `quarantine/binance-spot/btcusdt/date=…/archive-<dataset>.jsonl.zst`, com o motivo. Também vai para lá a linha cuja data não pertence ao período do arquivo, para não sobrescrever outro dia. Negócio repetido com conteúdo idêntico é descartado; com conteúdo diferente, ou fora da ordem dos ids, fica o primeiro e o outro vai para a quarentena.
6. **Relatório:** por arquivo, linhas gravadas, quarentena, repetições, ids de negócio ausentes e minutos sem candle, na tabela `backfill_files` do estado, exportada como dataset operacional. Ids ausentes dentro de um arquivo não têm outra fonte no host de produção: ficam registrados, sem preenchimento.
7. **Retomada (RF-144):** um arquivo só é marcado como importado depois que todas as saídas foram gravadas e replicadas; os já importados são pulados. Um arquivo interrompido é processado de novo desde o início e regrava as mesmas saídas, sem duplicar. Duas execuções simultâneas são impedidas por um bloqueio.
8. **Réplica:** cada saída vai para o armazenamento durável com o SHA-256 assim que o arquivo de origem termina. Havendo réplica, a cópia local é apagada em seguida, porque o disco do host é pequeno; no S3, os dados de backfill nunca são apagados (§6.2).
9. **Limite com a coleta ao vivo:** o backfill só cobre dias anteriores ao início da coleta contínua, para não repetir chaves da normalização horária. Do dia em que a coleta começou em diante, os arquivos diários servem à recuperação de lacunas (§9, F-06), que junta os dados recuperados na normalização da hora.
10. **Máscara de qualidade:** não é gerada para os dias importados nesta versão [PENDÊNCIA]; a cobertura sai do relatório (item 6) e dos próprios dados.

---

## 8. Validação e inconsistências

### 8.1 Regras

| Verificação | Aplica-se a | Ação |
|---|---|---|
| Falha de parsing ou campo obrigatório ausente | Todos | Quarentena; contador; alerta acima de 0,1% das mensagens por hora [PROPOSTA] (RF-119). |
| Marca de tempo fora da faixa da unidade declarada | Todos | Rejeitar para quarentena. |
| Preço ou quantidade ≤ 0 | Negócios, livro | Rejeitar para quarentena. |
| Id de negócio repetido com conteúdo idêntico | Negócios | Descartar a repetição (sobreposição na rotação ou recuperação). |
| Chave repetida com conteúdo diferente | Negócios, candles, top 20 | Fica a primeira cópia recebida; a divergente vai para a quarentena e o minuto é marcado `invalid` (a pesquisa exclui o minuto inteiro). |
| Salto na sequência de ids | Negócios | Registrar lacuna e recuperar (§9). |
| `recv_ts_us − trade_ts_us − offset` acima de 5 s [PROPOSTA] | Negócios ao vivo | Aceitar e marcar `late` na máscara. |
| OHLC inconsistente ou volume negativo | Candles | Rejeitar e alertar. |
| Minuto sem candle fechado | Candles | Registrar lacuna e recuperar via REST. |
| Candle × candle reconstruído dos negócios | Candles | Registrar divergência no relatório (RF-156). |
| Melhor compra ≥ melhor venda | Top 20 | Aceitar e marcar `invalid`. |
| Níveis fora de ordem | Top 20 | Rejeitar para quarentena. |
| Regressão de `last_update_id` | Top 20 | Aceitar e marcar `invalid`. |
| Intervalo entre snapshots > 2 s | Top 20 | Registrar lacuna (irrecuperável). |
| `U ≠ u anterior + 1` | Diferencial | Registrar lacuna do diferencial e solicitar novo snapshot REST imediato. |
| Offset de relógio > 250 ms | Sessão | Alerta e marca `low_clock_confidence` nos minutos afetados. |

### 8.2 Máscara de qualidade

Um registro por canal e por minuto UTC. Estados, do pior para o melhor:

| Estado | Significado |
|---|---|
| `missing` | Sem dados no minuto (lacuna aberta ou irrecuperável). |
| `invalid` | Dados presentes, mas reprovados em alguma verificação. |
| `low_clock_confidence` | Dados válidos, com horário de recebimento pouco confiável. |
| `late` | Dados válidos que chegaram com atraso acima do limite. |
| `recovered` | Dados recuperados de REST ou arquivo (sem `recv_ts_us`). |
| `ok` | Dados ao vivo, completos e válidos. |

Se mais de um estado se aplica ao minuto, prevalece o pior. A pesquisa (V0.2) e o Risk Manager (V0.3) consultam a máscara para excluir janelas contaminadas (RN-005).

### 8.3 Relatório de qualidade (RF-158, CA-05)

`trader quality --start AAAA-MM-DD --end AAAA-MM-DD [--json <arquivo>]` cobre os dias UTC completos do período e lê o diretório de dados local.

| Seção | Conteúdo | Fonte |
|---|---|---|
| Completude | Por canal: minutos em cada estado da máscara e a fração sem `missing`; horas sem máscara | `derived/quality_mask` |
| Lacunas | Por canal e status: quantidade e duração dentro do período | `gaps` |
| Repetições e quarentena | Por canal; linhas normalizadas por dataset | `hour_stats` |
| Anomalias | Minutos `invalid`, `late` e `low_clock_confidence` | `derived/quality_mask` |
| Atraso do evento ao recebimento | p50, p90, p99, p99,9 e máximo, em ms, de negócios e candles ao vivo, sem correção de offset; quantidade com atraso negativo | `normalized/trades` e `normalized/klines_1m` |
| Relógio | Medições, offset mediano e maior offset em módulo, ida e volta mediana | `clock_offsets` |
| Sessões | Sessões abertas no período, por motivo de encerramento | `sessions` |
| Armazenamento | Bruto comprimido e original por dia (CA-06) | `raw_files` |

No host, máscara e camada normalizada ficam só 7 dias no disco (§6.2). Para períodos mais antigos, o relatório roda sobre uma cópia: `derived/` e `normalized/` baixados do S3 e o estado de `ops/state-backup.db`. Dias importados pelo backfill não têm máscara nem horário de recebimento e ficam fora do relatório (§7, item 10).

### 8.4 Dados do painel da coleta

`trader dashboard-data --out <diretório> [--config <toml>] [--data-dir <cache>]` gera as tabelas do painel visual da coleta ao vivo. Com `replica_s3_bucket` na configuração, primeiro baixa do S3 para o cache local só o que mudou (comparando o ETag de cada objeto): os arquivos horários de `normalized/trades`, `normalized/klines_1m`, `normalized/book_top20` e `derived/quality_mask` desde o início da coleta, e a cópia do estado (`ops/state-backup.db`). Sem réplica configurada, lê o diretório de dados como está.

| Arquivo | Conteúdo |
|---|---|
| `minutes.csv` | Por minuto desde o primeiro minuto completo da coleta: fechamento, volume, volume por lado do agressor, negócios, fotos do livro, spread em pontos-base e desequilíbrio do livro (top 20 e 1º nível), em média no minuto |
| `book_snapshot.csv` | Os 20 níveis de cada lado da última foto do livro |
| `quality_hourly.csv` | Minutos `ok`, `recovered`, `missing` e outros, por hora e canal, segundo a máscara |
| `delays_hourly.csv` | Atraso do negócio ao recebimento por hora: quantidade, p50 e p99, em ms |
| `gaps.csv` | Lacunas registradas, com causa, status, início, duração e origem da recuperação |
| `highlights.csv` | Destaques em linguagem leiga (`metric`, `value`, `detail`): mensagens recebidas por canal e por segundo; dinheiro negociado na coleta; maior negócio; segundo com mais negócios; atraso mediano e p99; bytes recebidos e guardados; contagens do histórico importado (negócios, candles, dias); tamanho e número de objetos do bucket |

As mensagens recebidas são contadas linha a linha nos arquivos brutos do S3; a contagem de cada arquivo fica no cache junto com o ETag, então uma atualização só lê os arquivos novos. Sem réplica, conta os brutos que estão no disco local. O histórico importado entra só como contagem de linhas e de arquivos (`backfill_files`), nunca como preço ou volume.

Só entram dados da coleta ao vivo: os arquivos de backfill (`archive.parquet`) e qualquer período anterior ao início da coleta ficam de fora, o que mantém o holdout da trilha A do doc 04 sem consulta. O comando não publica nada; o painel é atualizado a partir dos arquivos gerados.

---

## 9. Recuperação de falhas por canal

| Canal | Como a lacuna é detectada | Recuperável | Fonte e momento |
|---|---|---|---|
| `trade` | Salto de id; reconexão | Sim | Onde `api.binance.com` responde: REST `historicalTrades` a partir do primeiro id ausente, imediatamente (alcance de ~2 anos, MD-07). **No host de produção (us-east-2), só pelo arquivo diário, no dia seguinte (MD-18).** |
| `kline_1m` | Minuto ausente; reconexão | Sim | REST `klines`, imediatamente. |
| `depth20@100ms` | Silêncio > 2 s; reconexão | **Não** | Lacuna marcada como irrecuperável. |
| `depth@100ms` | Quebra de `U`/`u`; reconexão | **Não** | Lacuna marcada; novo snapshot REST para reancorar a reconstrução futura. |
| `rest:depth` | Falha da chamada | Repetir | Nova tentativa com espera exponencial, respeitando o orçamento de peso. |

Dados recuperados: `origin = rest` ou `archive`, `recv_ts_us` nulo, mesma validação dos dados ao vivo (RF-149).

### 9.1 Como a recuperação funciona

É a primeira etapa do pipeline horário (doc 03 §6.4), antes da compressão:

1. **Candles** (lacuna `kline_missing` aberta, já encerrada): pede `/api/v3/klines` (peso 2) ao `rest_base_url` só para os minutos ausentes, com horário em µs. A resposta vai para a camada bruta no canal `rest:klines`, dividida pela hora de cada candle. Minuto que a REST também não traz: a lacuna fica `irrecoverable`.
2. **Negócios** (lacuna `trade_id_jump` aberta): quando o arquivo diário do dia da lacuna sai, ele é baixado com checksum verificado (§7), e as linhas dos ids ausentes vão para a camada bruta no canal `archive:trades`, na hora de cada negócio. O payload traz o nome do arquivo, o SHA-256, a unidade de tempo e os campos da linha. Id que o arquivo oficial também não tem: `irrecoverable`. Arquivo ainda não publicado (HTTP 404): nova tentativa na próxima rodada. `historicalTrades` não é usado: no host de produção ele responde 451 (MD-18).
3. **Livro:** nada a fazer; as lacunas já nascem irrecuperáveis (RN-009).
4. **Normalização:** os canais de recuperação passam pela mesma deduplicação do stream correspondente (a cópia ao vivo, recebida antes, prevalece), pela mesma validação e entram com `origin = rest` ou `archive`, `recv_ts_us` nulo e o minuto marcado `recovered` na máscara. O arquivo bruto novo muda o resumo das entradas da hora e dispara a renormalização (doc 03 §6.3).
5. **Status:** a lacuna passa a `recovered`, com `recovery_origin` = `rest:klines` ou `archive:<arquivo>@<SHA-256>`, ou a `irrecoverable`.
6. **Falhas** de rede, HTTP 5xx ou 429 não interrompem o pipeline: a lacuna continua aberta e volta na próxima rodada. Depois de um 429, nenhuma outra chamada REST na mesma rodada (RF-145).
7. **Limite:** lacunas cuja hora já saiu do disco local (mais de 7 dias, §6.2) não são recuperadas automaticamente.

---

## 10. Livro de ofertas: reconstrução futura (F-04)

O que a coleta garante desde a V0.1:

1. diferencial completo, com `E`, `U` e `u`, na camada bruta;
2. snapshot REST de 5000 níveis por lado no início de cada sessão, a cada 15 minutos e após cada quebra de continuidade;
3. registro das lacunas do diferencial.

Procedimento de reconstrução (quando F-04 for implementada), conforme a documentação oficial (FV-08):

1. Para cada sessão, usar o primeiro snapshot cujo `lastUpdateId` seja ≥ `U` do primeiro diferencial bufferizado.
2. Descartar diferenciais com `u` ≤ `lastUpdateId` do snapshot.
3. Aplicar os diferenciais em ordem, verificando a continuidade.
4. Em cada quebra, recomeçar a partir do snapshot seguinte.

**Validação cruzada:** os 20 melhores níveis do livro reconstruído devem coincidir com os snapshots `book_top20` de mesmo `lastUpdateId`. Esse teste permite verificar a reconstrução sem dados externos.

**Limitações:** o snapshot cobre só cerca de 0,9% de preço de cada lado (MD-13); o diferencial agrega mudanças a cada 100 ms; não há dados por ordem individual (nível 3).

---

## 11. Fontes futuras (V0.4)

GDELT, Trading Economics, FRED e outras (contexto inicial, §5.5 a §5.7). Requisito já fixado: cada item precisa preservar o **horário de publicação** e o **horário em que ficou disponível para o sistema**, para respeitar o point-in-time. Detalhamento quando a V0.4 for planejada.

---

## 12. Termos de uso e licenciamento

| Item | Situação |
|---|---|
| Termos de uso da API da Binance | [A VERIFICAR] antes de iniciar a coleta contínua — **mais importante agora**: a coleta roda nos EUA, por endpoints de dados de mercado que a Binance mantém acessíveis de lá (MD-17, MD-20), sem proxy nem VPN (RN-015). Premissa: uso pessoal, sem redistribuição dos dados brutos. |
| Termos dos arquivos públicos (data.binance.vision) | [A VERIFICAR]. |
| Bucket S3 | Privado, criptografado, sem acesso público (RNF-017). Dados brutos não são publicados nem compartilhados. |
| Restrição geográfica | Testar conectividade a partir da região AWS antes de provisionar (PR-12); nunca contornar com proxy ou VPN (RN-015). |

---

## 13. Pendências deste documento

| Item | Onde se resolve |
|---|---|
| Medir volume e compressão a partir da região AWS (latência: MD-22, MD-23) | Doc 03 / primeira semana da V0.1 |
| Escolher o endpoint REST principal pela latência medida na região | Primeira semana da V0.1 |
| Investigar a frequência do parcial top 20 (8,7/s × 10/s nominais; ~10/s na coleta local de 23/10/2026) | V0.1 |
| Tamanho real da camada normalizada | V0.1 |
| Máscara de qualidade para os dias importados pelo backfill (§7) | V0.2, se a pesquisa precisar |
| Peso exato de `exchangeInfo` | Implementação |
| Termos de uso (§12) | Antes da coleta contínua |
| Classes de armazenamento mais baratas no S3 | CP-3 (doc 03 §10.4) |

---

## 14. Rastreabilidade

| Seção | Requisitos do SRS |
|---|---|
| §3.2, §3.3 | RF-101–RF-105, RF-109, RF-192 |
| §3.4, §3.5 | RF-113, RF-125, RF-145, RF-193 |
| §4 | RF-121–RF-125, RNF-012, RN-006, RN-010 |
| §5 | RF-126–RF-129, RF-131–RF-139, RN-007, RN-011 |
| §6 | RF-136–RF-138, RNF-010, RNF-011 |
| §7 | RF-141–RF-145 |
| §8 | RF-151–RF-159, RF-194 |
| §9 | RF-146–RF-149 |
| §10 | RF-191–RF-194 |
| §12 | RNF-017, RNF-026, RN-015 |

---

## 15. Histórico de revisões

| Versão | Data | Descrição |
|---|---|---|
| 0.1 | 23/10/2026 | Rascunho inicial com DEC-18, DEC-19 e DEC-20; medições de referência a partir do MacBook. |
| 0.2 | 23/10/2026 | Custo de S3 com o preço de Tóquio (DEC-21); pendência de preços movida para o CP-3. |
| 0.3 | 23/10/2026 | Pendências atualizadas com a primeira coleta local (frequência do top 20; precisão da sonda de relógio). |
| 0.4 | 23/10/2026 | Regras do pipeline implementado: partição e ordem dos datasets, quarentena por hora, cópia divergente (fica a primeira; minuto `invalid`). |
| 0.5 | 23/10/2026 | §2.4 com o acesso medido a partir da us-east-2 (MD-16 a MD-21); endpoints e recuperação de negócios ajustados para produção nos EUA (DEC-26); custo de S3 com preço da us-east-2. |
| 0.6 | 23/10/2026 | Medições no host de produção (MD-22 a MD-24); sonda de relógio em rajada de 5 pedidos, com a amostra de menor ida e volta (§4.2), resolvendo a pendência de precisão. |
| 0.7 | 23/10/2026 | §7: períodos aprovados e funcionamento do backfill (saída por dia, quarentena, relatório, retomada, réplica, limite com a coleta ao vivo); MD-25. |
| 0.8 | 24/10/2026 | §7: MD-26 (candles importados no host) e carga de negócios no MacBook; pendência de onde rodar resolvida; MD-27 com o resultado da carga de negócios. |
| 0.9 | 24/10/2026 | §8.3: relatório de qualidade (CA-05); datasets operacionais `hour_stats` e `backfill_files` em §5.4. |
| 0.10 | 24/10/2026 | §9.1: funcionamento da recuperação de lacunas (canais brutos `rest:klines` e `archive:trades`, status, falhas). |
| 0.11 | 25/10/2026 | §8.4: dados do painel da coleta (`trader dashboard-data`). |
| 0.12 | 25/10/2026 | §8.4: tabela de destaques do painel (`highlights.csv`). |
