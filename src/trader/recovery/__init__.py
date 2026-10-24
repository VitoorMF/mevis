"""Recuperação de lacunas por canal (doc 06 §9.1; RF-146 a RF-149).

Roda como primeira etapa do pipeline horário (doc 03 §6.4). Grava o que obtém na camada bruta,
em canais próprios, e atualiza o status das lacunas; a normalização da hora faz o resto.
"""
