# ⚡ Matriculaciones EV España

*Dashboard interactivo para seguir la adopción del vehículo eléctrico en España — datos oficiales de la DGT*

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://literato1987-dgt-matriculaciones.streamlit.app)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Licencia MIT](https://img.shields.io/badge/licencia-MIT-green.svg)](LICENSE)
[![Datos actualizados](https://img.shields.io/badge/datos-actualizaci%C3%B3n%20diaria-brightgreen.svg)](https://github.com/literato1987/dgt-matriculaciones/releases)

<!-- screenshot -->
> *Captura del dashboard — próximamente*

---

## Por qué existe esto

Seguía los análisis de [@todoselectricos](https://bsky.app/profile/luisvaldes.bsky.social) con mucho interés. Un día vi [este hilo en X](https://x.com/joseantonio_qr/status/2030328421323067626?s=20) donde quedaba claro que los gráficos de matriculaciones se construían a mano cada mes. Pensé: *los datos de la DGT son públicos, ¿se puede automatizar esto por completo?*

Este proyecto es la respuesta. Descarga automáticamente los microdatos oficiales de la DGT, los agrega y los convierte en visualizaciones interactivas — sin intervención manual, sin suscripción, sin registro.

Los datos llevan años disponibles en [dgt.es](https://www.dgt.es) en forma de ficheros ZIP, pero sin ninguna herramienta visual que los haga útiles para el ciudadano. Este dashboard cierra ese hueco.

---

## Qué puedes ver

| Pestaña | Contenido |
|---------|-----------|
| **Ranking** | Pareto de marcas y modelos más matriculados + treemaps de distribución |
| **Evolución temporal** | Barras apiladas por marca/modelo a lo largo del tiempo (mes, trimestre, año) |
| **BEV Share** | Cuota eléctrica (BEV / PHEV / combustión) con línea de tendencia % |
| **Comunidades** | Mapa choropleth por provincia, ranking CCAA y evolución del % BEV por región |

Filtros disponibles en la barra lateral: rango de fechas, tipo de propulsión, tipo de vehículo, solo nuevos/usados, top N marcas y modelos.

---

## Cómo funciona

```
DGT (dgt.es)
    │  Microdatos diarios y mensuales (ZIP)
    ▼
GitHub Actions  ─── cada noche a las 04:00 UTC
  update_cloud_db.py
    │  Últimos 3 meses → datos_dgt_cloud.db (~145 MB, 2017–hoy)
    ▼
GitHub Releases v1.0.0
    │  Descarga automática en el primer arranque del dashboard
    ▼
Streamlit Community Cloud
    │  dashboard_dgt.py
    ▼
Tú, explorando los datos ⚡
```

- **`dgt_matriculaciones.py`** — descarga y parsea los ficheros de la DGT
- **`cache_db.py`** — gestión de la base de datos SQLite local
- **`build_cloud_db.py`** — genera la DB agregada para el despliegue en cloud
- **`update_cloud_db.py`** — actualización incremental diaria (usado por GitHub Actions)

---

## Instalación local

```bash
git clone https://github.com/literato1987/dgt-matriculaciones.git
cd dgt-matriculaciones
pip install -r requirements.txt
streamlit run dashboard_dgt.py
```

En la primera ejecución, el dashboard descarga automáticamente la base de datos desde GitHub Releases (~145 MB, tarda unos 30 segundos). A partir de ahí arranca en segundos.

Si tienes los datos históricos de la DGT en local (`datos_dgt.db`), el dashboard los usa directamente y te permite actualizar desde la propia interfaz.

---

## Contribuir

Las contribuciones son bienvenidas en cualquier forma:

- **Issues** para reportar errores o proponer nuevas visualizaciones
- **Pull requests** para mejoras de código, nuevas pestañas o correcciones
- No hace falta ser experto — la base de datos es SQLite estándar y los datos son fáciles de explorar

**Llamada especial a quienes siguen estos datos de forma manual**: si encuentras alguna cifra que no cuadra con lo que ves en otras fuentes, abre un issue. Tu ojo crítico tiene mucho valor para mejorar la fiabilidad del proyecto, y queda registrado tu nombre en los créditos.

Todos los colaboradores aparecen en este README con enlace a su perfil.

---

## Créditos e inspiración

**Creado por** Juan Clavel — [@rote_nelke](https://x.com/rote_nelke) en X · [@literato1987](https://github.com/literato1987) en GitHub

**Inspiración de visualizaciones**: [@electric_nick_](https://x.com/electric_nick_) — referencia para el estilo y tipo de gráficos del dashboard.

**Fuente de datos**: Dirección General de Tráfico (DGT), Ministerio del Interior de España. Los microdatos de matriculaciones son de dominio público y están disponibles en [dgt.es](https://www.dgt.es).

**Licencia**: MIT — úsalo, fórkalo, mejóralo. Si lo usas en un artículo, vídeo o análisis, una mención siempre se agradece.

---

## ☕ Apoya el proyecto

Este proyecto es gratuito y seguirá siéndolo. Si te resulta útil y quieres ayudar a mantener el servidor cuando escale, puedes invitarme a un café — cada aportación ayudará a mantener la infraestructura.

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/juanclavel)

---

*Datos: DGT España · Actualización diaria automática · Código abierto bajo licencia MIT*
