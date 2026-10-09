import json
import os

try:
  from groq import Groq
except ImportError:
  Groq = None


def clasificar_capas_con_groq(resumen_capas, textos_plano=None, api_key=None):
  """Clasifica capas, infiere peraltes y detecta ruido/capas no constructivas."""
  if Groq is None:
    print(
        "\n[!] Error: El paquete 'groq' no está instalado. Ejecuta 'pip install"
        " groq'."
    )
    return None

  clave_api = api_key or os.environ.get("GROQ_API_KEY")
  if not clave_api:
    print("\n[!] Error: No se encontró la clave API de Groq (GROQ_API_KEY).")
    return None

  client = Groq(api_key=clave_api)

  try:
    modelos_disponibles = client.models.list().data
    modelos_texto = [
        m.id
        for m in modelos_disponibles
        if not any(
            x in m.id for x in ["whisper", "guard", "orpheus", "vision"]
        )
    ]

    modelos_preferidos = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ]
    modelos_a_probar = [m for m in modelos_preferidos if m in modelos_texto]
    modelos_a_probar += [m for m in modelos_texto if m not in modelos_a_probar]

    if not modelos_a_probar:
      print(
          "\n[!] No se encontraron modelos de texto activos en la API de Groq."
      )
      return None

  except Exception as e:
    print(f"\n[!] Error al obtener la lista de modelos de Groq: {e}")
    modelos_a_probar = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

  lista_textos_str = (
      json.dumps(textos_plano[:80], ensure_ascii=False)
      if textos_plano
      else "[]"
  )

  prompt = f"""
    Analiza el resumen de capas y las anotaciones encontradas en un plano CAD de ingeniería civil.

    RESUMEN DE CAPAS:
    {json.dumps(resumen_capas, indent=2, ensure_ascii=False)}

    NOTAS EXTRAÍDAS DEL PLANO:
    {lista_textos_str}

    TAREAS:
    1. Clasificar cada capa en una categoría estructural (Cimentación, Columnas, Vigas/Trabes, Muros, Acero/Refuerzo, Anotaciones/Detalles).
    2. Identificar si la capa corresponde a RUIDO/ELEMENTOS NO CONSTRUCTIVOS (ej. membrete, recuadros de plano, textos de revisiones, cotas, proyecciones gráficas).
       - Asigna "es_ruido": true si NO representa un volumen u obra física real a cotizar.
       - Asigna "es_ruido": false si es un elemento físico de la obra.
    3. Inferir peralte_m (en metros) si está en los textos, o asigna null si no es explícito.

    Responde exclusivamente en un JSON estricto con esta estructura:
    {{
      "clasificacion": [
        {{
          "capa": "nombre_de_la_capa",
          "categoria": "Categoría Estructural o Anotaciones/Detalles",
          "unidad_medida": "m3 | m2 | m | kg | pza",
          "peralte_m": 0.50 o null,
          "es_ruido": true o false,
          "explicacion": "Criterio de clasificación y justificación de si es ruido/elemento no constructivo."
        }}
      ]
    }}
    """

  print(
      "\n🧠 Arc Brain: Analizando elementos estructurales vs. ruido gráfico..."
  )

  for modelo in modelos_a_probar:
    try:
      print(f"   -> Conectando con modelo activo: {modelo}...")
      response = client.chat.completions.create(
          model=modelo,
          messages=[
              {
                  "role": "system",
                  "content": (
                      "Eres un Ingeniero Civil experto en cuantificación de"
                      " obra y auditoría de planos CAD. Responde"
                      " exclusivamente en formato JSON estricto."
                  ),
              },
              {"role": "user", "content": prompt},
          ],
          response_format={"type": "json_object"},
          temperature=0.1,
      )

      res_text = response.choices[0].message.content
      datos = json.loads(res_text)

      print(f"   ✔ Análisis de capas completado con éxito usando '{modelo}'.\n")
      if isinstance(datos, dict):
        return datos.get("clasificacion", datos.get("capas", datos))
      return datos

    except Exception as e:
      print(f"   [!] Error en {modelo}: {e}")
      continue

  print("\n[!] Ningún modelo de Groq pudo procesar la solicitud.")
  return None