# 🟦🟥 Bot Scraping FC Barcelona → Cloud Firestore

Bot en Python que extrae automáticamente los partidos y resultados del FC Barcelona desde ESPN y Marca, y los sube a Cloud Firestore para alimentar tu app Flutter (`fl_predicciones`).

## 🏗️ Arquitectura

```
ESPN (API JSON)  ─┐
                  ├─→ Normalización + IDs únicos ─→ Cloud Firestore
Marca (HTML)    ─┘         (deduplicación)          (merge=True)
```

- **ESPN**: Fuente primaria. API JSON informal sin autenticación.
- **Marca**: Fuente secundaria. Scraping HTML con `cloudscraper` + `BeautifulSoup`.
- **Firestore**: `set(merge=True)` para no sobrescribir campos de tu app Flutter.

## 📦 Estructura del Proyecto

```
lucid-borg/
├── scraper/
│   ├── __init__.py
│   ├── main.py              # Entry point
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── espn.py          # Fuente 1: ESPN API JSON
│   │   └── marca.py         # Fuente 2: Marca HTML
│   ├── normalizer.py        # IDs determinísticos + fusión
│   └── firestore_client.py  # Upload a Firestore
├── requirements.txt
├── .github/workflows/
│   └── scraper.yml          # GitHub Actions cada 6 horas
├── .gitignore
└── README.md
```

---

## 🚀 Instalación y Configuración (Paso a Paso)

### Paso 1: Clonar el repositorio

```bash
git clone https://github.com/TU_USUARIO/lucid-borg.git
cd lucid-borg
```

### Paso 2: Crear un entorno virtual e instalar dependencias

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### Paso 3: Configurar Firebase

#### 3.1 Crear un proyecto Firebase (si no tienes uno)

