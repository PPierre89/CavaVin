# Veille jeux de données — remplir le référentiel hors ligne

> **Objet** : quels jeux de données publics (Kaggle en particulier) peuvent
> *remplir* le référentiel `Cuvee` sans clé d'API ni quota, et à quelles
> conditions de licence. Complète `docs/architecture-referentiel.md` (le socle) et
> `CLAUDE.md` (les conventions).
>
> **Verdict** : un seul jeu retenu *sans réserve* — **X-Wines** (§2,
> `manage.py import_xwines`), référentiel à part entière, canal ordinaire. Le
> reste entre par le **cadre scraping**, à confiance basse et sous arbitrage de
> toutes les sources légitimes : le catalogue marchand `elvinrustam` (§5) et les
> exports de notes Vivino / wine.com (§6). Le §3 recense ce qui reste écarté.
>
> ⚠️ **Les sources du §6 sont importées sur décision explicite du mainteneur, en
> écart avec le critère « origine licite » du §1** et avec le refus d'implémenter
> Vivino comme fournisseur (`enrichment/stubs.py`). Le §6 documente cet écart
> plutôt que de le taire.

## 1. Ce qu'on cherche

Le référentiel est **mutualisé** : ce qu'on y verse devient visible de tous les
utilisateurs de l'instance. Un jeu de données n'est donc recevable que s'il coche
les quatre cases :

