# Guia de ML para o MERIS

> **Não normativo.** Este guia é material de apoio para entender a V0.2. Quem manda são o PRD (doc 01) e, quando existir, o doc 04. Escrito em 23/10/2026.

Objetivo: dar o mínimo de Machine Learning para você **julgar** o que o projeto produz, sem precisar ser especialista. Cada seção aponta onde o assunto aparece no PRD.

---

## 1. O que o ML faz aqui, em uma frase

Dado o que aconteceu no mercado até agora (preço, volume, negócios, livro de ofertas), o modelo estima **se vale a pena comprar agora** — e o projeto mede se essa estimativa dá lucro **depois dos custos**, em dados que o modelo nunca viu.

O modelo não opera. Ele só gera um número. Quem decide é o Signal Engine, e quem veta é o Risk Manager (PP-02).

## 2. Vocabulário mínimo

| Termo | Significado | Exemplo com BTC |
|---|---|---|
| **Feature** | Uma informação de entrada, calculada só com o passado | Retorno dos últimos 5 minutos; volume do último minuto; desequilíbrio entre compras e vendas no livro |
| **Alvo (label)** | O que queremos prever | "Nos próximos 15 minutos, o preço sobe mais que o custo de operar?" (sim/não) |
| **Modelo** | Função que aprende a relação feature → alvo | XGBoost |
| **Treino** | Fase em que o modelo aprende, olhando exemplos passados com a resposta conhecida | Dados de janeiro a junho |
| **Teste** | Fase em que se mede o modelo em dados que ele **não** viu | Dados de julho |
| **Baseline** | Estratégia simples usada como régua de comparação | "Não operar", "comprar e segurar", "entrar aleatoriamente" |
| **Backtest** | Simular a estratégia no passado, com custos | Rodar o modelo sobre julho e somar ganhos e perdas |
| **Overfitting** | O modelo decorou o passado e não generaliza | Lucro perfeito no treino, prejuízo no teste |

## 3. XGBoost, sem matemática

Um conjunto de centenas de "árvores de decisão" pequenas (perguntas do tipo *"o volume está acima de X? então olhe o retorno; se for menor que Y…"*). Cada árvore corrige os erros das anteriores. É o modelo-padrão para dados em tabela: rápido, funciona bem com poucos dados, roda no MacBook sem GPU e dá para inspecionar quais features pesaram (DEC-09, F-15).

Redes neurais (PyTorch) só entram **depois**, como opcional (F-21).

## 4. Como o projeto organiza o experimento

Os baselines (PRD, Apêndice B) formam uma escada. Cada degrau acrescenta **uma** fonte de informação, mantendo todo o resto igual:

| ID | O que é | Pergunta que responde |
|---|---|---|
| B0 | Não operar | Operar é melhor que ficar parado? |
| B1 | Comprar e segurar | Melhor que o mercado puro? |
| B2 | Entradas aleatórias | Melhor que sorte? |
| B3 | Regra simples de candles | Melhor que análise técnica sem ML? |
| B4 | Modelo com candles e indicadores | H-01 |
| B5 | B4 + volume | H-02 |
| B6 | B5 + fluxo de negócios | H-03 (parte 1) |
| B7 | B6 + livro de ofertas | H-03 (parte 2) e **H-04, a hipótese central** |

Se B7 não supera os degraus de baixo depois de custos, a conclusão é **"não há evidência de vantagem"** — e isso é um resultado válido (PP-08).

## 5. Os erros que invalidam tudo

Esta é a parte mais importante. Cada item vem com a defesa que o projeto já prevê.

| Erro | O que acontece | Defesa no projeto |
|---|---|---|
| **Vazamento (leakage)** | O modelo "vê o futuro" sem querer, por exemplo usando o preço de fechamento de um candle que ainda não fechou. O resultado é lindo e falso | Features **point-in-time**: só informação disponível no instante da decisão, com o mesmo código em pesquisa e operação |
| **Validação embaralhada** | Separar treino e teste sorteando linhas ao acaso mistura passado e futuro, porque minutos vizinhos são parecidos | Validação **walk-forward**: treina no passado, testa no que vem depois, sempre em ordem temporal (F-16) |
| **Contaminação entre treino e teste** | Exemplos perto da fronteira "vazam" informação de um lado para o outro | **Purga e embargo**: descarta uma faixa de segurança entre treino e teste (F-16) |
| **Olhar o teste muitas vezes** | Quanto mais você ajusta olhando o resultado, mais "decora" o teste. Com bastante tentativa, algo sempre parece funcionar por pura sorte | **Holdout final avaliado uma única vez**, com critérios **pré-registrados** antes (IS-12, §8.4) |
| **Custos subestimados** | Taxas, spread e deslizamento (slippage) comem um lucro que parecia pequeno | Modelo de custos **conservador** e igual para todas as estratégias; análise de sensibilidade (R-04) |
| **Mudança de regime** | O mercado muda e o que funcionava deixa de funcionar | Avaliação por subperíodo e monitoramento de deriva (R-06) |
| **Comparação injusta** | O modelo novo parece melhor só porque teve mais ajuste ou outra janela | Mesma janela, mesmo backtester, mesmo tipo de modelo e mesmos hiperparâmetros em B4 a B7 |

## 6. Como ler um resultado

Três números bastam no começo:

- **Retorno líquido**: quanto sobrou depois de todos os custos.
- **Drawdown máximo**: a maior queda do pico ao vale. Mostra quanto dói o caminho.
- **Número de operações**: poucas operações significam pouca evidência. Um lucro em 20 operações pouco diz.

Compare sempre com B0, B1 e B2. Um modelo que ganha 5% quando entradas aleatórias ganham 4% não provou nada. Os critérios exatos de G2 serão definidos no doc 04 e no doc 07 (PD-07).

## 7. Regra de ouro: resultado bom demais é suspeito

Se aparecer um backtest muito bom, a primeira reação não é comemorar. É procurar o erro:

1. Há vazamento de informação do futuro?
2. O teste foi olhado muitas vezes?
3. Os custos são realistas?
4. O número de operações é suficiente?
5. O resultado se mantém em outros períodos?
6. Um modelo mais simples chega ao mesmo?

Em dados financeiros, a maioria dos "achados" some quando se corrige uma dessas perguntas.

## 8. O que estudar, em ordem

Só o necessário para a V0.2. Tudo gratuito ou barato:

1. **Fundamentos de ML supervisionado**: *An Introduction to Statistical Learning* (ISLR, James et al.), capítulos de regressão, classificação e validação cruzada. O PDF é gratuito.
2. **XGBoost na prática**: a documentação oficial do XGBoost e um tutorial com `scikit-learn`.
3. **Validação em séries temporais e finanças**: capítulos sobre backtest e validação em *Advances in Financial Machine Learning* (López de Prado) — é denso, leia só esses temas. Termos a buscar: *walk-forward*, *purged cross-validation*, *embargo*, *overfitting de backtest*.
4. **Estatística básica**: o que é uma amostra pequena, intervalo de confiança e por que testar muitas coisas aumenta a chance de achar sorte.

## 9. Como usar este guia com o Claude

Perguntas que valem fazer a cada entrega da V0.2:

- "Como você garantiu que esta feature só usa informação até o instante da decisão?"
- "Qual é o resultado dos baselines B0, B1 e B2 no mesmo período?"
- "Quantas operações o modelo fez e qual é o intervalo de incerteza?"
- "Quantas vezes o teste foi usado até aqui?"
- "O que acontece com o resultado se os custos dobrarem?"

Se alguma resposta for vaga, o resultado não está pronto para ser aceito.
