"""
update_cloud_db.py
==================
Actualiza datos_dgt_cloud.db con los datos más recientes de la DGT.
Usado por GitHub Actions: NO requiere datos_dgt.db local.

Estrategia:
  - Descarga la DB actual desde GitHub Releases (si no existe localmente)
  - Obtiene ficheros mensuales DGT de los últimos N meses
  - Reemplaza esos meses en resumen_marca y resumen_provincia
  - Guarda la DB actualizada (para que el Action la suba de vuelta)

Uso local:
    python update_cloud_db.py            # últimos 3 meses
    python update_cloud_db.py --meses 6  # últimos 6 meses
"""

import sqlite3
import sys
import requests
from datetime import date, datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from dgt_matriculaciones import (
    obtener_urls_diarias,
    obtener_urls_mensuales,
    _descargar_zip,
    parsear_fichero,
)

DST_PATH = Path(__file__).parent / "datos_dgt_cloud.db"
RELEASE_URL = (
    "https://github.com/literato1987/dgt-matriculaciones"
    "/releases/download/v1.0.0/datos_dgt_cloud.db"
)


def _descargar_db_si_falta():
    if DST_PATH.exists():
        print(f"DB existente: {DST_PATH} ({DST_PATH.stat().st_size / 1e6:.1f} MB)")
        return
    print("Descargando DB desde release...")
    with requests.get(RELEASE_URL, stream=True, timeout=300) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(DST_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    print(f"  {downloaded >> 20}/{total >> 20} MB", end="\r")
    print(f"\nDB descargada: {DST_PATH.stat().st_size / 1e6:.1f} MB")


def _meses_a_actualizar(n_meses: int) -> list:
    hoy = date.today()
    resultado = []
    for i in range(n_meses):
        mes = hoy.month - i
        anio = hoy.year
        while mes <= 0:
            mes += 12
            anio -= 1
        resultado.append((anio, mes))
    return resultado


def actualizar(n_meses: int = 3):
    _descargar_db_si_falta()

    conn = sqlite3.connect(str(DST_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; DGT-updater/1.0)"})

    print("Obteniendo indices de la DGT...")
    try:
        urls_diarias = obtener_urls_diarias()
        urls_mensuales = obtener_urls_mensuales()
    except Exception as e:
        print(f"ERROR al obtener indices DGT: {e}")
        conn.close()
        return False

    meses = _meses_a_actualizar(n_meses)
    print(f"Meses a actualizar: {[f'{a}-{m:02d}' for a, m in meses]}")

    total_filas = 0
    hoy = date.today()

    for anio, mes in meses:
        clave_mes = f"{anio}-{mes:02d}"
        es_mes_actual = (anio == hoy.year and mes == hoy.month)

        registros = []

        if (anio, mes) in urls_mensuales:
            print(f"  Mensual {clave_mes}...")
            try:
                contenido = _descargar_zip(session, urls_mensuales[(anio, mes)])
                if contenido:
                    registros = parsear_fichero(contenido)
                    print(f"    {len(registros):,} registros")
            except Exception as e:
                print(f"    ERROR en mensual {clave_mes}: {e}")

        if es_mes_actual and not registros:
            print(f"  Mes actual {clave_mes}: usando ficheros diarios...")
            dias_mes = [d for d in urls_diarias.keys()
                        if d.year == anio and d.month == mes]
            for d in sorted(dias_mes):
                try:
                    contenido = _descargar_zip(session, urls_diarias[d])
                    if contenido:
                        recs = parsear_fichero(contenido)
                        for r in recs:
                            r["FecMatricula"] = d.strftime("%Y%m%d")
                        registros.extend(recs)
                except Exception as e:
                    print(f"    ERROR diario {d}: {e}")
            print(f"    {len(registros):,} registros de {len(dias_mes)} dias")

        if not registros:
            print(f"  Sin datos para {clave_mes}, saltando.")
            continue

        # ── Agregar ──────────────────────────────────────────────────────────
        agg_marca = defaultdict(int)
        agg_prov  = defaultdict(int)

        for r in registros:
            fec = r.get("_fecha")
            if fec is None:
                continue
            mes_r = fec.strftime("%Y-%m")
            if mes_r != clave_mes:
                continue

            marca  = (r.get("MarcaItv")         or "").strip()
            modelo = (r.get("ModeloItv")         or "").strip()
            ctipo  = (r.get("CodTipo")           or "").strip()
            cprop  = (r.get("CodPropulsionItv")  or "").strip()
            nuevo  = (r.get("IndNuevoUsado")     or "").strip()
            cprov  = (r.get("CodProvinciaMat")   or "").strip()

            agg_marca[(clave_mes, marca, modelo, ctipo, cprop, nuevo)] += 1
            agg_prov[ (clave_mes, cprop, ctipo, nuevo, cprov)]          += 1

        # ── Reemplazar en DB ─────────────────────────────────────────────────
        conn.execute("DELETE FROM resumen_marca     WHERE mes = ?", (clave_mes,))
        conn.execute("DELETE FROM resumen_provincia WHERE mes = ?", (clave_mes,))

        conn.executemany(
            "INSERT INTO resumen_marca VALUES (?,?,?,?,?,?,?)",
            [(k[0], k[1], k[2], k[3], k[4], k[5], v) for k, v in agg_marca.items()],
        )
        conn.executemany(
            "INSERT INTO resumen_provincia VALUES (?,?,?,?,?,?)",
            [(k[0], k[1], k[2], k[3], k[4], v) for k, v in agg_prov.items()],
        )

        total_filas += len(agg_marca)
        print(f"  OK {clave_mes}: {len(agg_marca):,} filas marca | {len(agg_prov):,} filas provincia")

    # Actualizar meta
    fecha_min, fecha_max = conn.execute(
        "SELECT MIN(mes), MAX(mes) FROM resumen_marca"
    ).fetchone()
    conn.execute("UPDATE meta SET value=? WHERE key='built_at'",
                 (datetime.now().isoformat(),))
    conn.execute("UPDATE meta SET value=? WHERE key='fecha_min'", (fecha_min,))
    conn.execute("UPDATE meta SET value=? WHERE key='fecha_max'", (fecha_max,))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    dst_mb = DST_PATH.stat().st_size / 1e6
    print(f"\nActualizado: {DST_PATH} ({dst_mb:.1f} MB)")
    print(f"Rango: {fecha_min} -> {fecha_max}")
    return total_filas > 0


if __name__ == "__main__":
    n = 3
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--meses" and i + 1 < len(sys.argv) - 1:
            n = int(sys.argv[i + 2])
    hay_cambios = actualizar(n_meses=n)
    # exit 2 = sin cambios nuevos (GitHub Action puede saltarse el upload)
    sys.exit(0 if hay_cambios else 2)
