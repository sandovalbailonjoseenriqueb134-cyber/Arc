import glob
import json
import math
import os
import sys

try:
  import ezdxf
except ImportError:
  print(
      "Error: El paquete 'ezdxf' no está disponible. Ejecuta 'pip install"
      " ezdxf'."
  )
  sys.exit(1)

try:
  from arc_brain import clasificar_capas_con_groq
except ImportError:
  clasificar_capas_con_groq = None

try:
  from arc_exporter import generar_reporte_excel
except ImportError:
  generar_reporte_excel = None

ALTURAS_ESTANDAR = {
    'Cimentación': 0.50,
    'Columnas': 2.80,
    'Vigas/Trabes': 0.40,
    'Muros': 2.50,
    'Anotaciones/Detalles': 0.00,
    'Acero/Refuerzo': 0.00,
    'Desconocido': 0.00,
}

CATALOGO_PRECIOS_BASE = {
    'Cimentación': 2800.00,  # $/m3
    'Columnas': 3200.00,  # $/m3
    'Vigas/Trabes': 3000.00,  # $/m3
    'Muros': 450.00,  # $/m2
    'Acero/Refuerzo': 35.00,  # $/m o $/kg
    'Anotaciones/Detalles': 0.00,  # Sin costo
    'Desconocido': 0.00,
}


def calcular_area_poligono(puntos):
  n = len(puntos)
  if n < 3:
    return 0.0
  area = 0.0
  for i in range(n):
    j = (i + 1) % n
    area += puntos[i][0] * puntos[j][1]
    area -= puntos[j][0] * puntos[i][1]
  return abs(area) / 2.0


def calcular_perimetro_poligono(puntos, cerrado=True):
  n = len(puntos)
  if n < 2:
    return 0.0
  perimetro = 0.0
  for i in range(n - 1):
    perimetro += math.hypot(
        puntos[i + 1][0] - puntos[i][0], puntos[i + 1][1] - puntos[i][1]
    )
  if cerrado and n > 2:
    perimetro += math.hypot(
        puntos[0][0] - puntos[-1][0], puntos[0][1] - puntos[-1][1]
    )
  return perimetro


def determinar_factor_escala(doc):
  insunits = doc.header.get('$INSUNITS', 0)
  if insunits == 4:
    print('   📐 Unidades detectadas en DXF: MILÍMETROS (mm)')
    return 1e-6, 1e-3
  elif insunits == 5:
    print('   📐 Unidades detectadas en DXF: CENTÍMETROS (cm)')
    return 1e-4, 1e-2
  elif insunits == 6:
    print('   📐 Unidades detectadas en DXF: METROS (m)')
    return 1.0, 1.0

  print('\n[?] Plano sin metadatos de unidad ($INSUNITS = 0).')
  print('    [1] Milímetros (mm) <- Estándar estructural')
  print('    [2] Centímetros (cm)')
  print('    [3] Metros (m)')
  opcion = input('\nIngresa opción [1-3] (por defecto 1): ').strip()

  if opcion == '2':
    return 1e-4, 1e-2
  elif opcion == '3':
    return 1.0, 1.0
  else:
    return 1e-6, 1e-3


def procesar_plano_arc(ruta_dxf):
  if not os.path.exists(ruta_dxf):
    print(f"Error: No existe el archivo '{ruta_dxf}'.")
    return None, None, None

  try:
    doc = ezdxf.readfile(ruta_dxf)
  except Exception as e:
    print(f'Error al leer DXF: {e}')
    return None, None, None

  factor_area, factor_longitud = determinar_factor_escala(doc)
  msp = doc.modelspace()
  elementos = []
  capas_resumen = {}
  textos_plano = []

  for entity in msp.query('TEXT MTEXT DIMENSION'):
    txt = ''
    if entity.dxftype() == 'TEXT':
      txt = entity.dxf.text
    elif entity.dxftype() == 'MTEXT':
      txt = entity.text
    elif entity.dxftype() == 'DIMENSION':
      txt = getattr(entity.dxf, 'user_text', '') or ''

    txt_clean = txt.strip()
    if txt_clean and len(txt_clean) < 100:
      textos_plano.append(txt_clean)

  for entity in msp.query('LWPOLYLINE POLYLINE'):
    layer = entity.dxf.layer
    puntos = []

    if entity.dxftype() == 'LWPOLYLINE':
      puntos = [(p[0], p[1]) for p in entity.get_points('xy')]
    elif entity.dxftype() == 'POLYLINE':
      puntos = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]

    if getattr(entity, 'is_closed', False) and len(puntos) >= 3:
      area_m2 = calcular_area_poligono(puntos) * factor_area
      perimetro_m = (
          calcular_perimetro_poligono(puntos, cerrado=True) * factor_longitud
      )

      if area_m2 > 0:
        elementos.append({
            'capa': layer,
            'area_m2': round(area_m2, 4),
            'perimetro_m': round(perimetro_m, 4),
        })

        if layer not in capas_resumen:
          capas_resumen[layer] = {
              'cantidad_elementos': 0,
              'area_total_m2': 0.0,
              'perimetro_total_m': 0.0,
          }
        capas_resumen[layer]['cantidad_elementos'] += 1
        capas_resumen[layer]['area_total_m2'] = round(
            capas_resumen[layer]['area_total_m2'] + area_m2, 4
        )
        capas_resumen[layer]['perimetro_total_m'] = round(
            capas_resumen[layer]['perimetro_total_m'] + perimetro_m, 4
        )

  return elementos, capas_resumen, textos_plano


