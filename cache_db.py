"""
cache_db.py
===========
Capa de persistencia SQLite para datos de matriculaciones DGT.

Evita re-descargar datos ya obtenidos y preserva el histórico aunque
la DGT retire los ficheros de su web.

Tablas:
  registros      — un registro por vehículo matriculado
  descargas_log  — qué días/meses ya están en la DB
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path
import pandas as pd

RUTA_DB_DEFAULT = Path(__file__).parent / "datos_dgt.db"

DDL = """
CREATE TABLE IF NOT EXISTS registros (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha           TEXT NOT NULL,       -- YYYY-MM-DD
    marca           TEXT,
    modelo          TEXT,
    cod_tipo        TEXT,
    cod_propulsion  TEXT,
    ind_nuevo_usado TEXT,
    cod_provincia   TEXT
);

-- Índice compuesto para el patrón de consulta más frecuente
CREATE INDEX IF NOT EXISTS idx_main ON registros(fecha, cod_propulsion, ind_nuevo_usado, cod_tipo);
-- Índice para consultas de evolución por marca/modelo
CREATE INDEX IF NOT EXISTS idx_marca_modelo ON registros(marca, modelo, fecha);

CREATE TABLE IF NOT EXISTS descargas_log (
    tipo        TEXT NOT NULL,   -- 'dia' | 'mes'
    clave       TEXT NOT NULL,   -- 'YYYYMMDD' | 'YYYYMM'
    n_registros INTEGER,
    ts          TEXT,            -- timestamp de descarga
    PRIMARY KEY (tipo, clave)
);
"""


def inicializar_db(ruta=None) -> sqlite3.Connection:
    """Abre (o crea) la base de datos y aplica el esquema si no existe."""
    ruta = Path(ruta) if ruta else RUTA_DB_DEFAULT
    conn = sqlite3.connect(str(ruta), check_same_thread=False)
    # WAL mode: lecturas no bloquean escrituras, mejora rendimiento general
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-65536")   # 64 MB de caché en memoria
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA synchronous=NORMAL")  # más rápido que FULL, seguro con WAL
    conn.executescript(DDL)
    conn.commit()
    return conn


def dias_ya_descargados(conn: sqlite3.Connection) -> set:
    """Devuelve un set de objetos date con los días ya almacenados."""
    cur = conn.execute("SELECT clave FROM descargas_log WHERE tipo = 'dia'")
    return {date(int(r[0][:4]), int(r[0][4:6]), int(r[0][6:])) for r in cur}


def meses_ya_descargados(conn: sqlite3.Connection) -> set:
    """Devuelve un set de (año, mes) ya almacenados desde ficheros mensuales."""
    cur = conn.execute("SELECT clave FROM descargas_log WHERE tipo = 'mes'")
    return {(int(r[0][:4]), int(r[0][4:])) for r in cur}


def guardar_registros(conn: sqlite3.Connection, records: list, tipo: str, clave: str):
    """
    Inserta una lista de dicts (salida de parsear_fichero) en la tabla registros
    y registra la descarga en descargas_log.

    tipo  : 'dia' o 'mes'
    clave : 'YYYYMMDD' o 'YYYYMM'
    """
    if not records:
        return

    filas = []
    for r in records:
        # _fecha es un objeto date puesto por parsear_fichero (maneja YYYYMMDD y DDMMYYYY)
        _fecha = r.get("_fecha")
        fecha_iso = _fecha.isoformat() if _fecha else ""

        filas.append((
            fecha_iso,
            r.get("MarcaItv", ""),
            r.get("ModeloItv", ""),
            r.get("CodTipo", ""),
            r.get("CodPropulsionItv", ""),
            r.get("IndNuevoUsado", ""),
            r.get("CodProvinciaMat", ""),
        ))

    conn.executemany(
        """INSERT INTO registros
           (fecha, marca, modelo, cod_tipo, cod_propulsion, ind_nuevo_usado, cod_provincia)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        filas,
    )
    conn.execute(
        """INSERT OR REPLACE INTO descargas_log (tipo, clave, n_registros, ts)
           VALUES (?, ?, ?, ?)""",
        (tipo, clave, len(records), datetime.now().isoformat()),
    )
    conn.commit()


def query_registros(
    conn: sqlite3.Connection,
    fecha_inicio: date,
    fecha_fin: date,
    cod_propulsion: str = None,
    solo_nuevos: bool = True,
    cod_tipo_list: list = None,
) -> pd.DataFrame:
    """
    Consulta registros de la DB para el rango de fechas dado.
    Todos los filtros se aplican en SQL para minimizar datos cargados en memoria.
    """
    sql = """
        SELECT fecha, marca, modelo, cod_tipo, cod_propulsion, ind_nuevo_usado, cod_provincia
        FROM registros
        WHERE fecha >= ? AND fecha <= ?
    """
    params = [fecha_inicio.isoformat(), fecha_fin.isoformat()]

    if cod_propulsion:
        sql += " AND cod_propulsion = ?"
        params.append(cod_propulsion)

    if solo_nuevos:
        sql += " AND ind_nuevo_usado = 'N'"

    if cod_tipo_list:
        sql += f" AND cod_tipo IN ({','.join('?' * len(cod_tipo_list))})"
        params.extend(cod_tipo_list)

    df = pd.read_sql_query(sql, conn, params=params)
    df.rename(columns={
        "fecha":           "_fecha",
        "marca":           "MarcaItv",
        "modelo":          "ModeloItv",
        "cod_tipo":        "CodTipo",
        "cod_propulsion":  "CodPropulsionItv",
        "ind_nuevo_usado": "IndNuevoUsado",
        "cod_provincia":   "CodProvinciaMat",
    }, inplace=True)
    return df


def query_stats_propulsion(
    conn: sqlite3.Connection,
    fecha_inicio: date,
    fecha_fin: date,
    solo_nuevos: bool = True,
) -> dict:
    """
    Devuelve {cod_propulsion: count} para el rango dado.
    Mucho más ligero que cargar todos los registros en memoria.
    """
    sql = "SELECT cod_propulsion, COUNT(*) FROM registros WHERE fecha >= ? AND fecha <= ?"
    params = [fecha_inicio.isoformat(), fecha_fin.isoformat()]
    if solo_nuevos:
        sql += " AND ind_nuevo_usado = 'N'"
    sql += " GROUP BY cod_propulsion"
    return {r[0]: r[1] for r in conn.execute(sql, params)}


def n_registros_total(conn: sqlite3.Connection) -> int:
    """Devuelve el total de registros almacenados en la DB."""
    cur = conn.execute("SELECT COUNT(*) FROM registros")
    return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# DB CLOUD (tablas agregadas por mes)
# ─────────────────────────────────────────────────────────────────────────────

RUTA_CLOUD_DEFAULT = Path(__file__).parent / "datos_dgt_cloud.db"


def is_cloud_db(conn: sqlite3.Connection) -> bool:
    """True si la conexión apunta a la DB cloud (tiene tabla resumen_marca)."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='resumen_marca'"
    )
    return cur.fetchone() is not None


def inicializar_cloud_db(ruta=None) -> sqlite3.Connection:
    """Abre la DB cloud en modo lectura optimizado."""
    ruta = Path(ruta) if ruta else RUTA_CLOUD_DEFAULT
    conn = sqlite3.connect(str(ruta), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-32768")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
