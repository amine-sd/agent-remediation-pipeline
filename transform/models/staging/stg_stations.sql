-- One row per (snapshot day, station). Coordinates come as integers scaled by 100 000.
select
    cast(regexp_extract(filename, '(\d{4}-\d{2}-\d{2})', 1) as date) as snapshot_date,
    pdv_id as station_id,
    cast(latitude as double) / 100000 as latitude,
    cast(longitude as double) / 100000 as longitude,
    cp as postal_code,
    case
        when cp like '97%' then left(cp, 3)
        when cp like '20%' then case when cp < '20200' then '2A' else '2B' end
        else left(cp, 2)
    end as department_code,
    pop as location_type,
    adresse as address,
    ville as city,
    coalesce(automate_24_24 = '1', false) as has_24h_automat,
    services
from {{ source('raw', 'stations') }}
