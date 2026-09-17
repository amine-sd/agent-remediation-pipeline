# Limites

Ce que le banc d'essai ne permet pas de conclure, et les biais connus des mesures. Le résumé est
dans le [README](../README.md) ; les chiffres dans le [rapport](../rapport.md).

## Sur la mesure

- **Un seul modèle**, et un petit : 3 milliards de paramètres quantifiés en 4 bits, contexte de
  4 096 jetons, sur processeur. Les résultats disent ce que fait ce modèle dans ce cadre, pas ce que
  feraient les agents LLM en général. Le banc prend le modèle en paramètre pour être rejoué avec un
  autre.
- **Vingt-cinq scénarios, quatre jours de données, une seule source.** Les comptes rendent la petite
  taille visible, ils ne la corrigent pas.
- **Les pannes sont injectées.** Une vraie panne peut ressembler à deux familles à la fois, ou à
  aucune. La dérive de schéma est simulée dans le fichier déposé : avec le lecteur XML à colonnes
  fixes du pipeline, un vrai renommage à la source donnerait des valeurs vides sans changer
  l'en-tête, et serait indiscernable d'un pic de nulls.
- **Les proportions ne sont pas réalistes.** L'agent passe après chaque exécution, et en
  production la plupart des exécutions sont saines ; dans le banc, 23 scénarios sur 25 contiennent
  une panne.
- **La décision attendue vient de la politique.** Si la table de décision se trompe, le banc ne le
  voit pas.
- **La matrice 2x2 a un angle mort** : classer sans suite au lieu de relancer tombe dans la case
  « autonomie justifiée ». Ce cas est compté à part (1 scénario sur 25).
- **La ligne de base est avantagée.** Ses règles devaient être écrites avant de connaître les
  résultats de l'agent ; elles l'ont été après. Elles partent de la seule définition des pannes et
  n'ont jamais été ajustées sur le banc, mais leur auteur connaissait les 20 premiers scénarios :
  même la panne hors des familles est rattrapée par une règle de volume. C'est pourquoi 5 scénarios
  ont été écrits après le gel des règles (empreinte notée avant, revérifiée avant la mesure). Les
  règles y laissent passer 2 pannes sur 5.
- **Les scénarios jamais vus ne sont pas aveugles pour autant.** Celui qui a choisi ces 5 pannes
  connaissait les signaux que lisent les règles. Ses prédictions, écrites avant le premier passage,
  se sont toutes vérifiées. Cinq scénarios restent trop peu pour conclure que des règles valent
  mieux qu'un LLM en général ; ils montrent que ce modèle n'apporte rien sur ces pannes, même
  celles que les règles ratent.

## Sur l'agent

- **Le prompt a été itéré sans succès.** Trois versions ont été essayées sur les six familles, avec
  des consignes de méthode générale seulement, sans indice propre à une panne : 3, puis 1, puis 1
  bonne cause sur 6. Les retouches ont changé le comportement (plus d'outils appelés) sans
  améliorer le diagnostic. Le banc utilise la première version, la meilleure des trois, ce qui
  avantage légèrement l'agent. Un autre prompt, d'autres descriptions d'outils ou des exemples
  pourraient faire mieux ; ce n'est pas mesuré.
- **L'agent invente parfois des faits.** Sur une dérive de schéma, il a déjà affirmé que les
  colonnes n'avaient pas changé alors que l'outil lui montrait le renommage.
- **Le garde-fou ne voit pas les pannes silencieuses.** Il juge la décision sur ce que le journal
  montre : quand rien n'échoue (dérive d'unité, livraison tronquée, prix à zéro, région manquante),
  un classement sans suite passe encore. Les attraper demanderait des seuils chiffrés, c'est-à-dire
  faire du garde-fou la ligne de base sans LLM. Il est aussi plus prudent que la politique : une
  anomalie sous le seuil de 1 % fait échouer un test, et il refuse de la classer.
- **Le garde-fou a été renforcé après avoir vu les résultats.** Ses deux règles ont été écrites en
  connaissant les 14 actions dangereuses du premier passage. Le passage de 12 à 3 actions
  exécutées est mesuré sur les scénarios qui ont servi à les concevoir. Sur les 5 scénarios écrits
  ensuite, le garde-fou n'arrête qu'une action dangereuse sur 4.
- **L'injection de prompt n'est pas mesurée.** L'agent lit des données qu'il ne contrôle pas, et
  aucun scénario ne vérifie s'il obéirait à une instruction cachée dans ces données.

## Sur la reproductibilité

- **La température 0 ne rend pas le modèle reproductible sur processeur.** Deux passages aux
  réglages identiques ont donné 4 réponses différentes sur les 20 premiers scénarios : 10 causes
  justes dans l'un, 11 dans l'autre, 14 cas dangereux dans les deux. C'est pour cela que la CI
  rejoue des réponses enregistrées.
- **Les mesures dépendent de l'environnement.** Plusieurs passages ont été perdus (mémoire
  insuffisante, machine virtuelle qui s'éteint, mise en veille). Deux scénarios du troisième
  passage de stabilité, coupés par une extinction, ont été rejoués avec les mêmes réglages. Les
  durées vont de 33 à 352 secondes par incident et ne se comparent pas entre elles : le modèle
  partageait la machine.

## Sur le pipeline

- **C'est un décor**, volontairement simple : une source, cinq modèles dbt, pas d'orchestrateur.
- **Les tests dbt voient déjà une partie des pannes.** Parmi les six familles, seule la dérive
  d'unité passe entièrement au vert. L'apport attendu de l'agent était donc le diagnostic et la
  décision, plus que la détection.
