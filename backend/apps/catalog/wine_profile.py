"""Transformation d'un détail wineapi.io (`GET /wines/{id}`) en champs de fiche.

Fonctions *pures* (aucun réseau, aucune BDD) : elles mappent le JSON brut de
wineapi vers les structures attendues par la fiche vin. Testables directement à
partir d'un dictionnaire d'exemple. Quand une donnée manque, on renvoie `None`
ou on laisse l'appelant retomber sur le conseil dérivé de la couleur
(`sommellerie`).
"""

from __future__ import annotations

# Mots-clés -> emoji pour illustrer un accord mets-vins.
_FOOD_EMOJI: list[tuple[tuple[str, ...], str]] = [
    (("beef", "bœuf", "boeuf", "steak"), "🥩"),
    (("lamb", "agneau", "mouton"), "🍖"),
    (("game", "gibier", "venison", "duck", "canard"), "🦌"),
    (("pork", "porc", "charcuterie", "ham", "jambon"), "🥓"),
    (("chicken", "poultry", "volaille", "turkey"), "🍗"),
    (("fish", "poisson", "salmon", "tuna"), "🐟"),
    (("shellfish", "seafood", "fruits de mer", "shrimp", "crab", "oyster", "huître"), "🦐"),
    (("sushi", "sashimi"), "🍣"),
    (("cheese", "fromage"), "🧀"),
    (("salad", "salade", "vegetable", "légume"), "🥗"),
    (("spicy", "épicé", "curry"), "🌶️"),
    (("dessert", "chocolate", "chocolat", "cake", "gâteau"), "🍰"),
    (("pasta", "pizza", "risotto"), "🍝"),
    (("aperitif", "apéritif", "appetizer"), "🥂"),
]


def food_emoji(nom: str) -> str:
    """Devine un emoji représentatif d'un plat (défaut : couvert)."""
    bas = (nom or "").lower()
    for cles, emoji in _FOOD_EMOJI:
        if any(c in bas for c in cles):
            return emoji
    return "🍽️"


def _echelle(mots: dict[str, float], valeur) -> float | None:
    """Mappe une chaîne descriptive (ex. « Full-bodied ») vers 0..1."""
    if not isinstance(valeur, str):
        return None
    bas = valeur.lower()
    for cle, note in mots.items():
        if cle in bas:
            return note
    return None


_CORPS = {"full": 0.85, "medium-full": 0.7, "medium": 0.6, "light-medium": 0.45, "light": 0.3}
_ACIDITE = {"high": 0.8, "medium-high": 0.7, "medium": 0.5, "medium-low": 0.35, "low": 0.25}


def profil_gustatif(detail: dict, defaut: list[dict]) -> list[dict]:
    """Profil gustatif : corps et acidité viennent de wineapi si présents, le
    tanin reste dérivé de la couleur (`defaut`). `defaut` fournit aussi les
    libellés des axes et sert de repli complet."""
    puissance = _echelle(_CORPS, detail.get("body"))
    acidite = _echelle(_ACIDITE, detail.get("acidity"))
    if puissance is None and acidite is None:
        return defaut
    axes = [dict(a) for a in defaut]
    if len(axes) == 3:
        if puissance is not None:
            axes[0]["valeur"] = puissance
        if acidite is not None:
            axes[2]["valeur"] = acidite
    return axes


def accords_mets(detail: dict) -> list[dict] | None:
    """Accords mets-vins réels (avec score de confiance), triés par confiance."""
    pairings = detail.get("pairings")
    if not pairings:
        return None
    accords = []
    for p in pairings:
        if not isinstance(p, dict) or not p.get("food"):
            continue
        conf = p.get("confidence")
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        accords.append({"nom": p["food"], "emoji": food_emoji(p["food"]), "confiance": conf})
    if not accords:
        return None
    accords.sort(key=lambda a: a["confiance"] or 0, reverse=True)
    return accords


def note_communaute(detail: dict) -> dict | None:
    """Note communautaire moyenne, ramenée sur 5 (wineapi peut noter sur 100)."""
    note = detail.get("averageRating")
    nb = detail.get("ratingsCount")
    if note is None:
        return None
    try:
        note = float(note)
    except (TypeError, ValueError):
        return None
    if note > 5:  # échelle centésimale -> ramenée sur 5
        note /= 20
    return {"note": round(note, 1), "nb": int(nb or 0)}


def avis_critiques(detail: dict) -> list[dict]:
    """Avis / scores de critiques (reviewer, score, texte, date)."""
    avis = []
    for s in detail.get("scores") or []:
        if not isinstance(s, dict) or not s.get("reviewer"):
            continue
        avis.append(
            {
                "reviewer": s["reviewer"],
                "score": s.get("score"),
                "score_text": s.get("scoreText"),
                "date": s.get("reviewDate"),
            }
        )
    return avis


def prix_marche(detail: dict) -> dict | None:
    """Fourchette de prix marché (`priceRange`)."""
    pr = detail.get("priceRange")
    if not isinstance(pr, dict) or pr.get("min") is None or pr.get("max") is None:
        return None
    return {"min": pr["min"], "max": pr["max"], "devise": pr.get("currency") or "EUR"}