def gestionar_peraltes_faltantes(clasificacion_ia):
  if not clasificacion_ia:
    return clasificacion_ia

  peraltes_incompletos = False
  for item in clasificacion_ia:
    cat = item.get('categoria', 'Desconocido')
    val_ia = item.get('peralte_m')

    if val_ia is None or val_ia <= 0:
      estandar = ALTURAS_ESTANDAR.get(cat, 0.0)
      item['peralte_m'] = estandar
      if estandar > 0:
        peraltes_incompletos = True
        item['explicacion'] += (
            f' (Peralte de {estandar}m asignado por criterio estándar).'
        )

  if peraltes_incompletos:
    print(
        '\n⚠️  Atención: No se detectaron especificaciones de altura explícitas'
        ' en el DXF.'
    )
    print('   Se aplicarán los peraltes predeterminados de ingeniería civil:')
    for item in clasificacion_ia:
      if item.get('peralte_m', 0) > 0:
        print(
            f"   • Capa '{item.get('capa')}': [{item.get('categoria')}] ->"
            f' Peralte = {item.get("peralte_m")} m'
        )

    resp = (
        input(
            '\n¿Deseas continuar con estos peraltes predeterminados? [S/n]: '
        )
        .strip()
        .lower()
    )
    if resp in ['n', 'no']:
      for item in clasificacion_ia:
        if item.get('categoria') not in [
            'Anotaciones/Detalles',
            'Acero/Refuerzo',
        ]:
          try:
            nuevo_p = input(
                f"Ingresa peralte para capa '{item.get('capa')}' (Actual:"
                f" {item.get('peralte_m')}m): "
            )
            if nuevo_p.strip():
              item['peralte_m'] = float(nuevo_p)
          except ValueError:
            pass

  return clasificacion_ia


def gestionar_precios_unitarios(resultado_ia):
  """Muestra el catálogo de precios por categoría y permite editar valores si el usuario lo desea."""
  precios = CATALOGO_PRECIOS_BASE.copy()

  categorias_presentes = set(
      item.get('categoria')
      for item in resultado_ia
      if item.get('categoria')
      not in ['Anotaciones/Detalles', 'Desconocido']
  )

  print('\n💵 Catálogo de Precios Unitarios Base ($ MXN):')
  for cat in categorias_presentes:
    pu = precios.get(cat, 0.0)
    print(f'   • {cat}: ${pu:,.2f}')

  resp = (
      input(
          '\n¿Deseas ajustar algún precio unitario antes de generar el'
          ' presupuesto? [s/N]: '
      )
      .strip()
      .lower()
  )

  if resp in ['s', 'si']:
    print('\n--- Edición de Precios Unitarios ---')
    for cat in categorias_presentes:
      val_actual = precios.get(cat, 0.0)
      try:
        nuevo_val = input(
            f'Ingresa PU para "{cat}" (Actual: ${val_actual:,.2f}): '
        )
        if nuevo_val.strip():
          precios[cat] = float(nuevo_val)
      except ValueError:
        print('   Valor no válido, se conserva el anterior.')

  return precios


def seleccionar_archivo_dxf():
  if len(sys.argv) > 1:
    return sys.argv[1]
  archivos_dxf = glob.glob('*.dxf')
  if not archivos_dxf:
    print('\n[!] No se encontraron archivos .dxf en la carpeta actual.')
    return None
  if len(archivos_dxf) == 1:
    return archivos_dxf[0]

  print('\n📂 Archivos DXF detectados:')
  for i, archivo in enumerate(archivos_dxf, 1):
    print(f'  [{i}] {archivo}')
  try:
    eleccion = int(
        input('\nIngresa el número del plano que deseas procesar: ')
    )
    if 1 <= eleccion <= len(archivos_dxf):
      return archivos_dxf[eleccion - 1]
  except ValueError:
    pass
  return None


# --- PIPELINE PRINCIPAL DE ARC ---
if __name__ == '__main__':
  API_KEY_GROQ = os.environ.get("GROQ_API_KEY", "")

  print('====================================================')
  print('          ARC ENGINE & BRAIN & EXPORTER             ')
  print('====================================================')

  archivo_prueba = seleccionar_archivo_dxf()

  if archivo_prueba:
    print(f'\n📌 Procesando plano: {archivo_prueba}')

    elementos, resumen_capas, textos_plano = procesar_plano_arc(archivo_prueba)

    if elementos and resumen_capas:
      print(
          f'\n[1/3] ✔ Geometría procesada: {len(elementos)} polígonos en'
          f' {len(resumen_capas)} capas.'
      )

      resultado_ia = None
      if clasificar_capas_con_groq:
        print('\n[2/3] 🧠 Ejecutando análisis estructural e IA...')
        resultado_ia = clasificar_capas_con_groq(
            resumen_capas, textos_plano, api_key=API_KEY_GROQ
        )
        resultado_ia = gestionar_peraltes_faltantes(resultado_ia)

      if generar_reporte_excel and resultado_ia:
        # Módulo de Costos
        catalogo_precios = gestionar_precios_unitarios(resultado_ia)

        print('\n[3/3] 📄 Generando reporte cuantificado y presupuesto...')
        nombre_base = os.path.splitext(os.path.basename(archivo_prueba))[0]
        nombre_excel = f'Reporte_Cuantificacion_{nombre_base}.xlsx'

        generar_reporte_excel(
            resumen_capas, resultado_ia, catalogo_precios, nombre_excel
        )
        print(
            '====================================================\n🚀 ¡PRESUPUESTO'
            ' Y CUANTIFICACIÓN COMPLETADOS CON'
            ' ÉXITO!\n===================================================='
        )
      else:
        print('\n[!] No se pudo generar el reporte.')
    else:
      print(f"\n[!] No se encontraron geometrías en '{archivo_prueba}'.")