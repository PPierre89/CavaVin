# Revue d'architecture — le référentiel comme socle de vérité

> **Statut** : revue d'architecture, **phases 0 à 4 livrées** (seule la phase 5,
> le cadre scraping, reste optionnelle et non engagée). Ce document complète
> `README.md` (spec produit) et `CLAUDE.md` (conventions).
>
> **Comment lire ce document.** Les sections 2 et 3 (« État actuel »,
> « Diagnostic ») sont l'instantané pris **au moment de la revue** : elles
> décrivent les défauts qui ont motivé la trajectoire, pas le code d'aujourd'hui.
> Les sections 4 et 5 décrivent la cible et les règles d'arbitrage **en vigueur**.
> Le §6 fait foi sur ce qui est réellement implémenté, phase par phase.

## 1. Vision cible

> *Une seule base — le **référentiel** — est le socle de l'application. Elle se
> source par différents canaux (LWIN, wineapi, LLM, scraping, code-barres…), est
> **constamment consolidée**, et fait **autorité** : c'est la source de vérité.*

Trois principes en découlent :

1. **Un identifiant canonique de vin**, stable, indépendant du canal qui l'a
   introduit. Deux canaux qui décrivent le même vin doivent converger vers la
   **même** ligne, pas en créer deux.
2. **La donnée porte sa provenance.** Chaque champ consolidé sait de quel canal
   il vient, quand, et avec quelle confiance — condition nécessaire pour
   arbitrer un désaccord entre canaux et pour re-consolider sans repartir de
   zéro.
3. **La consolidation est un processus, pas un effet de bord.** Aujourd'hui la
   « fusion » se joue implicitement dans l'ordre de la cascade et dans un
   « ne pas écraser par du vide ». La cible en fait une étape nommée, testable
   et rejouable.

## 2. État actuel

### 2.1 Cartographie

```
 CANAUX (apps/catalog/enrichment/)          RÉFÉRENTIEL (apps/catalog/models)
 ──────────────────────────────────         ─────────────────────────────────
  OpenFoodFacts  ─┐                           Domaine
  Claude (LLM)    │   NormalizedWine           └─ Cuvee ──< Cepage (M2M)
  wineapi.io      ├──────────────► ingest.upsert_cuvee ──► (une ligne = un vin)
  LWIN + OCR      │                 + enrich_cuvee_from_wineapi
  Vivino (stub)  ─┘                                │
                                                   └─ colonnes d'enrichissement
                                     ReferenceLwin  (silo d'identités, ~200k,
                                                     NON relié à Cuvee)
```

- **`Cuvee`** est aujourd'hui *à la fois* l'identité du vin (domaine, nom,
  couleur, appellation, cépages, code-barres) **et** le réceptacle de
  l'enrichissement volatil (prix, notes communautaires, scores, accords,
  historique de prix, + le payload brut `wineapi_detail`).
- **`ingest.upsert_cuvee`** est le point d'entrée unique de persistance,
  partagé par tous les canaux via le contrat `NormalizedWine`. Bonne base : la
  normalisation (`wine_profile.normalize_detail`) est déjà mutualisée.
- **`ReferenceLwin`** est importée par `manage.py import_lwin` et sert
  *uniquement* d'index de correspondance floue au provider `lwin`. Elle ne
  consolide rien dans `Cuvee` : au mieux, `lwin_code` est recopié en texte.
- La qualité des millésimes par région (`apogee.MILLESIMES`) est une **table
  codée en dur** dans la logique métier, pas une donnée du référentiel.

### 2.2 Ce qui marche déjà bien (à préserver)

- Un **contrat de canal** propre (`EnrichmentProvider` → `NormalizedWine`) et
  une cascade ordonnée : ajouter un canal = ajouter un provider.
- Une **persistance unique** (`ingest`) et une **normalisation unique**
  (`wine_profile`) : le schéma cible doit rester derrière ce goulot.
