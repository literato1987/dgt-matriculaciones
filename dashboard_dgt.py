"""
dashboard_dgt.py
================
Dashboard interactivo de matriculaciones DGT con Streamlit.
Enfocado en vehículos eléctricos BEV (CodPropulsion = 'E').

Uso:
    streamlit run dashboard_dgt.py

Requisitos:
    pip install -r requirements.txt
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Importar lógica de descarga desde el módulo principal
sys.path.insert(0, str(Path(__file__).parent))
from dgt_matriculaciones import (
    PROPULSION,
    TIPOS_VEHICULO,
    _descargar_zip,
    obtener_urls_diarias,
    obtener_urls_mensuales,
    parsear_fichero,
)
from cache_db import (
    dias_ya_descargados,
    guardar_registros,
    inicializar_db,
    inicializar_cloud_db,
    is_cloud_db,
    meses_ya_descargados,
    n_registros_total,
    query_registros,
    query_stats_propulsion,
)

# ─────────────────────────────────────────────────────────────────────────────
# CARGA CON CACHÉ INTELIGENTE
# ─────────────────────────────────────────────────────────────────────────────

def _clave_dia(d: date) -> str:
    return d.strftime("%Y%m%d")

def _clave_mes(anio: int, mes: int) -> str:
    return f"{anio}{mes:02d}"


def cargar_datos_con_cache(conn, fecha_inicio: date, fecha_fin: date):
    """
    Descarga solo los días que faltan en la DB y los persiste.
    Devuelve (ficheros_descargados, dias_sin_cobertura, errores).
    Muestra progreso en Streamlit directamente.
    """
    import requests

    dias_en_db = dias_ya_descargados(conn)
    meses_en_db = meses_ya_descargados(conn)

    dias_rango = set()
    d = fecha_inicio
    while d <= fecha_fin:
        dias_rango.add(d)
        d += timedelta(days=1)

    dias_faltantes = dias_rango - dias_en_db
    if not dias_faltantes:
        return 0, 0, []

    status = st.status("Consultando índices de la DGT...", expanded=True)

    try:
        urls_diarias = obtener_urls_diarias()
        urls_mensuales = obtener_urls_mensuales()
        status.write(f"Diarios disponibles: {len(urls_diarias)} días | Mensuales: {len(urls_mensuales)} meses")
    except Exception as e:
        status.update(label=f"Error al consultar la DGT: {e}", state="error")
        return 0, 0, [str(e)]

    dias_via_diario = dias_faltantes & set(urls_diarias.keys())
    dias_via_mensual = dias_faltantes - dias_via_diario

    meses_necesarios: dict = {}
    for d in dias_via_mensual:
        clave_mes = (d.year, d.month)
        if clave_mes not in meses_en_db and clave_mes in urls_mensuales:
            meses_necesarios.setdefault(clave_mes, set()).add(d)

    dias_sin_cobertura = len(
        dias_via_mensual - {d for dias in meses_necesarios.values() for d in dias}
    )

    total_ops = len(dias_via_diario) + len(meses_necesarios)
    if total_ops == 0:
        status.update(label="Sin ficheros nuevos que descargar.", state="complete")
        return 0, dias_sin_cobertura, []

    status.write(f"Por descargar: {len(dias_via_diario)} fichero(s) diario(s) + {len(meses_necesarios)} mensual(es)")

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; DGT-dashboard/1.0)"})

    descargados = 0
    errores = []
    bar = st.progress(0, text="Iniciando descarga...")

    # Ficheros DIARIOS
    for d in sorted(dias_via_diario):
        bar.progress(descargados / total_ops, text=f"Diario {d.strftime('%d/%m/%Y')}...")
        try:
            contenido = _descargar_zip(session, urls_diarias[d])
            if contenido:
                recs = parsear_fichero(contenido)
                for r in recs:
                    r["FecMatricula"] = d.strftime("%Y%m%d")
                guardar_registros(conn, recs, "dia", _clave_dia(d))
                descargados += 1
                status.write(f"✓ {d.strftime('%d/%m/%Y')} — {len(recs):,} registros")
        except Exception as e:
            err = f"Error {d}: {e}"
            errores.append(err)
            status.write(f"✗ {err}")

    # Ficheros MENSUALES
    for (anio, mes), dias_del_mes in sorted(meses_necesarios.items()):
        bar.progress(descargados / total_ops, text=f"Mensual {anio}-{mes:02d}...")
        try:
            contenido = _descargar_zip(session, urls_mensuales[(anio, mes)])
            if contenido:
                recs = parsear_fichero(contenido, filtro_fechas=dias_del_mes)
                guardar_registros(conn, recs, "mes", _clave_mes(anio, mes))
                descargados += 1
                status.write(f"✓ {anio}-{mes:02d} — {len(recs):,} registros ({len(dias_del_mes)} días del rango)")
        except Exception as e:
            err = f"Error {anio}-{mes:02d}: {e}"
            errores.append(err)
            status.write(f"✗ {err}")

    bar.empty()
    estado = "error" if errores and descargados == 0 else "complete"
    status.update(label=f"Descarga completada: {descargados} fichero(s) | {dias_sin_cobertura} días sin cobertura", state=estado)
    return descargados, dias_sin_cobertura, errores


# ─────────────────────────────────────────────────────────────────────────────
# GRÁFICO PARETO
# ─────────────────────────────────────────────────────────────────────────────

CODIGO_BEV = "2"  # Confirmado: DGT usa "2" para vehículos eléctricos (BEV)

BG      = "#1a1a2e"
BG_PLOT = "#16213e"
ACCENT  = "#4fc3f7"
LINE    = "#90caf9"
TEXT    = "#e0e0e0"
GRID    = "#2a2a4a"

_TREEMAP_COLORS = [
    "#4fc3f7", "#ef5350", "#66bb6a", "#ffa726", "#ab47bc",
    "#26c6da", "#ff7043", "#42a5f5", "#d4e157", "#ec407a",
    "#26a69a", "#ffca28", "#7e57c2", "#8d6e63", "#78909c",
    "#f06292", "#aed581", "#4db6ac", "#ff8a65", "#9575cd",
    "#4dd0e1", "#dce775", "#a1887f", "#90a4ae", "#e57373",
]

def treemap_chart(serie: pd.Series, titulo: str, top_n: int, preagregado: bool = False) -> go.Figure:
    if preagregado:
        conteo = serie.head(top_n).reset_index()
        conteo.columns = ["categoria", "unidades"]
    else:
        conteo = serie.value_counts().head(top_n).reset_index()
        conteo.columns = ["categoria", "unidades"]
    n = len(conteo)
    colores = [_TREEMAP_COLORS[i % len(_TREEMAP_COLORS)] for i in range(n)]

    fig = go.Figure(go.Treemap(
        labels=conteo["categoria"].tolist(),
        parents=[""] * n,
        values=conteo["unidades"].tolist(),
        marker=dict(
            colors=colores,
            line=dict(width=2, color=BG),
        ),
        texttemplate="<b>%{label}</b><br>%{value:,}",
        textfont=dict(size=13, color="white"),
        hovertemplate="<b>%{label}</b><br>%{value:,} unidades<extra></extra>",
        tiling=dict(packing="squarify"),
    ))
    fig.update_layout(
        title=dict(text=titulo, font=dict(size=14, color=TEXT), x=0.01),
        paper_bgcolor=BG,
        margin=dict(t=50, b=10, l=10, r=10),
        height=500,
    )
    return fig


def pareto_chart(serie: pd.Series, titulo: str, eje_x: str, top_n: int, preagregado: bool = False) -> go.Figure:
    if preagregado:
        conteo = serie.head(top_n).reset_index()
        conteo.columns = ["categoria", "unidades"]
    else:
        conteo = serie.value_counts().head(top_n).reset_index()
        conteo.columns = ["categoria", "unidades"]
    # Horizontal: menor arriba → mayor abajo visualmente (ascending=True para barh)
    conteo = conteo.sort_values("unidades", ascending=True).reset_index(drop=True)

    total = conteo["unidades"].sum()
    conteo["acumulado_pct"] = conteo["unidades"].cumsum() / total * 100

    fig = go.Figure()

    # Barras horizontales
    fig.add_trace(go.Bar(
        y=conteo["categoria"],
        x=conteo["unidades"],
        orientation="h",
        name="Unidades",
        marker=dict(
            color=conteo["unidades"],
            colorscale=[[0, "#1565c0"], [0.5, "#1e88e5"], [1, ACCENT]],
            showscale=False,
        ),
        text=conteo["unidades"].apply(lambda v: f"{v:,}"),
        textposition="outside",
        textfont=dict(color=TEXT, size=11),
        xaxis="x1",
    ))

    # Línea % acumulado (eje X secundario)
    fig.add_trace(go.Scatter(
        y=conteo["categoria"],
        x=conteo["acumulado_pct"],
        name="% Acumulado",
        mode="lines+markers",
        line=dict(color=LINE, width=2),
        marker=dict(size=5, color=LINE),
        xaxis="x2",
    ))

    fig.update_layout(
        title=dict(text=titulo, font=dict(size=14, color=TEXT), x=0.01),
        xaxis=dict(
            title="Unidades",
            showgrid=True,
            gridcolor=GRID,
            tickfont=dict(color=TEXT),
            titlefont=dict(color=TEXT),
            zeroline=False,
        ),
        xaxis2=dict(
            title="% Acumulado",
            overlaying="x",
            side="top",
            range=[0, 108],
            showgrid=False,
            ticksuffix="%",
            tickfont=dict(color=LINE),
            titlefont=dict(color=LINE),
        ),
        yaxis=dict(
            title=eje_x,
            tickfont=dict(color=TEXT, size=11),
            titlefont=dict(color=TEXT),
            showgrid=False,
        ),
        legend=dict(
            orientation="h", y=-0.08, x=0,
            font=dict(color=TEXT),
            bgcolor="rgba(0,0,0,0)",
        ),
        plot_bgcolor=BG_PLOT,
        paper_bgcolor=BG,
        height=max(380, top_n * 28),
        margin=dict(t=60, b=40, l=10, r=80),
        bargap=0.25,
    )
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# LAYOUT
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DGT Matriculaciones Eléctricas",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Matriculaciones DGT — Vehículos Eléctricos")
st.caption("Datos oficiales DGT · Actualizado con caché local SQLite · Fuente: dgt.es")

# ── Inicializar DB (local o cloud) ─────────────────────────────────────────
_CLOUD_DB_URL = (
    "https://github.com/literato1987/dgt-matriculaciones"
    "/releases/download/v1.0.0/datos_dgt_cloud.db"
)

def _descargar_cloud_db(dst: Path) -> bool:
    """Descarga datos_dgt_cloud.db desde GitHub Releases si no existe."""
    import requests
    try:
        ph = st.empty()
        ph.info("⬇️ Primera ejecución: descargando base de datos (~145 MB)…")
        with requests.get(_CLOUD_DB_URL, stream=True, timeout=300) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            bar = st.progress(0, text="Descargando…")
            downloaded = 0
            with open(dst, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total
                        bar.progress(pct, text=f"Descargando… {downloaded >> 20} / {total >> 20} MB")
            bar.empty()
        ph.empty()
        return True
    except Exception as e:
        st.error(f"Error descargando la base de datos: {e}")
        return False

@st.cache_resource
def get_conn():
    _cloud = Path(__file__).parent / "datos_dgt_cloud.db"
    if not _cloud.exists():
        _local = Path(__file__).parent / "datos_dgt.db"
        if not _local.exists():
            _descargar_cloud_db(_cloud)
    if _cloud.exists():
        return inicializar_cloud_db(_cloud)
    return inicializar_db()

conn = get_conn()
CLOUD = is_cloud_db(conn)

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filtros")

    hoy = date.today()
    # Por defecto: los 2 últimos meses completos
    _primer_dia_mes_actual = hoy.replace(day=1)
    _default_fin = _primer_dia_mes_actual - timedelta(days=1)          # último día del mes anterior
    _default_inicio = _default_fin.replace(day=1) - timedelta(days=1)  # último día de hace 2 meses
    _default_inicio = _default_inicio.replace(day=1)                   # primer día de hace 2 meses

    fecha_inicio = st.date_input(
        "Fecha inicio",
        value=_default_inicio,
        max_value=hoy,
    )
    fecha_fin = st.date_input(
        "Fecha fin",
        value=_default_fin,
        max_value=hoy,
    )

    electrico_opcion = st.radio(
        "Tipo de propulsión",
        options=["Solo BEV eléctricos", "Todos los vehículos", "Excluir eléctricos"],
        index=0,
    )

    # Grupos de tipo de vehículo (códigos reales DGT confirmados desde BD)
    _GRUPOS_TIPO = {
        "Turismos / SUV": ["40", "25"],
        "Motocicletas": ["50"],
        "Ciclomotores": ["90"],
        "Furgonetas / Comerciales": ["20", "0G"],
        "Camiones": ["81"],
        "Autobuses": ["30"],
        "Otros": [],  # vacío = sin filtro adicional
    }
    tipo_vehiculo = st.multiselect(
        "Tipo de vehículo",
        options=list(_GRUPOS_TIPO.keys()),
        default=["Turismos / SUV"],
    )
    # Si no selecciona nada → todos
    _codigos_tipo_sel = []
    _incluir_otros = "Otros" in tipo_vehiculo
    for g in tipo_vehiculo:
        if g != "Otros":
            _codigos_tipo_sel.extend(_GRUPOS_TIPO[g])

    solo_nuevos = st.checkbox("Solo vehículos nuevos", value=True)

    top_marcas  = st.slider("Top marcas",  5, 50, 20, step=5)
    top_modelos = st.slider("Top modelos", 5, 50, 20, step=5)

    st.divider()
    if CLOUD:
        _meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        st.caption(f"Datos: {_meta.get('fecha_min','?')} → {_meta.get('fecha_max','?')}")
        cargar = False
    else:
        @st.cache_data(ttl=300)
        def _n_total():
            return n_registros_total(conn)
        st.caption(f"BD local: {_n_total():,} registros almacenados")
        cargar = st.button("Actualizar datos", type="primary", use_container_width=True)

# ── Lógica de carga ────────────────────────────────────────────────────────
if fecha_inicio > fecha_fin:
    st.error("La fecha de inicio debe ser anterior a la fecha de fin.")
    st.stop()

if cargar:
    cargar_datos_con_cache(conn, fecha_inicio, fecha_fin)

# ── Filtro propulsión + tipo (todo en SQL) ─────────────────────────────────
_codigos_conocidos = ["40", "25", "50", "90", "20", "0G", "81", "30", "7A", "80", "70", "73", "S2", "S7", "RH"]
_incluir_otros = "Otros" in tipo_vehiculo

# ── Condiciones SQL comunes (se reusarán en todos los tabs) ─────────────────
# Fechas en el formato correcto según el tipo de DB
if CLOUD:
    _f_ini = fecha_inicio.strftime("%Y-%m")
    _f_fin = fecha_fin.strftime("%Y-%m")
    _col_fecha = "mes"
else:
    _f_ini = fecha_inicio.isoformat()
    _f_fin = fecha_fin.isoformat()
    _col_fecha = "fecha"

def _periodo_sql(agrupacion: str) -> str:
    """SQL para agrupar por período según el tipo de DB."""
    if CLOUD:
        if agrupacion == "Año":
            return "substr(mes, 1, 4)"
        elif agrupacion == "Trimestre":
            return "substr(mes,1,4)||'-Q'||CAST((CAST(substr(mes,6,2) AS INT)-1)/3+1 AS TEXT)"
        return "mes"
    else:
        if agrupacion == "Año":
            return "strftime('%Y', fecha)"
        elif agrupacion == "Trimestre":
            return ("strftime('%Y', fecha) || '-Q' || "
                    "CAST(((CAST(strftime('%m', fecha) AS INTEGER) - 1) / 3 + 1) AS TEXT)")
        return "strftime('%Y-%m', fecha)"

if not CLOUD:
    # cod_tipo_list para query_registros (None = sin filtro de tipo)
    if _codigos_tipo_sel and not _incluir_otros:
        _sql_tipo = _codigos_tipo_sel
    elif not _codigos_tipo_sel and not _incluir_otros:
        _sql_tipo = None
    else:
        _sql_tipo = None

    if electrico_opcion == "Solo BEV eléctricos":
        df = query_registros(conn, fecha_inicio, fecha_fin, cod_propulsion=CODIGO_BEV,
                             solo_nuevos=solo_nuevos, cod_tipo_list=_sql_tipo)
    elif electrico_opcion == "Todos los vehículos":
        df = query_registros(conn, fecha_inicio, fecha_fin, cod_propulsion=None,
                             solo_nuevos=solo_nuevos, cod_tipo_list=_sql_tipo)
    else:
        df = query_registros(conn, fecha_inicio, fecha_fin, cod_propulsion=None,
                             solo_nuevos=solo_nuevos, cod_tipo_list=_sql_tipo)
        df = df[df["CodPropulsionItv"] != CODIGO_BEV]

    if _incluir_otros:
        if _codigos_tipo_sel:
            df = df[df["CodTipo"].isin(_codigos_tipo_sel) | ~df["CodTipo"].isin(_codigos_conocidos)]
        else:
            df = df[~df["CodTipo"].isin(_codigos_conocidos)]

    if df.empty:
        df_sin_filtro = query_registros(conn, fecha_inicio, fecha_fin, cod_propulsion=None, solo_nuevos=solo_nuevos)
        if df_sin_filtro.empty:
            st.info("No hay datos en la BD local para este rango. "
                    "Pulsa **Actualizar datos** en la barra lateral.")
        else:
            resumen_prop = df_sin_filtro["CodPropulsionItv"].value_counts().to_dict()
            prop_str = " | ".join(
                f"{PROPULSION.get(k, k)}: {v:,}"
                for k, v in sorted(resumen_prop.items(), key=lambda x: -x[1])
            )
            st.warning(f"Hay **{len(df_sin_filtro):,}** vehículos pero ninguno coincide "
                       f"con el filtro.\n\n**Propulsiones disponibles:** {prop_str}")
        st.stop()

# ── Condiciones SQL para resumen_marca (Tab 1 y Tab 2 en cloud) ─────────────
_cm_conds: list = [f"{_col_fecha} >= ?", f"{_col_fecha} <= ?"]
_cm_params: list = [_f_ini, _f_fin]
if CLOUD and _col_fecha == "mes":
    pass  # fechas ya añadidas
if solo_nuevos:
    _cm_conds.append("ind_nuevo_usado = 'N'")
if electrico_opcion == "Solo BEV eléctricos":
    _cm_conds.append("cod_propulsion = ?")
    _cm_params.append(CODIGO_BEV)
elif electrico_opcion == "Excluir eléctricos":
    _cm_conds.append("cod_propulsion != ?")
    _cm_params.append(CODIGO_BEV)
if _codigos_tipo_sel and not _incluir_otros:
    _cm_conds.append(f"cod_tipo IN ({','.join('?'*len(_codigos_tipo_sel))})")
    _cm_params.extend(_codigos_tipo_sel)
_cm_where = " AND ".join(_cm_conds)

if CLOUD:
    # Construir series de marcas/modelos para Tab 1 desde resumen_marca
    _df_m = pd.read_sql_query(
        f"SELECT marca, SUM(n) AS n FROM resumen_marca WHERE {_cm_where} AND marca != '' "
        "GROUP BY marca ORDER BY n DESC",
        conn, params=_cm_params,
    )
    _df_mo = pd.read_sql_query(
        f"SELECT modelo, SUM(n) AS n FROM resumen_marca WHERE {_cm_where} AND modelo != '' "
        "GROUP BY modelo ORDER BY n DESC",
        conn, params=_cm_params,
    )
    marcas_s  = _df_m.set_index("marca")["n"]    if not _df_m.empty else pd.Series(dtype=int)
    modelos_s = _df_mo.set_index("modelo")["n"]  if not _df_mo.empty else pd.Series(dtype=int)

    if marcas_s.empty:
        st.info("No hay datos para este rango en la DB cloud.")
        st.stop()

    total      = int(marcas_s.sum())
    marcas_u   = len(marcas_s)
    modelos_u  = len(modelos_s)

    # pct_elec desde resumen_provincia
    _stats_rp = pd.read_sql_query(
        f"SELECT cod_propulsion, SUM(n) AS n FROM resumen_provincia "
        f"WHERE mes >= ? AND mes <= ? {'AND ind_nuevo_usado = \'N\'' if solo_nuevos else ''} "
        "GROUP BY cod_propulsion",
        conn, params=[_f_ini, _f_fin],
    )
    total_rango = int(_stats_rp["n"].sum()) or 1
    pct_elec = int(_stats_rp.loc[_stats_rp["cod_propulsion"] == CODIGO_BEV, "n"].sum()) / total_rango * 100
    _stats_prop = dict(zip(_stats_rp["cod_propulsion"], _stats_rp["n"]))
else:
    marcas_s  = None
    modelos_s = None
    total     = len(df)
    marcas_u  = df["MarcaItv"].nunique()
    modelos_u = df["ModeloItv"].nunique()
    _stats_prop = query_stats_propulsion(conn, fecha_inicio, fecha_fin, solo_nuevos=solo_nuevos)
    total_rango = sum(_stats_prop.values()) or 1
    pct_elec = _stats_prop.get(CODIGO_BEV, 0) / total_rango * 100

# ── Métricas ───────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Matriculaciones (filtro activo)", f"{total:,}")
col2.metric("Marcas distintas",   f"{marcas_u:,}")
col3.metric("Modelos distintos",  f"{modelos_u:,}")
col4.metric("% BEV del total del período", f"{pct_elec:.1f}%")

st.divider()

fecha_txt = f"{fecha_inicio.strftime('%d/%m/%Y')} – {fecha_fin.strftime('%d/%m/%Y')}"
filtro_txt = {
    "Solo BEV eléctricos": " [Solo BEV]",
    "Todos los vehículos": "",
    "Excluir eléctricos":  " [Sin eléctricos]",
}[electrico_opcion]

# Condiciones SQL reutilizables para el tab de evolución (mismos filtros del sidebar)
_evol_conds: list = []
_evol_params: list = []

if solo_nuevos:
    _evol_conds.append("ind_nuevo_usado = 'N'")
if electrico_opcion == "Solo BEV eléctricos":
    _evol_conds.append("cod_propulsion = ?")
    _evol_params.append(CODIGO_BEV)
elif electrico_opcion == "Excluir eléctricos":
    _evol_conds.append("cod_propulsion != ?")
    _evol_params.append(CODIGO_BEV)
if _codigos_tipo_sel:
    _evol_conds.append(f"cod_tipo IN ({','.join('?' * len(_codigos_tipo_sel))})")
    _evol_params.extend(_codigos_tipo_sel)
elif _incluir_otros:
    _evol_conds.append(f"cod_tipo NOT IN ({','.join('?' * len(_codigos_conocidos))})")
    _evol_params.extend(_codigos_conocidos)

_evol_conds.append(f"{_col_fecha} >= ?")
_evol_params.append(_f_ini)
_evol_conds.append(f"{_col_fecha} <= ?")
_evol_params.append(_f_fin)

_evol_where = "AND " + " AND ".join(_evol_conds)
_evol_table  = "resumen_marca" if CLOUD else "registros"
_evol_count  = "SUM(n)"       if CLOUD else "COUNT(*)"

# Mapeo siglas DGT de provincia → Comunidad Autónoma
_PROV_CCAA = {
    # Andalucía
    "AL": "Andalucía",  "CA": "Andalucía",  "CO": "Andalucía",  "GR": "Andalucía",
    "H":  "Andalucía",  "J":  "Andalucía",  "MA": "Andalucía",  "SE": "Andalucía",
    # Aragón
    "HU": "Aragón",     "TE": "Aragón",     "Z":  "Aragón",
    # Asturias
    "O":  "Asturias",
    # Islas Baleares
    "IB": "Islas Baleares",
    # Canarias
    "GC": "Canarias",   "TF": "Canarias",   "SC": "Canarias",
    # Cantabria
    "S":  "Cantabria",
    # Castilla-La Mancha
    "AB": "Castilla-La Mancha", "CR": "Castilla-La Mancha",
    "CU": "Castilla-La Mancha", "GU": "Castilla-La Mancha", "TO": "Castilla-La Mancha",
    # Castilla y León
    "AV": "Castilla y León",  "BU": "Castilla y León",  "LE": "Castilla y León",
    "P":  "Castilla y León",  "SA": "Castilla y León",  "SG": "Castilla y León",
    "SO": "Castilla y León",  "VA": "Castilla y León",  "ZA": "Castilla y León",
    # Cataluña
    "B":  "Cataluña",   "GI": "Cataluña",   "L":  "Cataluña",   "T":  "Cataluña",
    # Ceuta
    "CE": "Ceuta",
    # Extremadura
    "BA": "Extremadura", "CC": "Extremadura",
    # Galicia
    "C":  "Galicia",    "LU": "Galicia",    "OU": "Galicia",    "PO": "Galicia",
    # Madrid
    "M":  "Madrid",
    # Melilla
    "ML": "Melilla",
    # Murcia
    "MU": "Murcia",
    # Navarra
    "NA": "Navarra",
    # País Vasco
    "BI": "País Vasco",  "SS": "País Vasco",  "VI": "País Vasco",
    # La Rioja
    "LO": "La Rioja",
    # C. Valenciana
    "A":  "C. Valenciana", "CS": "C. Valenciana", "V":  "C. Valenciana",
}

# Mapeo siglas DGT → código INE de provincia (para choropleth)
_DGT_TO_INE = {
    "A":  "03",  "AB": "02",  "AL": "04",  "AV": "05",
    "B":  "08",  "BA": "06",  "BI": "48",  "BU": "09",
    "C":  "15",  "CA": "11",  "CC": "10",  "CE": "51",
    "CO": "14",  "CR": "13",  "CS": "12",  "CU": "16",
    "GC": "35",  "GI": "17",  "GR": "18",  "GU": "19",
    "H":  "21",  "HU": "22",  "IB": "07",  "J":  "23",
    "L":  "25",  "LE": "24",  "LO": "26",  "LU": "27",
    "M":  "28",  "MA": "29",  "ML": "52",  "MU": "30",
    "NA": "31",  "O":  "33",  "OU": "32",  "P":  "34",
    "PO": "36",  "S":  "39",  "SA": "37",  "SC": "38",
    "SE": "41",  "SG": "40",  "SO": "42",  "SS": "20",
    "T":  "43",  "TE": "44",  "TF": "38",  "TO": "45",
    "V":  "46",  "VA": "47",  "VI": "01",  "Z":  "50",
    "ZA": "49",
}

# GeoJSON de provincias — cargado desde disco (descargado una vez)
@st.cache_data(ttl=0)
def _get_provinces_geojson():
    import json as _json
    _path = Path(__file__).parent / "spain_provinces.geojson"
    if _path.exists():
        return _json.loads(_path.read_text(encoding="utf-8"))
    return None

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Ranking", "Evolución temporal", "BEV Share", "Comunidades", "Acerca de"])

# ── TAB 1: Paretos + Treemaps + Desglose ───────────────────────────────────
with tab1:
    # En cloud, marcas_s/modelos_s son Series preagregadas (index=nombre, value=n)
    # En local, las construimos desde df
    if CLOUD:
        marcas  = marcas_s
        modelos = modelos_s
        _preag  = True
    else:
        marcas  = df["MarcaItv"].dropna().replace("", pd.NA).dropna()
        modelos = df["ModeloItv"].dropna().replace("", pd.NA).dropna()
        _preag  = False

    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Pareto por Marca")
        if marcas.empty:
            st.info("Sin datos de marca.")
        else:
            st.plotly_chart(pareto_chart(
                marcas,
                titulo=f"Top {top_marcas} marcas · {fecha_txt}{filtro_txt}",
                eje_x="Marca",
                top_n=top_marcas,
                preagregado=_preag,
            ), use_container_width=True)

    with col_b:
        st.subheader("Pareto por Modelo")
        if modelos.empty:
            st.info("Sin datos de modelo.")
        else:
            st.plotly_chart(pareto_chart(
                modelos,
                titulo=f"Top {top_modelos} modelos · {fecha_txt}{filtro_txt}",
                eje_x="Modelo",
                top_n=top_modelos,
                preagregado=_preag,
            ), use_container_width=True)

    st.divider()
    st.subheader("Distribución por área")
    col_c, col_d = st.columns(2)

    with col_c:
        st.caption("Por Marca")
        if not marcas.empty:
            st.plotly_chart(treemap_chart(
                marcas,
                titulo=f"Marcas · {fecha_txt}{filtro_txt}",
                top_n=top_marcas,
                preagregado=_preag,
            ), use_container_width=True)

    with col_d:
        st.caption("Por Modelo")
        if not modelos.empty:
            st.plotly_chart(treemap_chart(
                modelos,
                titulo=f"Modelos · {fecha_txt}{filtro_txt}",
                top_n=top_modelos,
                preagregado=_preag,
            ), use_container_width=True)

    st.divider()
    with st.expander("Desglose por tipo de propulsión (período completo)"):
        resumen = pd.DataFrame([
            {
                "Propulsión": PROPULSION.get(k, f"Código '{k}'" if k else "Desconocido"),
                "Unidades": v,
                "% del total": f"{v / total_rango * 100:.1f} %",
            }
            for k, v in sorted(_stats_prop.items(), key=lambda x: -x[1])
        ])
        st.dataframe(resumen, use_container_width=True, hide_index=True)

# ── TAB 2: Evolución temporal por marca / modelo ───────────────────────────
with tab2:
    _marcas_db = [
        r[0] for r in conn.execute(
            f"SELECT marca FROM {_evol_table} WHERE marca != '' {_evol_where} "
            f"GROUP BY marca ORDER BY {_evol_count} DESC LIMIT 300",
            _evol_params,
        )
    ]
    marca_evol = st.selectbox("Marca", _marcas_db, key="evol_marca")

    _modelos_db = [
        r[0] for r in conn.execute(
            f"SELECT modelo FROM {_evol_table} WHERE marca = ? AND modelo != '' {_evol_where} "
            f"GROUP BY modelo ORDER BY {_evol_count} DESC",
            [marca_evol] + _evol_params,
        )
    ]
    modelos_evol = st.multiselect(
        "Modelos",
        _modelos_db,
        default=_modelos_db[:5] if _modelos_db else [],
        key="evol_modelos",
    )

    agrupacion = st.radio(
        "Agrupar por",
        ["Mes", "Trimestre", "Año"],
        horizontal=True,
        key="evol_agrup",
    )

    if not modelos_evol:
        st.info("Selecciona al menos un modelo.")
    else:
        placeholders = ",".join("?" * len(modelos_evol))
        _psql = _periodo_sql(agrupacion)
        _titulo_periodo = agrupacion

        _extra = "" if CLOUD else "AND fecha != ''"
        df_evol = pd.read_sql_query(
            f"""SELECT {_psql} AS periodo, modelo, {_evol_count} AS n
                FROM {_evol_table}
                WHERE marca = ? AND modelo IN ({placeholders}) {_extra}
                  {_evol_where}
                GROUP BY periodo, modelo
                ORDER BY periodo""",
            conn,
            params=[marca_evol] + modelos_evol + _evol_params,
        )

        if df_evol.empty:
            st.info("No hay datos para la selección.")
        else:
            df_pivot = df_evol.pivot(index="periodo", columns="modelo", values="n").fillna(0)

            fig_evol = go.Figure()
            for i, modelo in enumerate(df_pivot.columns):
                color = _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)]
                fig_evol.add_trace(go.Bar(
                    x=df_pivot.index.tolist(),
                    y=df_pivot[modelo].tolist(),
                    name=modelo,
                    marker_color=color,
                ))

            fig_evol.update_layout(
                barmode="stack",
                title=dict(
                    text=f"Matriculaciones por {_titulo_periodo.lower()} — {marca_evol}",
                    font=dict(size=14, color=TEXT), x=0.01,
                ),
                xaxis=dict(
                    title=_titulo_periodo,
                    tickfont=dict(color=TEXT, size=10),
                    titlefont=dict(color=TEXT),
                    showgrid=False,
                    tickangle=-45,
                ),
                yaxis=dict(
                    title="Unidades",
                    tickfont=dict(color=TEXT),
                    titlefont=dict(color=TEXT),
                    showgrid=True, gridcolor=GRID,
                ),
                legend=dict(font=dict(color=TEXT), bgcolor="rgba(0,0,0,0)"),
                plot_bgcolor=BG_PLOT,
                paper_bgcolor=BG,
                height=520,
                margin=dict(t=60, b=80, l=60, r=20),
            )
            st.plotly_chart(fig_evol, use_container_width=True)

# ── TAB 3: BEV Share ───────────────────────────────────────────────────────
with tab3:
    agrupacion_share = st.radio(
        "Agrupar por",
        ["Mes", "Trimestre", "Año"],
        horizontal=True,
        key="share_agrup",
    )

    _titulo_share = agrupacion_share
    _periodo_share_sql = _periodo_sql(agrupacion_share)

    # WHERE sin filtro de propulsión (mix completo para calcular BEV share)
    _share_conds = [f"{_col_fecha} >= ?", f"{_col_fecha} <= ?"]
    _share_params = [_f_ini, _f_fin]
    if not CLOUD:
        _share_conds.append("fecha != ''")
    if solo_nuevos:
        _share_conds.append("ind_nuevo_usado = 'N'")
    if _codigos_tipo_sel and not _incluir_otros:
        _share_conds.append(f"cod_tipo IN ({','.join('?' * len(_codigos_tipo_sel))})")
        _share_params.extend(_codigos_tipo_sel)
    elif _incluir_otros and not _codigos_tipo_sel:
        _share_conds.append(f"cod_tipo NOT IN ({','.join('?' * len(_codigos_conocidos))})")
        _share_params.extend(_codigos_conocidos)

    _share_where = " AND ".join(_share_conds)
    _share_table  = "resumen_provincia" if CLOUD else "registros"
    _share_count  = "SUM(n)"            if CLOUD else "COUNT(*)"

    df_share = pd.read_sql_query(
        f"""SELECT {_periodo_share_sql} AS periodo, cod_propulsion, {_share_count} AS n
            FROM {_share_table}
            WHERE {_share_where}
            GROUP BY periodo, cod_propulsion
            ORDER BY periodo""",
        conn,
        params=_share_params,
    )

    if df_share.empty:
        st.info("No hay datos para este rango. Descarga datos primero.")
    else:
        # Clasificar propulsiones en 3 grupos
        def _grupo_prop(cod):
            if cod == "2":   return "BEV"
            if cod == "3":   return "PHEV"
            return "No-EV"

        df_share["grupo"] = df_share["cod_propulsion"].apply(_grupo_prop)
        df_agg = (
            df_share.groupby(["periodo", "grupo"])["n"]
            .sum()
            .reset_index()
            .pivot(index="periodo", columns="grupo", values="n")
            .fillna(0)
        )
        # Asegurar que siempre existen las tres columnas
        for col in ["BEV", "PHEV", "No-EV"]:
            if col not in df_agg.columns:
                df_agg[col] = 0

        df_agg["total"] = df_agg["BEV"] + df_agg["PHEV"] + df_agg["No-EV"]
        df_agg["bev_pct"] = df_agg["BEV"] / df_agg["total"].replace(0, 1) * 100

        periodos = df_agg.index.tolist()

        fig_share = go.Figure()

        # Barras apiladas
        fig_share.add_trace(go.Bar(
            x=periodos, y=df_agg["No-EV"].tolist(),
            name="No-EV", marker_color="#ffa726", yaxis="y1",
        ))
        fig_share.add_trace(go.Bar(
            x=periodos, y=df_agg["PHEV"].tolist(),
            name="PHEV", marker_color="#ab47bc", yaxis="y1",
        ))
        fig_share.add_trace(go.Bar(
            x=periodos, y=df_agg["BEV"].tolist(),
            name="BEV", marker_color="#66bb6a", yaxis="y1",
        ))

        # Línea BEV share (eje derecho)
        fig_share.add_trace(go.Scatter(
            x=periodos, y=df_agg["bev_pct"].tolist(),
            name="BEV Share %",
            mode="lines+markers",
            line=dict(color="#4fc3f7", width=2),
            marker=dict(size=4),
            yaxis="y2",
        ))

        fig_share.update_layout(
            barmode="stack",
            title=dict(
                text=f"Matriculaciones por {_titulo_share.lower()} y cuota BEV · {fecha_txt}",
                font=dict(size=14, color=TEXT), x=0.01,
            ),
            xaxis=dict(
                title=_titulo_share,
                tickfont=dict(color=TEXT, size=10),
                titlefont=dict(color=TEXT),
                showgrid=False,
                tickangle=-45,
            ),
            yaxis=dict(
                title="Matriculaciones",
                tickfont=dict(color=TEXT),
                titlefont=dict(color=TEXT),
                showgrid=True, gridcolor=GRID,
            ),
            yaxis2=dict(
                title="BEV Share %",
                overlaying="y",
                side="right",
                ticksuffix="%",
                tickfont=dict(color="#4fc3f7"),
                titlefont=dict(color="#4fc3f7"),
                showgrid=False,
                range=[0, max(df_agg["bev_pct"].max() * 1.3, 5)],
            ),
            legend=dict(
                orientation="h", y=-0.15, x=0,
                font=dict(color=TEXT),
                bgcolor="rgba(0,0,0,0)",
            ),
            plot_bgcolor=BG_PLOT,
            paper_bgcolor=BG,
            height=540,
            margin=dict(t=60, b=100, l=60, r=60),
        )
        st.plotly_chart(fig_share, use_container_width=True)

        # Tabla resumen por período
        with st.expander("Ver datos"):
            df_tabla = df_agg[["BEV", "PHEV", "No-EV", "total", "bev_pct"]].copy()
            df_tabla.columns = ["BEV", "PHEV", "No-EV", "Total", "BEV Share %"]
            df_tabla["BEV Share %"] = df_tabla["BEV Share %"].round(1)
            st.dataframe(df_tabla, use_container_width=True)

# ── TAB 4: Provincias / Comunidades ─────────────────────────────────────────
with tab4:
    # ── 1. Choropleth por provincia ─────────────────────────────────────────
    df_prov_map = pd.read_sql_query(
        f"""SELECT cod_provincia, cod_propulsion, {_share_count} AS n
            FROM {_share_table}
            WHERE {_share_where} AND cod_provincia != ''
            GROUP BY cod_provincia, cod_propulsion""",
        conn, params=_share_params,
    )

    if df_prov_map.empty:
        st.info("No hay datos para este rango. Descarga datos primero.")
    else:
        _dgt = df_prov_map["cod_provincia"].astype(str).str.strip()
        df_prov_map["cod_ine"] = _dgt.map(_DGT_TO_INE)
        df_prov_map["ccaa"]    = _dgt.map(_PROV_CCAA)
        df_prov_map = df_prov_map.dropna(subset=["cod_ine"])

        # Agrupación por provincia (para el mapa)
        df_prov_agg = (
            df_prov_map.groupby(["cod_ine", "cod_propulsion"])["n"]
            .sum().reset_index()
            .pivot_table(index="cod_ine", columns="cod_propulsion", values="n", fill_value=0)
        )
        df_prov_agg["total"] = df_prov_agg.sum(axis=1)
        if CODIGO_BEV not in df_prov_agg.columns:
            df_prov_agg[CODIGO_BEV] = 0
        df_prov_agg["bev_pct"] = (
            df_prov_agg[CODIGO_BEV] / df_prov_agg["total"].replace(0, 1) * 100
        )
        df_prov_plot = df_prov_agg.reset_index()[["cod_ine", "bev_pct"]].copy()

        # Añadir nombre de provincia para el hover
        _ine_to_name = {
            f["properties"]["cod_prov"]: f["properties"]["name"]
            for f in (_get_provinces_geojson() or {}).get("features", [])
        }
        df_prov_plot["provincia"] = df_prov_plot["cod_ine"].map(_ine_to_name)

        # Agrupación por CCAA (para ranking lateral y líneas)
        df_ccaa_agg = (
            df_prov_map.dropna(subset=["ccaa"])
            .groupby(["ccaa", "cod_propulsion"])["n"]
            .sum().reset_index()
            .pivot_table(index="ccaa", columns="cod_propulsion", values="n", fill_value=0)
        )
        df_ccaa_agg["total"] = df_ccaa_agg.sum(axis=1)
        if CODIGO_BEV not in df_ccaa_agg.columns:
            df_ccaa_agg[CODIGO_BEV] = 0
        df_ccaa_agg["bev_pct"] = (
            df_ccaa_agg[CODIGO_BEV] / df_ccaa_agg["total"].replace(0, 1) * 100
        )
        df_ccaa_map = df_ccaa_agg.reset_index()[["ccaa", "bev_pct"]].copy()

        geojson_prov = _get_provinces_geojson()
        col_mapa, col_tabla = st.columns([3, 1])
        with col_mapa:
            if geojson_prov:
                fig_map = px.choropleth_mapbox(
                    df_prov_plot,
                    geojson=geojson_prov,
                    locations="cod_ine",
                    featureidkey="properties.cod_prov",
                    color="bev_pct",
                    color_continuous_scale="RdYlGn",
                    mapbox_style="carto-darkmatter",
                    zoom=4.5,
                    center={"lat": 40.4, "lon": -3.7},
                    opacity=0.85,
                    hover_name="provincia",
                    hover_data={"bev_pct": ":.1f", "cod_ine": False},
                    labels={"bev_pct": "BEV %"},
                    title=f"Cuota BEV (%) por provincia · {fecha_txt}",
                )
                fig_map.update_layout(
                    paper_bgcolor=BG,
                    margin=dict(t=50, b=10, l=0, r=0),
                    height=500,
                    coloraxis_colorbar=dict(
                        title="BEV %",
                        ticksuffix="%",
                        tickfont=dict(color=TEXT),
                        titlefont=dict(color=TEXT),
                    ),
                )
                st.plotly_chart(fig_map, use_container_width=True)
            else:
                st.warning("No se encontró spain_provinces.geojson en el directorio.")

        with col_tabla:
            st.markdown("**Ranking por CCAA**")
            _df_rank = (
                df_ccaa_map
                .sort_values("bev_pct", ascending=False)
                .reset_index(drop=True)
            )
            _df_rank["bev_pct"] = _df_rank["bev_pct"].round(1)
            _df_rank.columns = ["CCAA", "BEV %"]
            st.dataframe(_df_rank, use_container_width=True, hide_index=True)

        st.divider()

        # ── 2. Treemap: ventas totales por CCAA ────────────────────────────
        df_ccaa_total = (
            df_prov_map.dropna(subset=["ccaa"])
            .groupby("ccaa")["n"].sum()
            .reset_index()
            .sort_values("n", ascending=False)
        )
        if not df_ccaa_total.empty:
            _labels = df_ccaa_total["ccaa"].tolist()
            _values = df_ccaa_total["n"].tolist()
            _colors = [_TREEMAP_COLORS[i % len(_TREEMAP_COLORS)] for i in range(len(_labels))]
            fig_tm_ccaa = go.Figure(go.Treemap(
                labels=_labels,
                parents=[""] * len(_labels),
                values=_values,
                texttemplate="<b>%{label}</b><br>%{value:,}",
                marker=dict(colors=_colors, line=dict(width=1, color="#1a1a2e")),
                hovertemplate="<b>%{label}</b><br>Matriculaciones: %{value:,}<extra></extra>",
            ))
            fig_tm_ccaa.update_layout(
                title=dict(
                    text=f"Matriculaciones totales por CCAA · {fecha_txt}",
                    font=dict(size=14, color=TEXT), x=0.01,
                ),
                paper_bgcolor=BG,
                margin=dict(t=50, b=10, l=10, r=10),
                height=380,
            )
            st.plotly_chart(fig_tm_ccaa, use_container_width=True)

        st.divider()

        # ── 3. Evolución BEV share % por CCAA ──────────────────────────────
        agrupacion_ccaa = st.radio(
            "Agrupar por",
            ["Mes", "Trimestre", "Año"],
            horizontal=True,
            key="ccaa_agrup",
        )

        # Ordenar por BEV % desc para que el default sean las más avanzadas
        _ccaa_sorted_bev = (
            df_ccaa_map.sort_values("bev_pct", ascending=False)["ccaa"]
            .dropna().tolist()
        )
        all_ccaa = sorted(df_ccaa_map["ccaa"].dropna().unique().tolist())
        ccaa_sel = st.multiselect(
            "Comunidades Autónomas",
            all_ccaa,
            default=_ccaa_sorted_bev[:8] if len(_ccaa_sorted_bev) >= 8 else _ccaa_sorted_bev,
            key="ccaa_sel",
        )

        if not ccaa_sel:
            st.info("Selecciona al menos una comunidad autónoma.")
        else:
            _periodo_ccaa_sql = _periodo_sql(agrupacion_ccaa)
            df_evol_prov = pd.read_sql_query(
                f"""SELECT {_periodo_ccaa_sql} AS periodo, cod_provincia, cod_propulsion,
                           {_share_count} AS n
                    FROM {_share_table}
                    WHERE {_share_where} AND cod_provincia != ''
                    GROUP BY periodo, cod_provincia, cod_propulsion
                    ORDER BY periodo""",
                conn, params=_share_params,
            )

            if df_evol_prov.empty:
                st.info("No hay datos suficientes para mostrar la evolución.")
            else:
                df_evol_prov["ccaa"] = (
                    df_evol_prov["cod_provincia"]
                    .astype(str).str.strip()
                    .map(_PROV_CCAA)
                )
                df_evol_prov = df_evol_prov.dropna(subset=["ccaa"])
                df_evol_prov = df_evol_prov[df_evol_prov["ccaa"].isin(ccaa_sel)]

                df_evol_total = (
                    df_evol_prov.groupby(["periodo", "ccaa"])["n"]
                    .sum().reset_index().rename(columns={"n": "total"})
                )
                df_evol_bev = (
                    df_evol_prov[df_evol_prov["cod_propulsion"] == CODIGO_BEV]
                    .groupby(["periodo", "ccaa"])["n"]
                    .sum().reset_index().rename(columns={"n": "bev"})
                )
                df_evol_m = df_evol_total.merge(df_evol_bev, on=["periodo", "ccaa"], how="left")
                df_evol_m["bev"] = df_evol_m["bev"].fillna(0)
                df_evol_m["bev_pct"] = (
                    df_evol_m["bev"] / df_evol_m["total"].replace(0, 1) * 100
                )

                df_evol_pivot = (
                    df_evol_m.pivot(index="periodo", columns="ccaa", values="bev_pct")
                    .fillna(0)
                )

                fig_ccaa = go.Figure()
                for i, ccaa in enumerate(df_evol_pivot.columns):
                    color = _TREEMAP_COLORS[i % len(_TREEMAP_COLORS)]
                    fig_ccaa.add_trace(go.Scatter(
                        x=df_evol_pivot.index.tolist(),
                        y=df_evol_pivot[ccaa].round(2).tolist(),
                        name=ccaa,
                        mode="lines+markers",
                        line=dict(color=color, width=2),
                        marker=dict(size=4),
                    ))

                fig_ccaa.update_layout(
                    title=dict(
                        text=f"Evolución cuota BEV (%) por CCAA · {fecha_txt}",
                        font=dict(size=14, color=TEXT), x=0.01,
                    ),
                    xaxis=dict(
                        title=agrupacion_ccaa,
                        tickfont=dict(color=TEXT, size=10),
                        titlefont=dict(color=TEXT),
                        showgrid=False,
                        tickangle=-45,
                    ),
                    yaxis=dict(
                        title="BEV Share %",
                        ticksuffix="%",
                        tickfont=dict(color=TEXT),
                        titlefont=dict(color=TEXT),
                        showgrid=True, gridcolor=GRID,
                    ),
                    legend=dict(
                        font=dict(color=TEXT, size=10),
                        bgcolor="rgba(0,0,0,0)",
                        orientation="v",
                    ),
                    plot_bgcolor=BG_PLOT,
                    paper_bgcolor=BG,
                    height=560,
                    margin=dict(t=60, b=80, l=60, r=20),
                )
                st.plotly_chart(fig_ccaa, use_container_width=True)

# ── TAB 5: Acerca de ───────────────────────────────────────────────────────
with tab5:
    st.markdown("""