1. Ve a [Firebase Console](https://console.firebase.google.com/)
2. Haz clic en **"Agregar proyecto"**
3. Ponle un nombre (ej. `fl-predicciones`)
4. Desactiva Google Analytics si no lo necesitas
5. Haz clic en **"Crear proyecto"**

#### 3.2 Habilitar Cloud Firestore

1. En tu proyecto Firebase, ve a **"Firestore Database"** en el menú lateral
2. Haz clic en **"Crear base de datos"**
3. Selecciona el modo:
   - **Modo de producción** → configura reglas después
   - **Modo de prueba** → para empezar rápido (caduca en 30 días)
4. Selecciona la ubicación más cercana (ej. `europe-west1` para España)
5. Haz clic en **"Crear"**

#### 3.3 Generar `serviceAccountKey.json`

1. En Firebase Console, ve a **⚙️ Configuración del proyecto** (icono de engranaje)
2. Pestaña **"Cuentas de servicio"**
3. Sección **"Firebase Admin SDK"**
4. Haz clic en **"Generar nueva clave privada"**
5. Descarga el archivo JSON
6. **Renómbralo** a `serviceAccountKey.json`
7. **Muévelo** a la raíz del proyecto `lucid-borg/`

> ⚠️ **NUNCA subas este archivo a GitHub**. Ya está en `.gitignore`.

### Paso 4: Ejecutar localmente

```bash
# Dry run (muestra datos sin escribir en Firestore)
python -m scraper.main --dry-run

# Ejecución real (sube datos a Firestore)
python -m scraper.main

# Solo fuente ESPN
python -m scraper.main --source espn

# Solo fuente Marca
python -m scraper.main --source marca
```

---

## ☁️ Configurar GitHub Actions (CI/CD)

### Paso 1: Codificar credenciales en Base64

```bash
# macOS/Linux
base64 -i serviceAccountKey.json | tr -d '\n'

# Windows (PowerShell)
[Convert]::ToBase64String([IO.File]::ReadAllBytes("serviceAccountKey.json"))
```

Copia el resultado (una cadena larga de texto).

### Paso 2: Crear el secreto en GitHub

1. Ve a tu repositorio en GitHub
2. **Settings** → **Secrets and variables** → **Actions**
3. Haz clic en **"New repository secret"**
4. Nombre: `FIREBASE_SERVICE_ACCOUNT_KEY`
5. Valor: pega la cadena Base64 del paso anterior
6. Haz clic en **"Add secret"**

### Paso 3: Push y activar

```bash
git add .
git commit -m "feat: bot scraping FC Barcelona → Firestore"
git push origin main
```

El workflow se ejecutará:
- **Automáticamente** cada 6 horas (00:00, 06:00, 12:00, 18:00 UTC)
- **Manualmente** desde la pestaña "Actions" → "Scraper FC Barcelona" → "Run workflow"

---

## 📄 Modelo de Datos en Firestore

**Colección**: `partidos_barca`

Cada documento tiene este formato:

```json
{
  "id": "liga_20260510_vs_real_madrid",
  "rival": "Real Madrid",
  "rival_logo": "https://a.espncdn.com/i/teamlogos/soccer/500/86.png",
  "fecha": "2026-05-10T19:00:00Z",
  "competicion": "La Liga",
  "temporada": "2025-26 Spanish LALIGA",
  "es_local": true,
  "estadio": "Spotify Camp Nou",
  "estado": "finalizado",
  "goles_barca": 2,
  "goles_rival": 0,
  "marcador": "2-0",
  "minuto": null,
  "fuente": "espn",
  "espn_event_id": "748484",
  "actualizado_en": "2026-08-30T13:44:00+00:00"
}
```

### Estados posibles

| Estado | Descripción | Uso en Flutter |
|--------|-------------|----------------|
| `programado` | Partido aún no jugado | Mostrar countdown, permitir predicciones |
| `en_vivo` | En juego ahora mismo | Mostrar marcador en tiempo real |
| `finalizado` | Ya terminó | Mostrar resultado, calcular puntos |
| `aplazado` | Pospuesto | Mostrar aviso, deshabilitar predicciones |
| `cancelado` | Cancelado | Mostrar aviso |

---

## 🔗 Integración con Flutter (`fl_predicciones`)

### Leer partidos desde Flutter (Dart)

```dart
import 'package:cloud_firestore/cloud_firestore.dart';

// Obtener próximos partidos (ordenados por fecha)
Stream<QuerySnapshot> getProximosPartidos() {
  return FirebaseFirestore.instance
    .collection('partidos_barca')
    .where('estado', isEqualTo: 'programado')
    .orderBy('fecha')
    .limit(10)
    .snapshots();
}

// Obtener resultados recientes
Stream<QuerySnapshot> getResultados() {
  return FirebaseFirestore.instance
    .collection('partidos_barca')
    .where('estado', isEqualTo: 'finalizado')
    .orderBy('fecha', descending: true)
    .limit(10)
    .snapshots();
}

// Modelo Dart para el partido
class Partido {
  final String id;
  final String rival;
  final String? rivalLogo;
  final DateTime fecha;
  final String competicion;
  final bool esLocal;
  final String estado;
  final int? golesBarca;
  final int? golesRival;
  final String? marcador;

  Partido.fromFirestore(DocumentSnapshot doc)
    : id = doc.id,
      rival = doc['rival'] ?? '',
      rivalLogo = doc['rival_logo'],
      fecha = DateTime.parse(doc['fecha']),
      competicion = doc['competicion'] ?? '',
      esLocal = doc['es_local'] ?? true,
      estado = doc['estado'] ?? 'programado',
      golesBarca = doc['goles_barca'],
      golesRival = doc['goles_rival'],
      marcador = doc['marcador'];
}
```

> **Nota sobre `merge=True`**: El scraper usa `set(merge=True)`, así que puedes
> añadir campos propios a cada documento (ej. `predicciones`, `likes`) desde tu
> app Flutter sin miedo a que el scraper los borre.

---

## ⚙️ Competiciones cubiertas

El bot extrae partidos de:
- 🇪🇸 **La Liga** (LALIGA)
- 🏆 **Champions League** (UEFA Champions League)
- 🏆 **Copa del Rey**
- 🏆 **Supercopa de España**
- 🏆 **Supercopa de Europa**
- 🌍 **Mundial de Clubes** (FIFA Club World Cup)

---

## 🧪 Troubleshooting

### El scraper no encuentra datos de Marca
Es normal. Marca usa protecciones Cloudflare modernas que `cloudscraper` no siempre supera. ESPN es la fuente primaria y no falla.

### Error de credenciales Firebase
Verifica que:
1. El archivo `serviceAccountKey.json` existe y es válido
2. En GitHub Actions, el secreto `FIREBASE_SERVICE_ACCOUNT_KEY` contiene el Base64 correcto
3. El proyecto Firebase tiene Firestore habilitado

### Los datos no aparecen en Firestore
1. Ejecuta con `--dry-run` primero para verificar que hay datos
2. Verifica las reglas de seguridad de Firestore (deben permitir escritura desde el Admin SDK)
3. Revisa los logs del workflow en GitHub Actions