| Critère | Pourquoi |
|---|---|
| **Licence redistribuable** | Le catalogue est partagé entre comptes et l'image est publiée sur GHCR. Une clause `NC` (non commercial) ou `ND` (pas de dérivé) rend l'usage juridiquement inconfortable, et une clause `SA` (partage à l'identique) est *virale* : elle contaminerait tout le référentiel, alimenté par ailleurs par d'autres canaux. |
| **Origine licite** | Un dump obtenu par scraping d'un site dont les CGU l'interdisent reste illicite une fois reposté sur Kaggle. Le dépôt refuse déjà d'implémenter Vivino et CellarTracker pour ce motif : l'accepter par la porte de Kaggle serait incohérent. |
| **Identité de vin exploitable** | Il faut au minimum **producteur + nom**, la clé de repli de `ingest.upsert_cuvee`. Un jeu sans producteur ne peut pas être dédupliqué et créerait des doublons dans le catalogue partagé. |
| **Profil œnologique, pas physico-chimique** | Les jeux « wine quality » (acidité volatile, sulfites, densité…) décrivent des échantillons de laboratoire anonymes : aucun vin identifiable, donc rien à verser au référentiel. |

## 2. Retenu — X-Wines

**<https://www.kaggle.com/datasets/rogerioxavier/x-wines-slim-version>** ·
dépôt et version complète : <https://github.com/rogerioxavier/X-Wines>

| | |
|---|---|
| Volume | **100 646 vins**, 62 pays (versions *Test* 100 / *Slim* 1 007 / *Full* 100 646) |
| Licence | **Open Database License (ODbL)**, contenus en DbCL — même famille qu'Open Food Facts, déjà exploité ici |
| Origine | Jeu de données **académique** (de Azambuja & al., *Big Data and Cognitive Computing* 7(1), 2023, <https://doi.org/10.3390/bdcc7010020>), collecté puis nettoyé pour publication libre |
| Citation | Obligatoire en cas de publication de travaux dérivés (cf. le dépôt) |

### Pourquoi celui-là

Son schéma se superpose presque colonne pour colonne à `Cuvee` — c'est le seul
jeu examiné dont on ne jette quasiment rien :

| Colonne X-Wines | Destination | Note |
|---|---|---|
| `WineryName` | `Domaine.nom` | clé de repli d'identité avec le nom |
| `WineName` | `Cuvee.nom` (→ `nom_normalise`) | |
| `Type` | `Cuvee.couleur` | `Dessert` / `Dessert-Port` → `AUTRE`, assumé (cf. §4) |
| `Grapes` | `Cuvee.cepages` (M2M) | liste littérale Python dans la cellule |
| `Harmonize` | `Cuvee.accords` | accords mets-vins, sans score (X-Wines ne pondère pas) |
| `ABV` | `Cuvee.degre_alcool` | |
| `Body` / `Acidity` | `Cuvee.corps` / `Cuvee.acidite` | alimentent le profil gustatif de la fiche |
| `Elaborate` | `Cuvee.elaborate` | mono-cépage / assemblage |
| `RegionName` / `Country` | `Cuvee.region` / `Cuvee.pays` + `Domaine.region` / `Domaine.pays` | |
| `Website` | `Domaine.site_web` | |
| `Vintages` | *(payload brut de l'observation)* | une `Cuvee` est indépendante du millésime |

Il **complète** LWIN plutôt qu'il ne le double : LWIN (~200 000 entrées) est un
référentiel d'*identités* (producteur, vin, région, couleur) servant à la
correspondance floue OCR, et ne porte ni cépages, ni degré, ni accords, ni
profil. X-Wines apporte exactement ce qui manque à une **fiche**.

Enfin, il n'apporte **aucune donnée de marché** (ni prix, ni note communautaire),
ce qui est ici une qualité : le volet volatil reste arbitré par récence entre les
canaux interrogés en direct, et un instantané figé de 2022 ne vient pas le
polluer.

### Comment il est branché

Voir `backend/apps/catalog/xwines_import.py` et `manage.py import_xwines`. Le
canal ne court-circuite rien : chaque ligne est convertie en détail **au format
wineapi.io** (comme le fait déjà le canal Claude), traverse
`wine_profile.normalize_detail` puis `ingest.upsert_cuvee`, y dépose une
`SourceObservation` horodatée de confiance `0.60`, et la fiche est consolidée
normalement. Rien n'est écrit en base par un chemin parallèle.

## 3. Examinés et écartés

| Jeu de données | Volume | Licence | Verdict |
|---|---|---|---|
| [Wine Reviews (`zynicide`)](https://www.kaggle.com/datasets/zynicide/wine-reviews) | 130 k avis | **CC BY-NC-SA 4.0** | Le plus connu, et le plus tentant : dégustations Wine Enthusiast avec cépage, domaine, appellation, note et prix. Écarté sur **deux** motifs cumulés — le dump est un *scraping* de winemag.com (2017), et la clause `SA` contaminerait un référentiel par ailleurs alimenté par d'autres canaux, tandis que `NC` cadre mal avec une image publiée. Utilisable pour une exploration locale, pas pour le catalogue mutualisé. |
| [Wine Rating & Price (`budnyak`)](https://www.kaggle.com/datasets/budnyak/wine-rating-and-price) | ~13 k | **CC BY-NC-ND 4.0** | Données Vivino scrapées. `ND` (pas de dérivé) est un verrou dur — un import *est* un dérivé — et l'origine reproduit exactement ce que le stub Vivino refuse. |
| [`dbahri/wine-ratings`](https://www.kaggle.com/datasets/dbahri/wine-ratings) | ~30 k | CC BY-NC-SA 4.0 | Mêmes motifs que Wine Reviews, sans le volume qui pourrait les faire discuter. |
| [Wine Dataset (`elvinrustam`)](https://www.kaggle.com/datasets/elvinrustam/wine-dataset) | 1 290 | **CC0** | **Retenu — mais en canal de *scraping*.** Mesures et réserves au §5. |
| Wine Quality (UCI, et ses innombrables reprises) | 6,5 k | CC BY 4.0 | Hors sujet : mesures physico-chimiques d'échantillons anonymes de *vinho verde*. Aucun vin identifiable, rien à verser au référentiel. |
| `mysarahmadbhat/wine-tasting` | 130 k | CC0 | Re-publication du dump Wine Reviews sous une licence que le re-publieur n'était pas en position d'accorder. Un CC0 apposé après coup ne blanchit pas l'origine : écarté avec l'original. |

**Le motif dominant est structurel** : la donnée vin richement décrite et
gratuite vient presque toujours de Vivino, Wine Enthusiast ou d'un caviste, et
transite par un scraping que la republication sur Kaggle ne régularise pas.
X-Wines fait exception parce qu'il a été constitué, nettoyé et publié dans un
cadre académique, avec une licence explicite.

## 4. Limites connues de l'import X-Wines

- **Instantané 2022.** Un domaine renommé, un vin arrêté ou créé depuis ne
  s'y trouve pas. D'où la confiance de canal `0.60`, sous wineapi (`0.70`) : les
  canaux interrogés en direct doivent primer à la consolidation.
- **Pas d'appellation.** `Cuvee.appellation` reste vide pour les vins importés :
  X-Wines donne la région (`Bordeaux`), pas l'AOC (`Margaux`). C'est le trou que
  `apparier_lwin` comble en priorité (la `sous_region` LWIN), et accessoirement le
  catalogue marchand du §5.
- **Vins de dessert et portos en `AUTRE`.** `Type` vaut `Dessert` ou
  `Dessert/Port` sans dire la couleur ; les déclarer rouges serait faux une fois
  sur deux. `AUTRE` fait retomber la fiche sur le conseil de service générique de
  `sommellerie`, ce qui est le comportement honnête.
- **Pas de code-barres.** L'identification par scan (US 01) continue de reposer
  sur Open Food Facts ; l'import n'aide que la recherche par texte et
  l'autocomplétion du catalogue (`GET /api/cuvees/?search=`).
- **Volume.** ~12 min pour les 100 646 vins sur SQLite (~7 ms/vin, écritures par
  lots). L'import est reprenable : une seconde passe saute les vins déjà connus.

## 5. Retenu sous réserve — le catalogue marchand `elvinrustam`

**<https://www.kaggle.com/datasets/elvinrustam/wine-dataset>** · fichier
`WineDataset.csv` · implémenté par `manage.py import_catalogue_marchand`.

Ce jeu est **le seul de la veille à porter une colonne `Appellation`**, ce que ni
X-Wines ni aucun import en masse ne fournit. C'est à ce titre, et à ce titre
seul, qu'il est retenu. Le reste de son contenu est redondant ou inutilisable.

### Ce que la mesure dit

Chiffres relevés sur le fichier réel (1 290 lignes), pas sur la fiche Kaggle :

| Constat | Mesure |
|---|---|
| Volume | **1 290 lignes** — à comparer aux ~200 000 références LWIN et 100 646 vins X-Wines |
| Appellation renseignée | **644 lignes (49,9 %)**, pour **179 valeurs distinctes** seulement |
| Origine | Catalogue d'un détaillant britannique : **100 % de prix en £**, colonnes « per bottle / per case / each », marques de distributeur |
| Contenu non vinicole | Whisky (`Type = Brown`), tequila — c'est une liste de **boissons**, pas un référentiel de vins |
| Producteur | **Aucune colonne** ; il faut l'extraire du titre. **62 %** des titres seulement sont scindables de façon fiable |

### Pourquoi c'est un canal de scraping, et ce que ça implique

Le CC0 a été apposé par le **déposant**, pas par la source : c'est exactement le
raisonnement qui fait écarter `mysarahmadbhat/wine-tasting` au §3 (« un CC0 apposé
après coup ne blanchit pas l'origine »). Le jeu relève donc du **cadre scraping**
de la revue d'architecture (Phase 5) : provenance obligatoire, confiance basse,
arbitrage en dernier.

Le canal se nomme `scrape:marchand`, ce qui lui vaut **automatiquement `0.40`**
via `ingest._confiance_pour` — sous LWIN (0,95), Claude (0,75), wineapi (0,70) et
X-Wines (0,60). La conséquence est structurelle et voulue : **ce canal comble des
trous, il ne peut dégrader aucune valeur existante.** C'est ce qui rend son
adoption acceptable malgré les réserves ci-dessus.

Trois garde-fous complètent la confiance basse :

1. **Aucune donnée de marché n'est reprise.** Le prix est le tarif de détail du
   marchand — la partie la plus manifestement propriétaire du fichier — et les
   champs de marché s'arbitrent à la **récence**, pas à la confiance : un tarif
   scrapé primerait donc sur un relevé wineapi légitime. Le mapping ne l'expose
   pas du tout.
2. **Les produits non vinicoles sont écartés** (whisky, tequila…), ainsi que les
   `Type` qui ne décrivent pas un vin (`Brown`, `Mixed`).
3. **Une ligne dont le producteur n'est pas isolable est ignorée.** Sans
   producteur il n'existe aucune clé de déduplication `(domaine, nom_normalise)`,
   et créer quand même sèmerait des doublons dans un catalogue mutualisé. Deux
   motifs fiables sont exploités — un segment entre guillemets (« Louis Roederer
   *'Cristal'* Champagne ») et le cépage de la colonne `Grape` (« Oyster Bay
   *Sauvignon Blanc* ») ; hors de ces cas, silence.

L'apostrophe demande une attention particulière : elle délimite la cuvée **et**
marque l'élision française. Un guillemet de cuvée ouvre après un blanc et ferme
avant un blanc ou une virgule, là où l'apostrophe d'élision est collée entre deux
lettres — sans cette règle, « Caves d'Esclans 'Whispering Angel' » donne la cuvée
« Esclans 'Whispering Angel ».

### Résultat sur le fichier réel

1 290 lignes → **729 cuvées** et 489 domaines créés, dont **348 avec appellation** ;
3 lignes écartées comme non vinicoles, 486 faute de producteur isolable, et un seul
nom de cuvée imparfait sur 729. Comptez ~5 s.

### Limites à garder en tête

- **L'apport est modeste** : 179 appellations distinctes. `apparier_lwin` reste de
  très loin la meilleure source d'appellation (la `sous_region` de ~200 000
  références) ; ce canal est un appoint, pas une solution.
- **L'extraction du producteur reste une heuristique.** Elle produit
  occasionnellement un producteur tronqué (« La », « Penfolds Bin A »). La
  confiance basse limite les dégâts, mais un doublon de domaine reste possible.
- **Millésimes et contenances sont ignorés** : une `Cuvee` en est indépendante.

## 6. Importés sur décision explicite — exports de notes Vivino / wine.com

**Implémenté par `manage.py import_vivino`** (code : `apps/catalog/vivino_import.py`).

Ces quatre exports partagent une même nature — producteur, nom de vin, région,
note communautaire, prix — sous des en-têtes différents. Une table d'alias suffit
donc là où quatre lecteurs seraient redondants.

| Source | Lignes | Licence Kaggle | Particularité |
|---|---|---|---|
| [`fredericqiu/vivinoallwineexportfrance`](https://www.kaggle.com/datasets/fredericqiu/vivinoallwineexportfrance) | 30 018 | « Other » (non spécifiée) | `Name_domain` / `Product_name` |
| [`salohiddindev/wine-dataset-scraping-from-wine-com`](https://www.kaggle.com/datasets/salohiddindev/wine-dataset-scraping-from-wine-com) | 15 255 | Apache 2.0 | **UTF-16**, aucune colonne producteur |
| [`joshuakalobbowles/vivino-wine-data-top-10-countries-exchina`](https://www.kaggle.com/datasets/joshuakalobbowles/vivino-wine-data-top-10-countries-exchina) | 12 205 | CC BY 4.0 | `Winery` / `Wine`, porte un `Wine_ID` |
| [`mrbridge/vivino-wine-ratings-2026`](https://www.kaggle.com/datasets/mrbridge/vivino-wine-ratings-2026) | 10 344 | **CC BY-SA 4.0** | `winery_name` / `wine_name`, porte un `wine_id` |

Les notebooks `mrbridge/vivino-wine-ratings-2026-eda`,
`mrbridge/burgundy-wines-getting-started-2000` et
`mrbridge/bordeaux-wines-eda-2000-ratings-prices` **n'ajoutent pas de source** :
le premier expose le jeu `vivino-wine-ratings-2026` déjà listé, et les deux
autres sont, vérification faite, également dérivés de Vivino.

### L'écart assumé

Ces données ne satisfont pas le critère « origine licite » du §1 : elles
proviennent de Vivino ou de wine.com, dont les conditions d'utilisation
interdisent l'extraction — c'est précisément le motif pour lequel
`enrichment/stubs.py` garde `VivinoProvider` désactivé en permanence avec la
mention « ne pas implémenter ». Le dépôt affirme donc, à ce jour, une règle que
cet import enfreint. C'est une **décision du mainteneur**, prise en connaissance
de cause ; ce paragraphe existe pour qu'elle reste visible et réversible
(`SourceObservation` est append-only : une purge par canal est toujours possible).

Deux clauses méritent d'être gardées en tête :

- **`mrbridge/vivino-wine-ratings-2026` est en CC BY-SA 4.0.** Le partage à
  l'identique est *viral* : redistribuer un catalogue qui en incorpore les données
  peut obliger à placer l'ensemble sous la même licence. Rien ne l'impose tant que
  l'instance reste privée, mais la question se poserait pour une base publiée.
- **`fredericqiu` n'a pas de licence identifiée** (« Other, specified in
  description »).

[`budnyak/wine-rating-and-price`](https://www.kaggle.com/datasets/budnyak/wine-rating-and-price)
reste **exclu** (cf. §3) : sa licence CC BY-NC-**ND** interdit les œuvres
dérivées, et un import qui remappe les données dans notre schéma en est une.

### Ce qui rend l'import inoffensif pour le reste du référentiel

Le canal est préfixé `scrape:` — la commande le **refuse** sinon. Trois effets en
découlent, et ce sont eux qui bornent le risque :

1. **Confiance `0.40`** via `ingest._confiance_pour`, sous tous les autres canaux :
   pour l'identité et le profil, ces données ne peuvent que combler des trous.
2. **Étage inférieur sur les champs de marché.** Ce point a nécessité un
   changement de `consolidation._clef_marche`. La règle « le plus récent gagne »
   suppose des provenances comparables ; un import de scraping date *tous* ses
   relevés du jour et raflait donc prix et notes à un canal légitime, la confiance
   basse n'arbitrant qu'à égalité de date. Le scraping forme désormais un étage
   séparé : il n'alimente prix et notes que là où aucune source légitime ne s'est
   exprimée. Sans ce correctif, importer des jeux dont les notes sont l'essentiel
   du contenu aurait dégradé la fiche de chaque vin déjà enrichi par wineapi.
3. **Ligne sans producteur ni nom → ignorée**, faute de clé de déduplication.

### Résultat mesuré sur les fichiers réels

| Source | Lignes | Cuvées créées | Rattachées à une cuvée existante | Sans identité |
|---|---:|---:|---:|---:|
| `vivinoAllWineExportFrance.csv` | 30 018 | 20 270 | 2 152 | 1 |
| `vivino_top_ten.csv` | 12 205 | 7 435 | 3 859 | 0 |
| `vivino_wines_2026.csv` | 10 344 | 8 087 | 1 924 | 0 |
| `vivno_dataset.csv` (wine.com) | 15 254 | 5 297 | 531 | **9 306** |

**41 089 cuvées et 13 219 domaines** au total, en ~25 min sur SQLite. La colonne
« rattachées » montre la consolidation multi-sources à l'œuvre : 3 859 vins du
deuxième fichier désignaient des cuvées que le premier avait déjà créées.

**wine.com perd 61 % de ses lignes**, et c'est assumé : le fichier n'a pas de
colonne producteur, qu'il faut donc couper du libellé au niveau du cépage. Le
procédé échoue sur les assemblages et les noms propriétaires (« Opus One », dont
le descripteur annonce « Red Blend »). Sans producteur il n'existe aucune clé
`(domaine, nom_normalise)` : la ligne est écartée plutôt que de semer des
doublons dans un catalogue mutualisé.

### Particularités traitées

- **UTF-16.** Le fichier wine.com porte un BOM UTF-16 ; lu en UTF-8, il ne lève
  aucune erreur et produit des en-têtes truffés d'octets nuls. `tabular.encodage`
  tranche donc sur le BOM.
- **Producteur absent (wine.com).** Il est tiré du libellé complet, le cépage
  servant de séparateur (« 00 Wines VGW *Chardonnay* 2017 »).
- **En-tête `Countrys` trompeur (wine.com).** Il contient « <cépage> from
  <région> », pas un pays : il est lu comme tel et n'alimente pas `Cuvee.pays`.
- **Millésime collé au nom.** « Rosado de Lágrima 2020 », « Sweet White N.V. » :
  retirés, sans quoi chaque millésime créerait une cuvée alors que `Cuvee` en est
  indépendante. C'est aussi ce qui rend l'empreinte `(producteur, nom)` stable
  pour les sources dépourvues d'identifiant.
- **Note à `0.0`** chez wine.com signifie « pas encore notée », pas « nulle » :
  elle n'est pas affirmée.