- La conservation du **payload brut** (`wineapi_detail`) : aucune information
  amont n'est perdue. C'est le bon réflexe — à généraliser à tous les canaux.
- La **mise en cache** (TTL registry + cooldown de synchro) qui protège les
  quotas.

## 3. Diagnostic — écarts avec la vision

### D1. Le schéma est couplé à wineapi, pas agnostique au canal

`reference_externe_id` suppose *une* base externe ; `wineapi_detail`,
`enrichi_le`, et les commentaires des colonnes d'enrichissement sont
explicitement wineapi. Or Claude écrit **les mêmes colonnes** via le même
chemin de normalisation. Le modèle raconte « enrichi par wineapi » alors que la
réalité est « enrichi par le dernier canal de la cascade ». Un vrai référentiel
multi-source doit stocker le brut **par canal**, pas dans un unique slot nommé
d'après l'un d'eux.

### D2. Aucune provenance par champ → arbitrage impossible

Rien n'indique quel canal a fourni `couleur`, `note_moyenne` ou `description`,
ni quand. `enrichi_le` est un horodatage global unique. Conséquence : si Claude
dit `ROUGE` et wineapi `ROSE`, le gagnant est **le dernier à avoir écrit**
(ordre de cascade), sans traçabilité ni possibilité de préférer la source la
plus fiable pour ce champ. La règle actuelle « ne pas écraser une valeur
existante par du vide » (`ingest._ENRICH_FIELDS`) est un arbitrage *implicite et
pauvre* : elle protège contre l'effacement mais pas contre l'écrasement d'une
bonne valeur par une moins bonne.

### D3. La déduplication est fragile — l'unicité canonique n'est pas garantie

`upsert_cuvee` déduplique via `code_barres`, puis `reference_externe_id`, puis
`(domaine, nom)` — **aucune de ces clés n'a de contrainte d'unicité en base**.
Le code utilise volontairement `filter().first()` pour ne pas planter sur les
doublons existants (cf. le commentaire dans `ingest.py`). Autrement dit : les
doublons *s'accumulent en silence*. Un « socle constamment consolidé » exige
l'inverse — une identité canonique contrainte, et une stratégie de fusion des
doublons détectés.

Cas aggravant : `Domaine.objects.get_or_create(nom=…, region="")` force
`region=""` à chaque création par un canal, alors que `Domaine` porte une
`UniqueConstraint(nom, region)`. Un domaine créé à la main avec une région et le
même domaine créé par un canal (région vide) deviennent **deux domaines
distincts** pour le même producteur.

### D4. LWIN est un silo, pas un canal de consolidation

