"""Testes de ``petwatch``.

Rodar com::

    QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .
"""

import logging

# Vários testes exercitam caminhos de erro que, de propósito, registram
# avisos. Sem isso a saída do unittest fica poluída com mensagens
# esperadas. Use ``self.assertLogs`` quando a asserção for sobre o log.
logging.getLogger("petwatch").setLevel(logging.CRITICAL)
