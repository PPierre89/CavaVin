"""Préparation d'une photo d'étiquette pour les sources d'identification distantes.

Une photo de téléphone pèse couramment 3 à 12 Mo pour 4000 px de côté. Les APIs
de vision (Claude, wineapi, GrapeMinds) **redimensionnent de toute façon** en
entrée — Anthropic ramène le grand côté à 1568 px —, et l'encodage base64 gonfle
encore la charge de 33 %. Envoyer l'original revient donc à payer, en temps de
téléversement (souvent depuis un mobile, via un NAS domestique) et en jetons
d'entrée facturés, des pixels que personne ne lira.

On réduit donc **une seule fois** la photo avant la cascade, et toutes les
sources distantes se partagent cette version. L'OCR local (provider ``lwin``)
fait exception et reçoit l'original : sa phase de recadrage relit la zone de
texte *en pleine résolution*, c'est précisément là qu'une étiquette lointaine
redevient lisible (cf. ``EnrichmentProvider.image_pleine_resolution``).

Best-effort : sans Pillow, sur une image illisible ou déjà petite, l'original
est renvoyé tel quel — jamais d'échec d'identification pour un défaut de
redimensionnement.
"""

from __future__ import annotations

import logging
from io import BytesIO

logger = logging.getLogger(__name__)

# Grand côté visé : la limite au-delà de laquelle l'API Anthropic redimensionne
# elle-même. Au-dessus on transporte des pixels qui seront jetés à l'arrivée.
TAILLE_MAX = 1568
# Qualité JPEG du ré-encodage : au-delà, le gain de lisibilité sur une étiquette
# est nul et le poids repart à la hausse.
QUALITE_JPEG = 85


def reduire(data: bytes, content_type: str = "") -> tuple[bytes, str]:
    """Photo réduite pour un envoi distant -> ``(octets, content_type)``.

    Applique l'orientation EXIF (une photo de téléphone couchée arriverait
    sinon tournée chez le fournisseur), ramène le grand côté à ``TAILLE_MAX`` et
    ré-encode en JPEG. Renvoie l'entrée inchangée si elle est déjà assez petite
    ou si quoi que ce soit échoue.
    """
    try:
        from PIL import Image, ImageOps

        image = Image.open(BytesIO(data))
        grand_cote = max(image.size)
        if grand_cote <= TAILLE_MAX and (content_type or "").endswith("jpeg"):
            # Déjà au gabarit et déjà en JPEG : ré-encoder ne ferait que dégrader.
            return data, content_type

        image = ImageOps.exif_transpose(image)
        # La transparence n'a pas de sens sur une étiquette et JPEG ne la gère
        # pas : on aplatit sur blanc plutôt que de produire un fond noir.
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            fond = Image.new("RGB", image.size, (255, 255, 255))
            fond.paste(image, mask=image.split()[-1])
            image = fond
        else:
            image = image.convert("RGB")

        grand_cote = max(image.size)
        if grand_cote > TAILLE_MAX:
            facteur = TAILLE_MAX / grand_cote
            image = image.resize(
                (max(1, round(image.width * facteur)), max(1, round(image.height * facteur))),
                Image.LANCZOS,
            )

        tampon = BytesIO()
        image.save(tampon, "JPEG", quality=QUALITE_JPEG, optimize=True)
        reduite = tampon.getvalue()
        # Garde-fou : sur une petite image déjà bien compressée, le ré-encodage
        # peut alourdir. On ne garde la réduction que si elle gagne vraiment.
        if len(reduite) >= len(data):
            return data, content_type
        return reduite, "image/jpeg"
    except Exception as exc:  # Pillow absent, image corrompue, format exotique…
        logger.warning("réduction d'image impossible (%s), original conservé", exc)
        return data, content_type


# Grand côté de la vignette d'étiquette conservée au catalogue : assez pour
# relire le nom du vin à l'écran, assez petit pour que quelques milliers de
# cuvées ne pèsent pas sur le volume d'un NAS (~50 à 100 Ko par photo).
TAILLE_VIGNETTE = 800
# Marge laissée autour de la zone de texte détectée (part de chaque dimension) :
# une étiquette rognée au ras des lettres est laide et perd son cadre.
_MARGE = 0.06
# Budget du repérage : une seule passe, courte. On est dans le temps de réponse
# d'un scan, et l'enjeu n'est qu'esthétique — un repli sur la photo entière est
# parfaitement acceptable.
_TIMEOUT_REPERAGE = 6


def recadrer_etiquette(data: bytes) -> tuple[bytes, str]:
    """Vignette de l'étiquette, recadrée sur le texte -> ``(octets, content_type)``.

    Réutilise le repérage de zone de texte déjà écrit pour l'OCR
    (``enrichment.lwin._zone_texte``) : les boîtes de mots de Tesseract
    délimitent l'étiquette dans la photo, ce qui évite de conserver au catalogue
    une bouteille perdue dans une cuisine. Le recadrage retire d'ailleurs
    l'essentiel de ce qui entoure la bouteille — utile puisque cette vignette est
    attachée au catalogue *mutualisé*, donc visible par les autres utilisateurs.

    Best-effort de bout en bout : sans Tesseract, sans Pillow, ou si le repérage
    ne trouve rien, on renvoie la photo simplement redimensionnée. Un scan ne doit
    jamais échouer pour une question de vignette.
    """
    reduite, _ = reduire(data)
    zone = _zone_etiquette(reduite)
    try:
        from PIL import Image, ImageOps

        image = ImageOps.exif_transpose(Image.open(BytesIO(reduite))).convert("RGB")
        if zone:
            gauche, haut, droite, bas = zone
            marge_l = round(image.width * _MARGE)
            marge_h = round(image.height * _MARGE)
            image = image.crop((
                max(0, gauche - marge_l),
                max(0, haut - marge_h),
                min(image.width, droite + marge_l),
                min(image.height, bas + marge_h),
            ))
        grand_cote = max(image.size)
        if grand_cote > TAILLE_VIGNETTE:
            facteur = TAILLE_VIGNETTE / grand_cote
            image = image.resize(
                (max(1, round(image.width * facteur)), max(1, round(image.height * facteur))),
                Image.LANCZOS,
            )
        tampon = BytesIO()
        image.save(tampon, "JPEG", quality=QUALITE_JPEG, optimize=True)
        return tampon.getvalue(), "image/jpeg"
    except Exception as exc:
        logger.warning("recadrage d'étiquette impossible (%s)", exc)
        return reduite, "image/jpeg"


def _zone_etiquette(image: bytes) -> tuple[int, int, int, int] | None:
    """Boîte du texte dans l'image, via une passe Tesseract courte. None si rien."""
    import subprocess

    from django.conf import settings

    from .lwin import _zone_texte

    try:
        resultat = subprocess.run(
            [settings.TESSERACT_CMD, "stdin", "stdout", "--dpi", "300", "--psm", "3", "tsv"],
            input=image,
            capture_output=True,
            timeout=_TIMEOUT_REPERAGE,
        )
        if resultat.returncode != 0:
            return None
        return _zone_texte([resultat.stdout.decode("utf-8", "ignore")])
    except Exception:
        # Binaire absent, délai dépassé… : la photo entière fera l'affaire.
        return None