def prix_marchands(detail: dict) -> list[dict]:
    """Prix marchands détaillés (`prices`) : un tarif par marchand, avec lien.

    wineapi.io renvoie, dans le détail d'un vin, une liste `prices`
    [{merchantName, price, currency, url, fetchedAt}] — les offres des cavistes
    partenaires. On la ramène à `{marchand, prix, devise, url}` (le tarif le plus
    bas d'abord) pour alimenter la section « Prix par marchand » de la fiche."""
    offres = []
    for p in detail.get("prices") or []:
        if not isinstance(p, dict) or p.get("price") is None:
            continue
        try:
            prix = float(p["price"])
        except (TypeError, ValueError):
            continue
        offres.append(
            {
                "marchand": _texte(p.get("merchantName")),
                "prix": prix,
                "devise": _texte(p.get("currency")) or "EUR",
                "url": _texte(p.get("url")),
                # Fraîcheur du tarif (« prix relevé le… ») : date de relevé wineapi.
                "releve_le": _jour(p.get("fetchedAt")),
            }
        )
    offres.sort(key=lambda o: o["prix"])
    return offres


def _jour(valeur) -> str:
    """Ramène un timestamp wineapi (`fetchedAt`, ISO 8601) à sa date `YYYY-MM-DD`.

    Tolérant : accepte une date seule ou un datetime (avec heure / suffixe `Z`),
    renvoie `''` si la valeur est absente ou non exploitable."""
    texte = _texte(valeur)
    if len(texte) >= 10 and texte[4] == "-" and texte[7] == "-":
        return texte[:10]
    return ""


def points_historique_prix(detail: dict) -> list[dict]:
    """Points d'historique de prix dérivés des offres marchands datées.

    wineapi renvoie, pour chaque offre `prices[]`, un `fetchedAt` (date de relevé).
    On regroupe les offres **par jour de relevé** et on retient, pour chaque jour,
    le prix le plus bas et le plus haut observés — soit une observation
    `{date, prix_min, prix_max, devise}` par jour. Un seul appel wineapi peut donc
    déjà semer plusieurs points si les marchands ont été relevés à des dates
    différentes ; les appels suivants (bouton « actualiser ») enrichissent la série.
    Fonction pure : les offres sans prix ou sans date exploitable sont ignorées.
    """
    par_jour: dict[str, dict] = {}
    for p in detail.get("prices") or []:
        if not isinstance(p, dict) or p.get("price") is None:
            continue
        jour = _jour(p.get("fetchedAt"))
        if not jour:
            continue
        try:
            prix = float(p["price"])
        except (TypeError, ValueError):
            continue
        devise = _texte(p.get("currency")) or "EUR"
        point = par_jour.get(jour)
        if point is None:
            par_jour[jour] = {"date": jour, "prix_min": prix, "prix_max": prix, "devise": devise}
        else:
            point["prix_min"] = min(point["prix_min"], prix)
            point["prix_max"] = max(point["prix_max"], prix)
    return [par_jour[j] for j in sorted(par_jour)]


def _texte(valeur) -> str:
    """Chaîne nettoyée (None -> '')."""
    return valeur.strip() if isinstance(valeur, str) else ""


def _nom(objet) -> str:
    """Nom d'un objet wineapi {id, name} ou d'une chaîne."""
    if isinstance(objet, dict):
        return _texte(objet.get("name"))
    return _texte(objet)


def cepages_noms(detail: dict) -> list[str]:
    """Noms des cépages (`grapes`)."""
    return [_nom(g) for g in (detail.get("grapes") or []) if _nom(g)]


def normalize_detail(detail: dict) -> dict:
    """Aplati un détail wineapi (`GET /wines/{id}`) vers les champs persistés sur
    la cuvée. Fonction pure : renvoie un dict prêt à poser sur le modèle (les
    valeurs absentes sont `''`, `None` ou `[]`)."""
    region = detail.get("region") if isinstance(detail.get("region"), dict) else {}
    note = note_communaute(detail) or {}
    prix = prix_marche(detail) or {}
    return {
        "region": _nom(detail.get("region")),
        "pays": _texte(region.get("country")),
        "classification": _texte(detail.get("classification")),
        "description": _texte(detail.get("description")),
        "elaborate": _texte(detail.get("elaborate")),
        "corps": _texte(detail.get("body")),
        "acidite": _texte(detail.get("acidity")),
        "degre_alcool": detail.get("alcoholContent"),
        "image_url": _texte(detail.get("imageUrl")),
        "lwin_code": _texte(detail.get("lwinCode")),
        "note_moyenne": note.get("note"),
        "nb_notes": note.get("nb"),
        "prix_min": prix.get("min"),
        "prix_max": prix.get("max"),
        "devise": prix.get("devise", ""),
        "accords": accords_mets(detail) or [],
        "scores": avis_critiques(detail),
        "prix_marchands": prix_marchands(detail),
        "cepages": cepages_noms(detail),
    }
