import os
import tempfile
import pandas as pd
import streamlit as st

# Módulos core de Arc
from Arc import procesar_plano_arc
from arc_brain import clasificar_capas_con_groq
from arc_exporter import generar_reporte_excel

# --- GESTIÓN SEGURA DE LA API KEY ---
# Primero busca en los Secretos de Streamlit Cloud (st.secrets), luego en variables de entorno,
# y finalmente usa la clave de desarrollo como respaldo local.
CLAVE_LOCAL_DEV = "TU_GROQ_API_KEY_AQUI"

try:
  API_KEY_GROQ = st.secrets.get(
      "GROQ_API_KEY", os.environ.get("GROQ_API_KEY", CLAVE_LOCAL_DEV)
  )
except Exception:
  API_KEY_GROQ = os.environ.get("GROQ_API_KEY", CLAVE_LOCAL_DEV)

# --- CONFIGURACIÓN DE LA PÁGINA STREAMLIT ---
st.set_page_config(
    page_title="Arc - Civil Engineering CAD Quantifier",
    page_icon="🏗️",
    layout="wide",
)

st.title("🏗️ Arc: Cuantificación Automática & Presupuesto CAD")
st.markdown(
    "Carga tu archivo **.DXF** para realizar la extracción geométrica,"
    " clasificación con IA, cálculo de volúmenes y generación de presupuesto."
)

# --- BARRA LATERAL: CARGA Y CONFIGURACIÓN ---
st.sidebar.header("⚙️ Configuración del Proyecto")

uploaded_file = st.sidebar.file_uploader(
    "Selecciona un archivo DXF", type=["dxf"]
)

# Menú de unidades si el DXF no las especifica
unidad_opcion = st.sidebar.selectbox(
    "Unidad de dibujo del plano (si no se auto-detecta):",
    options=["Milímetros (mm)", "Centímetros (cm)", "Metros (m)"],
    index=0,
)

if uploaded_file is not None:
  # Guardar archivo subido temporalmente para procesarlo con ezdxf
  with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp_file:
    tmp_file.write(uploaded_file.getvalue())
    tmp_path = tmp_file.name

  st.sidebar.success(f"Archivo cargado: {uploaded_file.name}")

  # Botón principal para ejecutar el pipeline
  if st.sidebar.button("🚀 Procesar Plano con Arc"):
    with st.spinner(
        "Procesando geometría, extrayendo textos y analizando con Groq IA..."
    ):
      elementos, resumen_capas, textos_plano = procesar_plano_arc(tmp_path)

      if elementos and resumen_capas:
        # Clasificación IA
        resultado_ia = clasificar_capas_con_groq(
            resumen_capas, textos_plano, api_key=API_KEY_GROQ
        )

        # Guardar resultados en el estado de sesión de Streamlit para no recomputar en cada interacción
        st.session_state["resumen_capas"] = resumen_capas
        st.session_state["resultado_ia"] = resultado_ia
        st.session_state["nombre_plano"] = os.path.splitext(uploaded_file.name)[
            0
        ]
        st.success("¡Análisis completado exitosamente!")
      else:
        st.error(
            "No se encontraron geometrías cerradas válidas en el archivo DXF."
        )

  # Borrar archivo temporal de la memoria
  try:
    os.remove(tmp_path)
  except Exception:
    pass

# --- PANEL PRINCIPAL: AJUSTES INTERACTIVOS Y VISTA PREVIA ---
if "resultado_ia" in st.session_state and st.session_state["resultado_ia"]:
  resumen_capas = st.session_state["resumen_capas"]
  resultado_ia = st.session_state["resultado_ia"]

  st.divider()
  st.subheader("📊 Ajuste de Peraltes y Precios Unitarios")

  tab1, tab2 = st.tabs(
      ["📐 Peraltes / Alturas (m)", "💵 Catálogo Precios ($ MXN)"]
  )

  with tab1:
    st.caption(
        "Modifica los peraltes ($m$) inferidos por la IA o asignados por"
        " criterio estándar:"
    )
    cols = st.columns(3)
    idx = 0
    for item in resultado_ia:
      if item.get("categoria") not in [
          "Anotaciones/Detalles",
          "Acero/Refuerzo",
      ]:
        with cols[idx % 3]:
          val_def = float(item.get("peralte_m") or 0.0)
          nuevo_p = st.number_input(
              f"Peralte {item.get('capa')} ({item.get('categoria')}):",
              value=val_def,
              step=0.05,
              key=f"p_{item.get('capa')}",
          )
          item["peralte_m"] = nuevo_p
        idx += 1

  with tab2:
    st.caption("Ajusta los Precios Unitarios Base por Categoría Estructural:")
    cats_unicas = list(
        set(
            item.get("categoria")
            for item in resultado_ia
            if item.get("categoria")
            not in ["Anotaciones/Detalles", "Desconocido"]
        )
    )
    precios_editados = {}
    cols_p = st.columns(3)
    for i, cat in enumerate(cats_unicas):
      with cols_p[i % 3]:
        pu_base = (
            2800.0 if "Ciment" in cat else (3200.0 if "Colum" in cat else 450.0)
        )
        val_pu = st.number_input(
            f"Precio Unitario {cat} ($/unidad):",
            value=pu_base,
            step=50.0,
            key=f"pu_{cat}",
        )
        precios_editados[cat] = val_pu

  # --- VISTA PREVIA DE CUANTIFICACIÓN Y EXPORTACIÓN ---
  st.divider()
  st.subheader("📑 Resumen de Cuantificación Estimada")

  # Generación del archivo Excel
  nombre_excel = (
      f"Reporte_Cuantificacion_{st.session_state['nombre_plano']}.xlsx"
  )
  ruta_excel = generar_reporte_excel(
      resumen_capas, resultado_ia, precios_editados, nombre_excel
  )

  if ruta_excel and os.path.exists(ruta_excel):
    with open(ruta_excel, "rb") as f:
      excel_bytes = f.read()

    st.download_button(
        label="📥 Descargar Reporte Completo en Excel (.xlsx)",
        data=excel_bytes,
        file_name=nombre_excel,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )