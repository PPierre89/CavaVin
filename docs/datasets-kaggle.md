# Veille jeux de données — remplir le référentiel hors ligne

> **Objet** : quels jeux de données publics (Kaggle en particulier) peuvent
> *remplir* le référentiel `Cuvee` sans clé d'API ni quota, et à quelles
> conditions de licence. Complète `docs/architecture-referentiel.md` (le socle) et
> `CLAUDE.md` (les conventions).
>
> **Verdict** : deux jeux retenus, à des titres très différents. **X-Wines**
> (§2, `manage.py import_xwines`) est un référentiel à part entière et entre en
> canal ordinaire. Le **catalogue marchand `elvinrustam`** (§5,
> `manage.py import_catalogue_marchand`) est retenu *sous réserve*, en canal de
> **scraping** à confiance basse, pour la seule appellation. Tous les autres sont
> écartés, et le §3 dit pourquoi : dans la quasi-totalité des cas la donnée est du
> *scraping* de sites marchands ou communautaires republié sous une licence non
> commerciale, ce que ce dépôt refuse par ailleurs (stubs Vivino et CellarTracker).

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
