-- One row per (snapshot day, station, fuel). Types and English names start here.
select
    cast(regexp_extract(filename, '(\d{4}-\d{2}-\d{2})', 1) as date) as snapshot_date,
    pdv_id as station_id,
    cast(prix_id as integer) as fuel_id,
    prix_nom as fuel_name,
    cast(prix_maj as timestamp) as price_updated_at,
    cast(prix_valeur as double) as price_eur_per_liter
from {{ source('raw', 'prices') }}
