"""Configuration gunicorn, bakée dans l'image et chargée automatiquement depuis
/app (WORKDIR).

Pourquoi un fichier de conf plutôt que seulement des flags dans la commande :
gunicorn lit ce fichier même quand la commande de lancement ne passe pas les
bons flags — typiquement un ``docker-compose.yml`` obsolète qui surcharge la
commande de l'image. Les valeurs ci-dessous s'appliquent alors quand même, ce
qui évite qu'un appel wineapi lent (identification texte = 2 appels séquentiels,
vision) ne dépasse le ``--timeout`` par défaut de gunicorn (30 s) et fasse tuer
le worker (WORKER TIMEOUT / SIGKILL).

Tout reste surchargeable par variable d'environnement.
"""

import os

bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.getenv("GUNICORN_WORKERS", "3"))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
# Doit rester > à la somme des timeouts wineapi d'une requête (identification
# texte = identify + detail). 120 s laisse une marge confortable.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
