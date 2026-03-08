"""
build_cloud_db.py
=================
Genera datos_dgt_cloud.db desde datos_dgt.db local.
La DB cloud (~80 MB) puede subirse directamente a GitHub (< 100 MB).

Por defecto incluye datos desde 2021-01 para mantener el tamaño manejable.

Dos tablas agregadas por mes:
  resumen_marca      -> Tabs Ranking y Evolución temporal
  resumen_provincia  -> Tabs BEV Share y Comunidades

Uso:
    python build_cloud_db.py
    python build_cloud_db.py --desde 2019-01   # ampliar rango
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SRC_PATH = Path(__file__).parent / "datos_dgt.db"
DST_PATH = Path(__file__).parent / "datos_dgt_cloud.db"
FECHA_MIN_DEFAULT = "2021-01"  # ~80 MB; usar --desde 2017-01 para histórico completo

_DDL = """
CREATE TABLE resumen_marca (
    mes             TEXT NOT NULL,
    marca           TEXT NOT NULL DEFAULT '',
    modelo          TEXT NOT NULL DEFAULT '',
    cod_tipo        TEXT NOT NULL DEFAULT '',
    cod_propulsion  TEXT NOT NULL DEFAULT '',
    ind_nuevo_usado TEXT NOT NULL DEFAULT '',
    n               INTEGER NOT NULL
);
CREATE INDEX idx_rm_periodo ON resumen_marca(mes, cod_propulsion, ind_nuevo_usado, cod_tipo);
CREATE INDEX idx_rm_marca   ON resumen_marca(marca, modelo, mes);

CREATE TABLE resumen_provincia (
    mes             TEXT NOT NULL,
    cod_propulsion  TEXT NOT NULL DEFAULT '',
    cod_tipo        TEXT NOT NULL DEFAULT '',
    ind_nuevo_usado TEXT NOT NULL DEFAULT '',
    cod_provincia   TEXT NOT NULL DEFAULT '',
    n               INTEGER NOT NULL
);
CREATE INDEX idx_rp_periodo ON resumen_provincia(mes, cod_propulsion, ind_nuevo_usado);
CREATE INDEX idx_rp_prov    ON resumen_provincia(cod_provincia, mes, cod_propulsion);

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def build(src_path=SRC_PATH, dst_path=DST_PATH, fecha_min=FECHA_MIN_DEFAULT):
    if not src_path.exists():
        print(f"ERROR: no se encuentra {src_path}")
        return

    src_mb = src_path.stat().st_size / 1e6
    print(f"Leyendo  : {src_path} ({src_mb:.0f} MB)")
    print(f"Destino  : {dst_path}")
    print(f"Desde    : {fecha_min}")

    if dst_path.exists():
        dst_path.unlink()

    conn = sqlite3.connect(str(dst_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    conn.executescript(_DDL)

    conn.execute(f"ATTACH DATABASE '{src_path}' AS src")

    print("Construyendo resumen_marca ...")
    conn.execute(f"""
        INSERT INTO resumen_marca
        SELECT
            strftime('%Y-%m', fecha),
            COALESCE(NULLIF(marca,        ''), ''),
            COALESCE(NULLIF(modelo,       ''), ''),
            COALESCE(NULLIF(cod_tipo,     ''), ''),
            COALESCE(NULLIF(cod_propulsion,''),''),
            COALESCE(NULLIF(ind_nuevo_usado,''),''),
            COUNT(*)
        FROM src.registros
        WHERE fecha != '' AND strftime('%Y-%m', fecha) >= '{fecha_min}'
        GROUP BY 1, 2, 3, 4, 5, 6
    """)
    n_rm = conn.execute("SELECT COUNT(*) FROM resumen_marca").fetchone()[0]
    print(f"  {n_rm:,} filas")

    print("Construyendo resumen_provincia ...")
    conn.execute(f"""
        INSERT INTO resumen_provincia
        SELECT
            strftime('%Y-%m', fecha),
            COALESCE(NULLIF(cod_propulsion, ''), ''),
            COALESCE(NULLIF(cod_tipo,       ''), ''),
            COALESCE(NULLIF(ind_nuevo_usado,''), ''),
            COALESCE(NULLIF(cod_provincia,  ''), ''),
            COUNT(*)
        FROM src.registros
        WHERE fecha != '' AND strftime('%Y-%m', fecha) >= '{fecha_min}'
        GROUP BY 1, 2, 3, 4, 5
    """)
    n_rp = conn.execute("SELECT COUNT(*) FROM resumen_provincia").fetchone()[0]
    print(f"  {n_rp:,} filas")

    fecha_min, fecha_max = conn.execute(
        "SELECT MIN(mes), MAX(mes) FROM resumen_marca"
    ).fetchone()

    conn.executemany("INSERT INTO meta VALUES (?,?)", [
        ("built_at",   datetime.now().isoformat()),
        ("fecha_min",  fecha_min),
        ("fecha_max",  fecha_max),
    ])

    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.execute("VACUUM")
    conn.close()

    dst_mb = dst_path.stat().st_size / 1e6
    print(f"\nGenerado : {dst_path} ({dst_mb:.1f} MB)")
    print(f"Rango    : {fecha_min} -> {fecha_max}")


if __name__ == "__main__":
    fm = FECHA_MIN_DEFAULT
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--desde" and i + 1 < len(sys.argv) - 1:
            fm = sys.argv[i + 2]
    build(fecha_min=fm)