### ⚡ Matriculaciones EV España

*Dashboard interactivo para seguir la adopción del vehículo eléctrico en España — datos oficiales de la DGT*

---

#### Por qué existe esto

Seguía con mucho interés los análisis de ventas de [Luis Valdés](https://bsky.app/profile/luisvaldes.bsky.social)
en el canal [Todos Eléctricos](https://x.com/todoselectricos). Un día me topé con
[este hilo de @joseantonio_qr](https://x.com/joseantonio_qr/status/2030328421323067626?s=20)
donde quedaba claro que los gráficos de matriculaciones se construían a mano cada mes. Pensé: *los datos de la DGT son públicos,
¿se puede automatizar esto por completo?*

Este proyecto es la respuesta. Descarga automáticamente los microdatos oficiales de la DGT, los agrega
y los convierte en visualizaciones interactivas — sin intervención manual, sin suscripción, sin registro.

Aunque el foco está en los vehículos eléctricos, los microdatos de la DGT cubren **todos los tipos de
propulsión** — híbridos, gasolina, diésel, gas — y también motos, furgonetas y otros tipos de vehículo.
Todo eso está disponible en los filtros de la barra lateral.

---

#### Cómo funciona

```
DGT (dgt.es)  →  GitHub Actions (04:00 UTC)  →  GitHub Release  →  este dashboard  →  tú ⚡
```

Cada noche un workflow automático descarga los datos más recientes de la DGT, actualiza la base de datos
y la publica en GitHub Releases. Al abrir el dashboard, los datos ya están frescos.

---

#### Contribuir

Las contribuciones son bienvenidas:

- **Issues** para reportar errores o proponer nuevas visualizaciones
- **Pull requests** para mejoras de código o nuevas pestañas
- **Llamada especial a quienes siguen estos datos a mano**: si encuentras alguna cifra que no cuadra,
  abre un issue — tu ojo crítico tiene mucho valor y queda registrado en los créditos

Código en [github.com/literato1987/dgt-matriculaciones](https://github.com/literato1987/dgt-matriculaciones)

---

#### Créditos

**Creado por** Juan Clavel — [@rote_nelke](https://x.com/rote_nelke) en X · [@literato1987](https://github.com/literato1987) en GitHub

**Inspiración original**: [Luis Valdés](https://bsky.app/profile/luisvaldes.bsky.social) · canal [Todos Eléctricos](https://x.com/todoselectricos) — sus análisis semanales de ventas demostraron que había demanda real para estos datos.

**El detonante**: [hilo de @joseantonio_qr](https://x.com/joseantonio_qr/status/2030328421323067626?s=20) — ver que el proceso era manual fue lo que empujó a automatizarlo.

**Inspiración de visualizaciones**: [@electric_nick_](https://x.com/electric_nick_)

**Fuente de datos**: Dirección General de Tráfico (DGT), Ministerio del Interior de España.
Microdatos de dominio público disponibles en [dgt.es](https://www.dgt.es).

**Licencia**: MIT — úsalo, fórkalo, mejóralo.

---

#### ☕ Apoya el proyecto

Este proyecto es gratuito y seguirá siéndolo. Si te resulta útil y quieres ayudar a mantener
el servidor cuando escale, puedes invitarme a un café.

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/juanclavel)
""")

# ── Footer ─────────────────────────────────────────────────────────────────
st.caption(
    "Datos: Dirección General de Tráfico (DGT) España · "
    "Microdatos de matriculaciones diarias y mensuales · "
    "Dashboard de código abierto"
)
