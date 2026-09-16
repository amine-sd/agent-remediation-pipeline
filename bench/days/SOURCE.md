# Données embarquées pour le banc d'essai

Ces quatre archives sont les flux quotidiens des prix des carburants en France des 10, 11, 12 et
13 septembre 2026, telles que la source les publiait. Le banc d'essai rejoue ses scénarios sur ces
jours-là, et la source ne garde ses archives quotidiennes qu'une trentaine de jours : elles sont donc
conservées ici, sans modification.

- Source : flux quotidien « Prix des carburants en France », https://donnees.roulez-eco.fr/opendata/jour
- Producteur : DGCCRF, système d'information « Prix Carburants »
- Licence : Licence Ouverte Etalab
- Fichiers : les archives d'origine, une par jour (`AAAA-MM-JJ.zip`, un fichier XML chacune)

Pour reconstruire `data/raw/` et l'entrepôt à partir de ces archives, sans réseau :

    python -m bench.bootstrap
