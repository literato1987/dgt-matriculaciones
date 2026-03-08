"""
dgt_matriculaciones.py
======================
Descarga y analiza los microdatos de matriculaciones de la DGT.
Genera gráficas de barras por MARCA para un rango de fechas dado.

Fuentes DGT:
  - Diarios (últimos ~30 días):
    https://www.dgt.es/.../matriculaciones-automoviles-diario.html
  - Mensuales (desde dic. 2014):
    https://www.dgt.es/.../matriculaciones-automoviles-mensual.html

El script elige automáticamente la fuente correcta según el rango pedido:
  • Fechas dentro de los últimos 30 días  → ficheros diarios
  • Fechas más antiguas                   → ficheros mensuales (filtrando por fecha)
  • Rangos que cruzan ambos períodos      → combina ambas fuentes

Uso:
    python dgt_matriculaciones.py --inicio 2026-03-01 --fin 2026-03-08
    python dgt_matriculaciones.py --inicio 2026-01-01 --fin 2026-01-31
    python dgt_matriculaciones.py --inicio 2025-01-01 --fin 2025-12-31 --top 10
    python dgt_matriculaciones.py --inicio 2026-03-01 --fin 2026-03-08 --tipo turismo
    python dgt_matriculaciones.py --inicio 2026-03-01 --fin 2026-03-08 --guardar resultados.xlsx

Requisitos:
    pip install requests matplotlib pandas openpyxl
"""

import requests
import zipfile
import io
import re
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from datetime import datetime, timedelta, date
import argparse
import sys

# ─────────────────────────────────────────────────────────────────────────────
# DEFINICIÓN DE CAMPOS (formato longitud fija)
# ─────────────────────────────────────────────────────────────────────────────
CAMPOS = [
    ("FecMatricula",           8),
    ("CodClaseMat",            1),
    ("FecTramitacion",         8),
    ("MarcaItv",              30),   # ← MARCA
    ("ModeloItv",             22),
    ("CodProcedenciaItv",      1),
    ("BastidorItv",           21),
    ("CodTipo",                2),   # Tipo vehículo
    ("CodPropulsionItv",       1),   # Propulsión
    ("CilindradaItv",          5),
    ("PotenciaItv",            6),
    ("Tara",                   6),
    ("PesoMax",                6),
    ("NumPlazasItv",           3),
    ("IndPrecinto",            2),
    ("IndEmbargo",             2),
    ("NumTransmisiones",       2),
    ("NumTitulares",           2),
    ("LocalidadVehiculo",     24),
    ("CodProvinciaVeh",        2),
    ("CodProvinciaMat",        2),   # Provincia matriculación
    ("ClaveTramite",           1),
    ("FecTramite",             8),
    ("CodigoPostal",           5),
    ("FecPrimMatriculacion",   8),
    ("IndNuevoUsado",          1),   # N=Nuevo, U=Usado
    ("PersonaFisicaJuridica",  1),
    ("CodigoItv",              9),
    ("Servicio",               3),
    ("CodMunicipioIneVeh",     5),
    ("Municipio",             30),
    ("KwItv",                  7),
    ("NumPlazasMax",           3),
    ("Co2Itv",                 5),
    ("Renting",                1),
    ("CodTutela",              1),
    ("CodPosesion",            1),
    ("IndBajaDef",             1),
    ("IndBajaTemp",            1),
    ("IndSustraccion",         1),
    ("BajaTelematica",        11),
    ("TipoItv",               25),
    ("VarianteItv",           25),
    ("VersionItv",            35),
    ("FabricanteItv",         70),
    ("MasaOrdenMarchaItv",     6),
    ("MasaMaxTecAdmisible",    6),
    ("CatHomologacionEU",      4),
    ("Carroceria",             4),
    ("PlazasPie",              3),
    ("NivelEmisionesEuro",     8),
    ("ConsumoWhKm",            4),
    ("ClasifReglamento",       4),
    ("CatVehElectrico",        4),
    ("AutonomiaElectrico",     6),
    ("MarcaVehBase",          30),
    ("FabricanteVehBase",     50),
    ("TipoVehBase",           35),
    ("VarianteVehBase",       25),
    ("VersionVehBase",        35),
    ("DistanciaEjes12",        4),
    ("ViaAnterior",            4),
    ("ViaPosterior",           4),
    ("TipoAlimentacion",       1),
    ("ContraseñaHomologacion", 25),
    ("EcoInnovacion",          1),
    ("ReduccionEco",           4),
    ("CodigoEco",             25),
    ("FecProceso",             8),
]