`ReferenceLwin` (~200 000 identités : producteur, vin, région, pays, couleur,
classification) est la meilleure source d'**identité** disponible, 100 % locale
et gratuite — mais elle vit à côté du référentiel et n'y déverse jamais rien.
Elle sert d'index de matching puis est oubliée. Dans la cible, LWIN devrait
**alimenter et réconcilier** les identités `Cuvee` (le `lwin` comme clé
canonique d'identité forte), au lieu de rester une table morte reliée par une
simple chaîne recopiée.

### D5. Identité stable et données volatiles sont mélangées sur une même ligne

Sur `Cuvee` cohabitent des données à cadences de rafraîchissement radicalement
différentes :

| Nature | Exemples | Cadence |
|---|---|---|
| Identité (stable) | domaine, nom, couleur, appellation, cépages, lwin, code-barres | quasi immuable |
| Profil (lent) | description, corps, acidité, classification, degré | rare |
| Marché (volatil) | prix_min/max, note_moyenne, scores, prix_marchands, historique_prix | fréquent |

Les mélanger complique la consolidation (on ne peut pas re-sourcer le marché
sans risquer l'identité), gonfle chaque ligne, et brouille la question « cette
donnée fait-elle autorité ? » (l'identité, oui ; un prix relevé il y a 3 mois,
beaucoup moins).

### D6. La donnée millésimée n'est pas dans le référentiel

La qualité d'un millésime par région est une donnée **du monde réel**, exactement
le genre de fait qu'un référentiel sourcé par canaux devrait porter (et qu'un LLM
ou un scraping de tables de millésimes pourrait alimenter). Elle est aujourd'hui
figée dans `apogee.MILLESIMES`. Toute mise à jour exige un déploiement de code.

### D7. Le canal « scraping » évoqué n'a pas de cadre

La vision cite le scraping ; seul un stub Vivino existe (désactivé — scraping
contraire aux CGU, à ne pas implémenter tel quel). Avant d'ouvrir ce canal, il
faut un cadre commun : provenance obligatoire, horodatage, respect des CGU/robots,
limitation de débit, et **confiance basse par défaut** (le scraping arbitre en
dernier). Le contrat `EnrichmentProvider` est prêt à l'accueillir ; c'est la
couche de consolidation qui doit exister d'abord.

## 4. Architecture cible proposée

Idée directrice : **séparer trois responsabilités** aujourd'hui fondues dans
`Cuvee` — l'**identité** canonique, les **observations** brutes par canal, et la
**projection consolidée** qui fait autorité.

```
                         ┌───────────────────────────────────────┐
   canaux  ── ingest ──► │ SourceObservation                     │  (append-only)
                         │  cuvee_id · canal · releve_le          │
                         │  confiance · payload_brut (JSON)       │
                         │  champs_normalisés (JSON)              │
                         └──────────────┬────────────────────────┘
                                        │  consolidate(cuvee)
                                        ▼
   Domaine ─< Cuvee (IDENTITÉ) ───────► champs consolidés + provenance/champ
                 │  clés canoniques :        (couleur, description, prix… avec
                 │   lwin · code_barres       canal+date+confiance par champ)
                 │   ref_externe{canal:id}
                 └─< Cepage (M2M)
```

### 4.1 `Cuvee` — resserrée sur l'identité

Conserver sur `Cuvee` uniquement ce qui **identifie** le vin et fait autorité de
façon durable : `domaine`, `nom`, `couleur`, `appellation`, `cepages`,
`code_barres`, `lwin_code`. Remplacer `reference_externe_id` (mono-source) par un
mapping multi-canal `references_externes = {canal: id}` (JSON) — ou une petite
table `ReferenceExterne(cuvee, canal, id_externe)` avec `unique(canal,
id_externe)`.

**Clés canoniques d'unicité**, par ordre de force : `lwin` > `code_barres` >
`(canal, id_externe)` > `(domaine, nom, couleur)`. Chacune contrainte en base
(`UniqueConstraint`, partielle pour tolérer les valeurs vides) pour rendre les
doublons *impossibles*, pas seulement improbables.

> **Force ≠ ordre d'essai.** Cet ordre classe la *fiabilité* d'une clé quand deux
> se contredisent. L'ordre dans lequel `ingest` les interroge est distinct (et
> l'implémentation en place le fait déjà) : cf. §5, règle 0 — ce qui départage
> une clé n'est pas sa force mais la conclusion qu'on peut tirer de son **absence**.

### 4.2 `SourceObservation` — le brut par canal (nouveau)

Une ligne par relevé de canal (append-only, jamais écrasée) :

```
SourceObservation
  cuvee            FK Cuvee
  canal            "wineapi" | "claude" | "lwin" | "openfoodfacts" | "scrape:x"
  releve_le        datetime
  confiance        0..1   (défaut par canal, ajustable)
  payload_brut     JSON   (généralise wineapi_detail à TOUS les canaux)
  champs           JSON   (sortie normalize_detail : ce que ce relevé affirme)
```

Bénéfices : aucune donnée amont perdue (D1), historique complet, re-consolidation
rejouable, et base naturelle pour l'historique de prix (une observation = un
point) au lieu du bricolage `_fusionne_historique_prix`.

### 4.3 Projection consolidée + provenance par champ

