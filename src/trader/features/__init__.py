"""Features point-in-time da V0.2 (doc 04 §5; SRS §4.11; DEC-34). Sem E/S.

O mesmo cálculo serve à pesquisa, sobre meses de minutos, e à operação da V0.3, sobre os últimos
1.500 minutos: ele recebe uma série de minutos (`series.MinuteSeries`) e não sabe de onde ela veio
(RF-202). A montagem da série a partir dos arquivos fica em `trader.pipeline.minutes`.
"""