# Posiciones de inicio de cada campo
_pos = 0
OFFSETS = {}
for _nombre, _longitud in CAMPOS:
    OFFSETS[_nombre] = (_pos, _longitud)
    _pos += _longitud

# Códigos de tipo de vehículo
TIPOS_VEHICULO = {
    # Códigos reales confirmados desde los ficheros DGT
    "40": "Turismo",
    "25": "SUV / Todoterreno",
    "50": "Motocicleta",
    "90": "Ciclomotor",
    "20": "Furgoneta / Comercial",
    "0G": "Furgoneta derivada turismo",
    "81": "Camión",
    "30": "Autobús / Autocar",
    "7A": "Autocaravana",
    "80": "Tractor agrícola",
    "70": "Maquinaria industrial",
    "73": "Carretilla elevadora",
    "S2": "Semirremolque",
    "S7": "Remolque frigorífico",
    "RH": "Remolque agrícola",
}

# Códigos de propulsión
# La DGT usa códigos numéricos en los ficheros actuales (confirmado con Tesla = "2")
PROPULSION = {
    # Códigos numéricos (formato actual DGT)
    "0": "Gasolina",
    "1": "Diésel",
    "2": "Eléctrico",
    "3": "Híbrido enchufable",
    "6": "Híbrido",
    "7": "GLP",
    "8": "Gas natural",
    "9": "Hidrógeno",
    # Códigos letra (formato antiguo DGT, por compatibilidad)
    "G": "Gasolina", "D": "Diésel",   "E": "Eléctrico",
    "H": "Híbrido",  "I": "Híbrido enchufable",
    "L": "GLP",      "N": "Gas natural", "B": "Bicombustible",
    "R": "Hidrógeno", "X": "Otros",
}

# ─────────────────────────────────────────────────────────────────────────────
# DESCARGA DE ÍNDICES DE URLS
# ─────────────────────────────────────────────────────────────────────────────

URL_DIARIO   = ("https://www.dgt.es/menusecundario/dgt-en-cifras/matraba-listados/"
                "matriculaciones-automoviles-diario.html?nocache=1")
URL_MENSUAL  = ("https://www.dgt.es/menusecundario/dgt-en-cifras/matraba-listados/"
                "matriculaciones-automoviles-mensual.html")

def obtener_urls_diarias():
    """Devuelve dict {date: url} con los ficheros diarios disponibles."""
    resp = requests.get(URL_DIARIO, timeout=30)
    resp.raise_for_status()
    resultado = {}
    for m in re.finditer(
        r'(https://www\.dgt\.es/microdatos/salida/\d+/\d+/vehiculos/'
        r'matriculaciones/export_mat_(\d{8})\.zip)',
        resp.text
    ):
        url, fecha_str = m.group(1), m.group(2)
        resultado[datetime.strptime(fecha_str, "%Y%m%d").date()] = url
    return resultado

def obtener_urls_mensuales():
    """Devuelve dict {(año, mes): url} con los ficheros mensuales disponibles."""
    resp = requests.get(URL_MENSUAL, timeout=30)
    resp.raise_for_status()
    resultado = {}
    for m in re.finditer(
        r'(https://www\.dgt\.es/microdatos/salida/\d+/\d+/vehiculos/'
        r'matriculaciones/export_mensual_mat_(\d{6})\.zip)',
        resp.text
    ):
        url, ym_str = m.group(1), m.group(2)
        anio, mes = int(ym_str[:4]), int(ym_str[4:])
        resultado[(anio, mes)] = url
    return resultado

# ─────────────────────────────────────────────────────────────────────────────
# PARSEAR FICHERO DE LONGITUD FIJA
# ─────────────────────────────────────────────────────────────────────────────

CAMPOS_INTERES = ["MarcaItv", "ModeloItv", "CodTipo", "CodPropulsionItv",
                   "IndNuevoUsado", "CodProvinciaMat", "FecMatricula"]

def parsear_fichero(contenido_bytes, filtro_fechas=None):
    """
    Parsea el fichero de longitud fija.
    filtro_fechas: set de objetos date; si se indica, solo se devuelven esos días.
    """
    try:
        texto = contenido_bytes.decode("latin-1")
    except Exception:
        texto = contenido_bytes.decode("utf-8", errors="replace")

    lineas = texto.splitlines()
    if lineas and not lineas[0][:8].strip().isdigit():
        lineas = lineas[1:]  # saltar cabecera

    registros = []
    for linea in lineas:
        if len(linea) < 20:
            continue
        rec = {}
        for campo in CAMPOS_INTERES:
            inicio, longitud = OFFSETS[campo]
            rec[campo] = linea[inicio:inicio + longitud].strip()

        # Parsear fecha de matriculación para filtrar
        # Los ficheros diarios usan YYYYMMDD; los mensuales usan DDMMYYYY
        fec = None
        fec_str = rec.get("FecMatricula", "")
        if fec_str:
            for fmt in ("%Y%m%d", "%d%m%Y"):
                try:
                    fec = datetime.strptime(fec_str, fmt).date()
                    break
                except ValueError:
                    pass

        if filtro_fechas:
            if fec is None or fec not in filtro_fechas:
                continue
        rec["_fecha"] = fec

        registros.append(rec)
    return registros