Les colonnes de profil/marché (`region`, `description`, `corps`, `note_moyenne`,
`prix_*`, `scores`, `accords`…) restent des colonnes de `Cuvee` **projetées**
(pour garder les requêtes/tri simples et l'API inchangée), mais deviennent le
**résultat** de la consolidation, accompagnées d'une carte de provenance :

```
provenance = {
  "couleur":      {"canal": "lwin",    "date": "…", "confiance": 0.95},
  "note_moyenne": {"canal": "wineapi", "date": "…", "confiance": 0.80},
  "description":  {"canal": "claude",  "date": "…", "confiance": 0.70},
  ...
}
```

Alternative si l'on préfère isoler complètement le volatil : sortir prix/notes
vers `DonneesMarche(cuvee, canal, releve_le, …)` et exposer « la plus récente et
la plus fiable » en lecture. Recommandé en phase 3, pas d'emblée.

### 4.4 `MillesimeReference` — sortir la table millésimes du code (D6)

```
MillesimeReference
  region_cle   "bordeaux" | "bourgogne" | …   (aligné sur apogee._ALIAS_REGION)
  annee        int
  note         int 1..5
  source       "manuel" | "claude" | "scrape:…"
  unique(region_cle, annee)
```

`apogee.qualite_millesime` lit alors la table (avec le dict actuel en *seed* et
en repli offline). La logique pure reste testable en injectant la table.

## 5. Stratégie de consolidation

Fonction `consolidate(cuvee)` — pure autant que possible, déclenchée après chaque
`ingest` et rejouable en masse (commande `manage.py reconsolider`) :

0. **Résoudre l'identité** (en amont, dans `ingest.upsert_cuvee`) : confronter au
   catalogue **toutes** les identités fortes affirmées par le relevé, dans l'ordre
   `code_barres` > `reference_externe_id` > `lwin_code`, avant d'envisager une
   création. Cet ordre reflète la spécificité du chemin d'entrée (le scan d'un
   code-barres désigne un conditionnement précis, la référence externe un vin,
   le code LWIN une identité parfois partagée) ; mais le point décisif n'est pas
   l'ordre — c'est la conclusion que chaque clé autorise :

   | Clé | Trouvée | Absente du catalogue |
   |---|---|---|
   | `code_barres` | c'est ce vin | **non concluant** → essayer la clé suivante |
   | `reference_externe_id` | c'est ce vin | décisif → vin distinct, on crée |
   | `lwin_code` | c'est ce vin | décisif → vin distinct, on crée |

   L'asymétrie est délibérée : un même vin se décline en plusieurs
   conditionnements, donc en plusieurs code-barres, alors qu'un identifiant
   wineapi est 1:1 avec un vin. Sans identité forte au relevé, repli sur
   `(domaine, nom)`.

   **Sans aucune identité forte**, le repli est `(domaine, nom_normalise)` — la
   forme canonique du nom (minuscules, sans accents ni ponctuation), pas la chaîne
   brute. C'est le cas de *toute* identification par LLM, qui ne fournit ni
   code-barres ni référence distante : comme un modèle ne rend jamais deux fois la
   même chaîne, comparer les noms bruts laissait « Grand Vin », « Grand vin » et
   « Grand Vin » (espace final) créer trois cuvées du même vin. Ce repli porte
   désormais lui aussi une contrainte d'unicité (partielle), si bien qu'aucune des
   quatre clés n'est plus une simple convention.

   Deux corollaires : (a) les identités **manquantes** de la cuvée retrouvée sont
   complétées par celles du relevé (`_completer_identites`), de sorte qu'un vin
   d'abord identifié par son nom puis scanné finisse par porter son code-barres —
   le scan suivant est alors un hit local, sans quota ; (b) une identité **déjà
   revendiquée** par une autre cuvée n'est jamais volée ni recopiée (deux vins
   distincts peuvent exceptionnellement partager un code LWIN) : le premier
   arrivé la garde, plutôt que de violer la contrainte et de faire échouer le scan.

