"""
Captura de imágenes de rodilla → Google Drive
Sube capturas de pantalla (⌘V) organizadas por diagnóstico y nivel.
"""

import base64
import io
import os

import streamlit as st
import streamlit.components.v1 as components

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --------------------------------------------------------------------------
# Configuración general
# --------------------------------------------------------------------------
st.set_page_config(page_title="Captura de Rodilla", page_icon="🦵", layout="centered")

NIVELES = [1, 2, 3, 4]
DIAGNOSTICOS = {
    "Rodilla con EPL": "Rodilla-EPL",
    "Rodilla Sana": "Rodilla-Sana",
}
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Registrar el componente HTML de captura (carpeta capture_component/index.html)
_COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_component")
_capture = components.declare_component("capture_slot", path=_COMPONENT_DIR)


def capture_slot(label: str, key: str, value: str = "") -> str:
    """Renderiza una casilla de captura. Devuelve el dataURL de la imagen pegada (o '')."""
    result = _capture(label=label, value=value, key=key, default=value)
    return result or ""


# --------------------------------------------------------------------------
# Google Drive helpers
# --------------------------------------------------------------------------
@st.cache_resource
def get_drive_service():
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def find_or_create_folder(service, name: str, parent_id: str) -> str:
    """Devuelve el ID de la subcarpeta `name` dentro de `parent_id`, creándola si no existe."""
    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    resp = service.files().list(
        q=query, spaces="drive", fields="files(id, name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    folder = service.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
    return folder["id"]


def upload_or_replace_png(service, folder_id: str, filename: str, image_bytes: bytes) -> str:
    """Sube un PNG a `folder_id`. Si ya existe un archivo con ese nombre, lo sobrescribe."""
    safe_name = filename.replace("'", "\\'")
    query = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
    existing = service.files().list(
        q=query, spaces="drive", fields="files(id, name)",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])

    media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype="image/png", resumable=False)

    if existing:
        file_id = existing[0]["id"]
        service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
        return file_id
    metadata = {"name": filename, "parents": [folder_id]}
    created = service.files().create(
        body=metadata, media_body=media, fields="id", supportsAllDrives=True
    ).execute()
    return created["id"]


def dataurl_to_bytes(data_url: str) -> bytes:
    """Convierte un dataURL 'data:image/png;base64,...' en bytes crudos."""
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


# --------------------------------------------------------------------------
# Estado de sesión
# --------------------------------------------------------------------------
if "reset_counter" not in st.session_state:
    st.session_state.reset_counter = 0
if "ultimo_paciente" not in st.session_state:
    st.session_state.ultimo_paciente = None

rc = st.session_state.reset_counter  # se usa en las keys para forzar limpieza tras subir

# --------------------------------------------------------------------------
# Interfaz
# --------------------------------------------------------------------------
st.title("🦵 Captura de imágenes de rodilla")

if st.session_state.ultimo_paciente:
    st.success(f"✅ Último paciente subido: **{st.session_state.ultimo_paciente}**")

st.markdown(
    "Captura con **⌃⇧⌘4** (Control + Shift + Cmd + 4) → selecciona el área → "
    "haz clic en la casilla del nivel y pega con **⌘V**."
)

codigo = st.text_input("Código de paciente *", key=f"codigo_{rc}", placeholder="Ej: P001")

diagnostico_label = st.radio(
    "Tipo de rodilla *",
    options=list(DIAGNOSTICOS.keys()),
    index=None,
    key=f"diag_{rc}",
)

st.divider()
st.subheader("Capturas por nivel")
st.caption("No es obligatorio llenar todos los niveles.")

# Casillas de captura (2x2)
imagenes = {}
cols = st.columns(2)
for idx, nivel in enumerate(NIVELES):
    with cols[idx % 2]:
        data_url = capture_slot(
            label=f"Captura Nivel {nivel}",
            key=f"slot_{nivel}_{rc}",
        )
        imagenes[nivel] = data_url

st.divider()

# --------------------------------------------------------------------------
# Botón de subida
# --------------------------------------------------------------------------
if st.button("⬆️  Subir imágenes", type="primary", use_container_width=True):
    errores = []
    if not codigo or not codigo.strip():
        errores.append("El **código de paciente** es obligatorio.")
    if not diagnostico_label:
        errores.append("Debes elegir **Rodilla con EPL** o **Rodilla Sana**.")

    niveles_con_imagen = {n: d for n, d in imagenes.items() if d}
    if not niveles_con_imagen:
        errores.append("Debes pegar al menos **una** captura.")

    if errores:
        for e in errores:
            st.error(e)
    else:
        codigo_limpio = codigo.strip()
        carpeta_diag = DIAGNOSTICOS[diagnostico_label]
        try:
            with st.spinner("Subiendo a Google Drive…"):
                service = get_drive_service()
                root_id = st.secrets["drive"]["root_folder_id"]

                # Carpeta del diagnóstico (Rodilla-EPL / Rodilla-Sana)
                diag_folder_id = find_or_create_folder(service, carpeta_diag, root_id)

                subidos = []
                for nivel, data_url in sorted(niveles_con_imagen.items()):
                    nivel_folder_id = find_or_create_folder(service, f"Nivel {nivel}", diag_folder_id)
                    filename = f"{codigo_limpio}_{carpeta_diag}_Nivel{nivel}.png"
                    image_bytes = dataurl_to_bytes(data_url)
                    upload_or_replace_png(service, nivel_folder_id, filename, image_bytes)
                    subidos.append(filename)

            st.success(f"Se subieron {len(subidos)} imagen(es):")
            for f in subidos:
                st.write(f"• {f}")

            # Reiniciar interfaz para el próximo paciente
            st.session_state.ultimo_paciente = codigo_limpio
            st.session_state.reset_counter += 1
            st.rerun()

        except Exception as ex:  # noqa: BLE001
            st.error("Ocurrió un error al subir a Google Drive:")
            st.exception(ex)