def _descargar_zip(session, url):
    """Descarga un ZIP y devuelve el contenido del primer fichero de datos."""
    resp = session.get(url, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for nombre in zf.namelist():
            if not nombre.endswith("/"):
                return zf.read(nombre)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# DESCARGA Y PROCESADO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def descargar_y_procesar(fecha_inicio, fecha_fin,
                          filtro_tipo=None, filtro_propulsion=None,
                          solo_nuevos=True):
    """
    Descarga los datos para el rango de fechas usando la fuente correcta:
      - Diaria  → si los días están disponibles en el índice diario
      - Mensual → para el resto (filtrando por FecMatricula dentro del mes)
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; DGT-stats/1.0)"
    })

    dias_rango = set()
    d = fecha_inicio
    while d <= fecha_fin:
        dias_rango.add(d)
        d += timedelta(days=1)

    # ── Índice de ficheros disponibles ──────────────────────────────────────
    print("📡 Consultando índices de la DGT...")
    urls_diarias  = obtener_urls_diarias()
    urls_mensuales = obtener_urls_mensuales()
    print(f"   Diarios disponibles : {len(urls_diarias)} días "
          f"({min(urls_diarias) if urls_diarias else '?'} → {max(urls_diarias) if urls_diarias else '?'})")
    print(f"   Mensuales disponibles: {len(urls_mensuales)} meses "
          f"(desde {min(urls_mensuales) if urls_mensuales else '?'})")

    # ── Clasificar cada día del rango ────────────────────────────────────────
    dias_diarios  = dias_rango & set(urls_diarias.keys())
    dias_mensuales = dias_rango - dias_diarios

    # Agrupar días mensuales por (año, mes)
    meses_necesarios = {}
    for d in dias_mensuales:
        clave = (d.year, d.month)
        meses_necesarios.setdefault(clave, set()).add(d)

    # Días que no tienen cobertura en ninguna fuente
    sin_cobertura = set()
    for clave, dias in meses_necesarios.items():
        if clave not in urls_mensuales:
            sin_cobertura.update(dias)
            del meses_necesarios[clave]  # quitar del proceso

    if sin_cobertura:
        fechas_str = sorted(d.strftime("%d/%m/%Y") for d in sin_cobertura)
        print(f"⚠️  Sin datos para {len(sin_cobertura)} día(s): {', '.join(fechas_str[:5])}"
              + ("..." if len(fechas_str) > 5 else ""))

    total_descargas = len(dias_diarios) + len(meses_necesarios)
    if total_descargas == 0:
        print("❌ No hay datos disponibles para el rango solicitado.")
        return pd.DataFrame()

    print(f"\n📅 Rango: {fecha_inicio} → {fecha_fin}")
    print(f"   Ficheros diarios a descargar : {len(dias_diarios)}")
    print(f"   Ficheros mensuales a descargar: {len(meses_necesarios)}")

    todos_registros = []

    # ── Descargar ficheros DIARIOS ───────────────────────────────────────────
    for d in sorted(dias_diarios):
        url = urls_diarias[d]
        print(f"   ↓ [{d.strftime('%d/%m/%Y')}] diario...", end=" ", flush=True)
        try:
            contenido = _descargar_zip(session, url)
            if contenido:
                recs = parsear_fichero(contenido)
                for r in recs:
                    r["_fecha"] = d
                todos_registros.extend(recs)
                print(f"✓ ({len(recs):,} registros)")
            else:
                print("⚠️  ZIP vacío")
        except Exception as e:
            print(f"✗ {e}")

    # ── Descargar ficheros MENSUALES ─────────────────────────────────────────
    for (anio, mes), dias_del_mes in sorted(meses_necesarios.items()):
        url = urls_mensuales[(anio, mes)]
        n_dias = len(dias_del_mes)
        print(f"   ↓ [{anio}-{mes:02d}] mensual ({n_dias} día(s) del mes)...",
              end=" ", flush=True)
        try:
            contenido = _descargar_zip(session, url)
            if contenido:
                recs = parsear_fichero(contenido, filtro_fechas=dias_del_mes)
                todos_registros.extend(recs)
                print(f"✓ ({len(recs):,} registros)")
            else:
                print("⚠️  ZIP vacío")
        except Exception as e:
            print(f"✗ {e}")

    if not todos_registros:
        return pd.DataFrame()

    df = pd.DataFrame(todos_registros)

    # ── Filtros ──────────────────────────────────────────────────────────────
    if solo_nuevos and "IndNuevoUsado" in df.columns:
        df = df[df["IndNuevoUsado"] == "N"]

    if filtro_tipo and "CodTipo" in df.columns:
        tipos_mapa = {v.lower(): k for k, v in TIPOS_VEHICULO.items()}
        cod = tipos_mapa.get(filtro_tipo.lower())
        if cod:
            df = df[df["CodTipo"] == cod]
            print(f"   Filtro tipo vehiculo: {filtro_tipo} (cód. {cod})")
        else:
            print(f"   ⚠️  Tipo '{filtro_tipo}' no reconocido. "
                  f"Opciones: {', '.join(TIPOS_VEHICULO.values())}")

    if filtro_propulsion and "CodPropulsionItv" in df.columns:
        prop_mapa = {v.lower(): k for k, v in PROPULSION.items()}
        cod = prop_mapa.get(filtro_propulsion.lower())
        if cod:
            df = df[df["CodPropulsionItv"] == cod]
            print(f"   Filtro propulsión: {filtro_propulsion} (cód. {cod})")

    return df

# ─────────────────────────────────────────────────────────────────────────────
# GENERAR GRÁFICA
# ─────────────────────────────────────────────────────────────────────────────

def generar_grafica(df, fecha_inicio, fecha_fin, top_n=25,
                    titulo_extra="", guardar_imagen=None):
    conteo = (
        df["MarcaItv"].str.upper()
        .value_counts()
        .head(top_n)
        .sort_values(ascending=True)
    )

    fig, ax = plt.subplots(figsize=(12, max(6, top_n * 0.4)))
    fig.patch.set_facecolor("white")
    barras = ax.barh(conteo.index, conteo.values, color="#2980b9", height=0.7)

    for barra, valor in zip(barras, conteo.values):
        ax.text(barra.get_width() + conteo.max() * 0.005,
                barra.get_y() + barra.get_height() / 2,
                f"{valor:,}", va="center", ha="left", fontsize=9, color="#333")

    # Título estilo DGT
    if fecha_inicio.month == fecha_fin.month and fecha_inicio.year == fecha_fin.year:
        meses_es = ["ENERO","FEBRERO","MARZO","ABRIL","MAYO","JUNIO",
                    "JULIO","AGOSTO","SEPTIEMBRE","OCTUBRE","NOVIEMBRE","DICIEMBRE"]
        mes_str = f"{meses_es[fecha_inicio.month-1]} {fecha_inicio.year}"
        rango_dias = f"{fecha_inicio.day}-{fecha_fin.day}"
        titulo = f"GLOBALES {rango_dias} {mes_str} POR MARCA (TOP {top_n})"
    else:
        titulo = (f"MATRICULACIONES {fecha_inicio.strftime('%d/%m/%Y')} – "
                  f"{fecha_fin.strftime('%d/%m/%Y')} POR MARCA (TOP {top_n})")
    if titulo_extra:
        titulo += f"\n{titulo_extra}"

    ax.set_title(titulo, fontsize=13, fontweight="bold", pad=15)
    ax.set_xlabel("UNIDADES", fontsize=10, labelpad=8)
    ax.set_ylabel("MARCA", fontsize=10, labelpad=8)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
    ax.tick_params(axis="y", labelsize=9)
    ax.tick_params(axis="x", labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0, conteo.max() * 1.12)
    plt.tight_layout()

    if guardar_imagen:
        plt.savefig(guardar_imagen, dpi=150, bbox_inches="tight")
        print(f"🖼️  Gráfica guardada en: {guardar_imagen}")
    else:
        plt.show()
    plt.close()

# ─────────────────────────────────────────────────────────────────────────────
# GUARDAR EN EXCEL
# ─────────────────────────────────────────────────────────────────────────────

def guardar_excel(df, ruta_excel):
    conteo_marca = (
        df["MarcaItv"].str.upper().value_counts()
        .reset_index()
        .rename(columns={"MarcaItv": "MARCA", "count": "UNIDADES"})
    )
    conteo_marca.index = conteo_marca.index + 1
    conteo_marca.index.name = "POSICIÓN"

    with pd.ExcelWriter(ruta_excel, engine="openpyxl") as writer:
        conteo_marca.to_excel(writer, sheet_name="Por Marca")

        if "CodTipo" in df.columns:
            df2 = df.copy()
            df2["Tipo"] = df2["CodTipo"].map(TIPOS_VEHICULO).fillna(df2["CodTipo"])
            (df2["Tipo"].value_counts()
             .reset_index().rename(columns={"Tipo": "TIPO", "count": "UNIDADES"})
             .to_excel(writer, sheet_name="Por Tipo", index=False))

        if "CodPropulsionItv" in df.columns:
            df3 = df.copy()
            df3["Prop"] = df3["CodPropulsionItv"].map(PROPULSION).fillna(df3["CodPropulsionItv"])
            (df3["Prop"].value_counts()
             .reset_index().rename(columns={"Prop": "PROPULSIÓN", "count": "UNIDADES"})
             .to_excel(writer, sheet_name="Por Propulsión", index=False))

    print(f"📊 Excel guardado en: {ruta_excel}")

# ─────────────────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Estadísticas de matriculaciones DGT por marca",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Cobertura de datos:
  • Últimos ~30 días → ficheros diarios (un ZIP por día)
  • Desde dic. 2014  → ficheros mensuales (un ZIP por mes, filtrado por fecha)
  El script elige automáticamente la fuente correcta.

Ejemplos:
  python dgt_matriculaciones.py --inicio 2026-03-01 --fin 2026-03-08
  python dgt_matriculaciones.py --inicio 2026-01-01 --fin 2026-01-31
  python dgt_matriculaciones.py --inicio 2025-01-01 --fin 2025-12-31 --top 10
  python dgt_matriculaciones.py --inicio 2026-03-01 --fin 2026-03-08 --tipo turismo --propulsion electrico
  python dgt_matriculaciones.py --inicio 2026-03-01 --fin 2026-03-08 --guardar res.xlsx --imagen grafica.png
        """
    )
    parser.add_argument("--inicio",     required=True, help="Fecha inicio (YYYY-MM-DD)")
    parser.add_argument("--fin",        required=True, help="Fecha fin (YYYY-MM-DD)")
    parser.add_argument("--top",        type=int, default=25, help="Nº marcas (default: 25)")
    parser.add_argument("--tipo",       default=None,
                        help=f"Tipo vehículo: {', '.join(TIPOS_VEHICULO.values())}")
    parser.add_argument("--propulsion", default=None,
                        help=f"Propulsión: {', '.join(PROPULSION.values())}")
    parser.add_argument("--todos",      action="store_true",
                        help="Incluir nuevos Y usados (por defecto solo nuevos)")
    parser.add_argument("--guardar",    default=None, help="Guardar Excel (ej: res.xlsx)")
    parser.add_argument("--imagen",     default=None, help="Guardar gráfica PNG (ej: grafica.png)")
    args = parser.parse_args()

    try:
        fecha_inicio = datetime.strptime(args.inicio, "%Y-%m-%d").date()
        fecha_fin    = datetime.strptime(args.fin,    "%Y-%m-%d").date()
    except ValueError as e:
        print(f"❌ Formato de fecha incorrecto: {e}"); sys.exit(1)

    if fecha_inicio > fecha_fin:
        print("❌ La fecha de inicio debe ser anterior a la de fin."); sys.exit(1)

    df = descargar_y_procesar(
        fecha_inicio, fecha_fin,
        filtro_tipo=args.tipo,
        filtro_propulsion=args.propulsion,
        solo_nuevos=not args.todos
    )

    if df.empty:
        print("❌ No hay datos para mostrar."); sys.exit(1)

    total = len(df)
    print(f"\n✅ Total matriculaciones: {total:,}")

    filtros = []
    if args.tipo:        filtros.append(f"Tipo: {args.tipo}")
    if args.propulsion:  filtros.append(f"Propulsión: {args.propulsion}")
    if args.todos:       filtros.append("Nuevos + Usados")

    if args.guardar:
        guardar_excel(df, args.guardar)

    generar_grafica(df, fecha_inicio, fecha_fin,
                    top_n=args.top,
                    titulo_extra=" | ".join(filtros),
                    guardar_imagen=args.imagen)

    print(f"\n📋 TOP {min(10, args.top)} MARCAS:")
    print("-" * 32)
    for i, (marca, n) in enumerate(df["MarcaItv"].str.upper().value_counts().head(10).items(), 1):
        print(f"  {i:>2}. {marca:<26} {n:>6,}")
    print("-" * 32)
    print(f"  {'TOTAL':>28} {total:>6,}")


if __name__ == "__main__":
    main()
