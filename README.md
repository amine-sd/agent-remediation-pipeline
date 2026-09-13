# Agent de remédiation d'un pipeline de données

> Détecter un incident est une chose, décider qu'on a le droit d'y toucher tout seul en est une
> autre.

Ce projet construit un agent qui diagnostique les pannes d'un pipeline de données, et surtout un
banc d'essai qui mesure **quand il a eu raison d'agir seul**.

> **Statut : en construction.** Aucun résultat n'est encore publié. Le tableau de résultats
> apparaîtra ici quand le banc d'essai tournera.

## Le principe

Un petit pipeline ingère chaque jour les prix des carburants en France (flux quotidien publié par
la DGCCRF sur data.economie.gouv.fr, Licence Ouverte Etalab), les transforme avec dbt et les
stocke dans DuckDB.

Un injecteur y provoque volontairement des pannes. On sait donc exactement ce qui a été cassé :
la vérité terrain est exacte, sans annotation.

Un agent, servi en local par Ollama, enquête avec six outils (lire les journaux, requêter
l'entrepôt, lire le lineage dbt, comparer à l'exécution précédente, relancer une étape, ouvrir un
ticket) et rend un verdict : la cause racine, sa justification, et une décision, **agir** seul ou
**escalader** vers un humain.

## Les six familles de pannes

| # | Famille | Ce qui se passe |
|---|---|---|
| 1 | Dérive de schéma | Une colonne est renommée en amont |
| 2 | Qualité | Pic de valeurs nulles sur une colonne clé |
| 3 | Idempotence | Doublons après le rejeu d'une étape |
| 4 | Fraîcheur | Le fichier du jour n'arrive pas |
| 5 | Dérive silencieuse | Une unité change sans que rien ne casse |
| 6 | Panne dure | La source renvoie une erreur 500 |

Les cinq premières sont les plus intéressantes : elles ne font pas forcément planter le pipeline,
elles le font mentir.

## Ce qui est mesuré

| Mesure | Question |
|---|---|
| Cause racine correcte | L'agent a-t-il trouvé la bonne famille de panne ? |
| Matrice de décision 2x2 | Agir ou escalader, croisé avec ce qu'il fallait faire |
| Coût | Combien d'appels au modèle et de jetons par incident ? |
| Stabilité | Le même scénario rejoué donne-t-il la même décision ? |

La case qui compte le plus est **« l'agent a agi alors qu'il aurait dû escalader »**. Elle sera
rapportée seule, en évidence, même à zéro.

## Pile technique

Python, dbt-core et dbt-duckdb, DuckDB en fichier local, Ollama pour le modèle, pytest et des
scénarios YAML pour le banc d'essai. Pas d'orchestrateur, pas de framework d'agents : l'appel
d'outils est écrit à la main.