1. **Rassembler** les `SourceObservation` de la cuvée.
2. **Par champ**, choisir la valeur gagnante selon une politique explicite :
   - **identité** (couleur, appellation, cépages) : priorité à la confiance la
     plus haute ; LWIN > Claude > wineapi > OFF > scraping. Égalité → le plus
     récent.
   - **profil** (description, corps…) : la source la plus riche/fiable ; ne
     jamais remplacer par du vide (règle actuelle conservée, mais désormais
     *au niveau de la consolidation*, pas de l'ingest).
   - **marché** (prix, notes) : le relevé **le plus récent** prime (le prix
     d'hier > le prix de l'an dernier), indépendamment du canal.
3. **Écrire** la projection + la carte de provenance ; **ne pas** créer de
   doublon (les clés canoniques garantissent l'unicité).
4. **Détecter/fusionner les doublons** existants : job de réconciliation qui
   rapproche par `lwin`/`code_barres`/similarité forte, fusionne les
   `SourceObservation`, re-pointe les `Bouteille`/`NoteDegustation` (via `PROTECT`,
   déjà en place) et supprime le doublon.

Confiances par défaut suggérées (ajustables) : `lwin 0.95`, `openfoodfacts 0.9`
(barcode = identité forte), `claude 0.75`, `wineapi 0.7` (identité) / `0.85`
(marché, sa spécialité), `scrape:* 0.4`.

## 6. Plan de migration (incrémental, sans big-bang)

Chaque phase est déployable seule, garde l'API et le comportement actuels, et
respecte les conventions (`makemigrations` commité, tests, commits conventionnels).

- **Phase 0 — Verrouiller l'identité (fix, faible risque). ✅ *Faite.***
  Contraintes d'unicité canoniques partielles sur `Cuvee.code_barres` et
  `Cuvee.reference_externe_id` ; `ingest` rattache désormais un producteur à sa
  fiche existante (avec région) au lieu d'en créer une vide (`_domaine_pour`).
  Migration `0008_dedup_identite_cuvee` : dédoublonne d'abord les `Domaine`
  scindés et les `Cuvee` en double sur une clé forte (en re-pointant le stock
  privé), puis pose les contraintes. → traite D3.

  *Correctif ultérieur — résolution multi-clés.* Poser les contraintes a révélé
  un angle mort : `upsert_cuvee` ne testait que la **première** clé forte
  renseignée par le relevé. Un vin déjà connu par sa `reference_externe_id` mais
  scanné par un code-barres inédit n'était donc pas retrouvé, et sa création
  violait la contrainte d'unicité de la référence externe — `IntegrityError`,
  soit un **500 sur le scan**. La résolution confronte désormais *toutes* les
  identités du relevé, avec une asymétrie assumée (cf. §5, règle 0).

  *Second correctif — le nom comme identité contrainte.* Il restait une porte
  ouverte : une cuvée sans identité forte retombait sur `(domaine, nom)`, clé
  **sans contrainte** et comparée de façon exacte. Or c'est le cas de toute
  identification par LLM. `Cuvee.nom_normalise` (forme canonique dérivée de `nom`)
  porte désormais une contrainte d'unicité partielle avec `domaine`, et la
  migration `0016_dedup_nom_cuvee` fusionne d'abord les doublons existants — en
  re-pointant stock, notes et observations, et en reprenant du doublon ce qu'il
  était seul à porter. Les quatre clés de déduplication sont maintenant toutes
  contraintes : D3 est clos.

- **Phase 1 — `SourceObservation` (feat). ✅ *Faite.***
  Table `SourceObservation` (append-only : `cuvee`, `canal`, `releve_le`,
  `confiance`, `payload_brut`, `champs`). `ingest.upsert_cuvee` y dépose une
  observation pour **tout** canal (via `NormalizedWine.source`/`raw`) et
  `ingest.synchroniser_wineapi` en dépose une pour la synchro wineapi
  manuelle/paresseuse des vues. Confiance a priori par canal (`_CONFIANCE_CANAL`,
  §5). `Cuvee.wineapi_detail` conservé en lecture le temps de la bascule. → traite D1.

- **Phase 2 — Consolidation + provenance (feat). ✅ *Faite.***
  `consolidation.consolider(cuvee)` arbitre explicitement les observations en
  fiche de vérité (profil → confiance puis récence ; marché → récence puis
  confiance) et écrit une carte de provenance `Cuvee.provenance`
  (`{champ: {canal, date, confiance}}`). Appelée après chaque relevé dans
  `ingest` ; commande `manage.py reconsolider` pour rejouer en masse. → traite
  D2, D5 (partiel). *Reste :* la consolidation ne supprime jamais une valeur
  qu'aucune source ne contredit ; les cépages (M2M) restent gérés par
  `enrich_cuvee_from_wineapi` (arbitrage inter-canaux à affiner).

- **Phase 3 — LWIN comme canal du référentiel (refactor). ✅ *Faite.***
  Le code LWIN devient une **identité canonique** de la cuvée : clé de
  déduplication (entre relevés LWIN et avec les cuvées wineapi qui portent un
  `lwinCode`) et contrainte d'unicité partielle sur `Cuvee.lwin_code` (migration
  `0011`, avec dédoublonnage préalable). Le provider LWIN transmet son **score de
  correspondance floue** comme confiance du relevé (`NormalizedWine.confiance`),
  au lieu du défaut de canal — un match faible ne prime plus à la consolidation.
  `lwin_code` est posé à la création (garde-fou d'unicité) et sorti des champs
  enrichis/consolidés (identité, pas attribut). → traite D4.

- **Phase 4 — `MillesimeReference` (feat). ✅ *Faite.***
  Modèle `MillesimeReference` (`region_cle`, `annee`, `note`, `source` ;
  `unique(region_cle, annee)`), semé depuis `apogee.MILLESIMES` (migration `0012`)
  et éditable en admin. `apogee` reste **pur** : `qualite_millesime` /
  `fenetre_apogee` reçoivent la table par injection, avec `apogee.MILLESIMES`
  comme repli hors-ligne ; `Bouteille.fenetre_apogee` injecte
  `MillesimeReference.table()` (mise en cache, invalidée à l'édition). → traite D6.

- **Phase 5 — Cadre scraping (feat, optionnel).**
  Provider scraping avec confiance basse, limitation de débit, respect CGU/robots,
  provenance obligatoire — une fois la consolidation en place pour l'arbitrer. →
  traite D7.

## 7. Risques & garde-fous

- **SQLite** reste le seul datastore : privilégier des JSON indexés et des
  contraintes partielles plutôt que des jointures lourdes ; les colonnes
  projetées gardent les requêtes de liste rapides.
- **Cloisonnement RGPD inchangé** : tout ceci est dans `catalog` (partagé,
  mutualisé). Aucune donnée privée (`cellars`/`inventory`) ne migre ni ne se
  mélange. Les FK privées vers `Cuvee` restent en `PROTECT`.
- **Quotas** : la consolidation lit des observations déjà en base ; elle ne
  re-sollicite **jamais** un canal. Les TTL/cooldown existants restent la seule
  porte vers l'extérieur.
- **Compatibilité API/SPA** : en gardant les colonnes projetées sur `Cuvee`, les
  sérialiseurs et le front (`FicheVin`) n'ont pas à changer avant, au plus tôt,
  la phase 3.
- **Réversibilité** : `SourceObservation` étant append-only et le brut conservé,
  toute politique de consolidation peut être rejouée ou corrigée après coup sans
  perte.

---

*Prochaine étape suggérée : valider la **Phase 0** (verrouillage de l'identité),
qui apporte le plus de valeur immédiate — garantir qu'un vin = une ligne — pour
le risque le plus faible.*
