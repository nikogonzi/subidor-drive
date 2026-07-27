"""
Captura de imágenes de rodilla -> Google Drive (OAuth, cuenta personal).
Sube capturas de pantalla (Cmd+V) organizadas por diagnostico y nivel.
"""

import base64
import io
import os

import streamlit as st
import streamlit.components.v1 as components

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --------------------------------------------------------------------------
# Configuracion general
# --------------------------------------------------------------------------
st.set_page_config(page_title="Captura de Rodilla", page_icon="🦵", layout="centered")

NIVELES = [1, 2, 3, 4]
DIAGNOSTICOS = {
    "Rodilla con EPL": "Rodilla-EPL",
    "Rodilla Sana": "Rodilla-Sana",
}
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

# Compactar la interfaz: reducir margenes por defecto de Streamlit
st.markdown(
    """
    <style>
      /* Ocultar barra superior de Streamlit (Fork, menu, deploy) y footer */
      header[data-testid="stHeader"] { display: none !important; }
      #MainMenu { visibility: hidden; }
      footer { visibility: hidden; }
      div[data-testid="stToolbar"] { display: none !important; }
      div[data-testid="stDecoration"] { display: none !important; }
      .stAppDeployButton { display: none !important; }
      .block-container { padding-top: 1.2rem; padding-bottom: 1rem; max-width: 720px; }
      h1 { font-size: 1.5rem !important; margin-bottom: 0.2rem !important; }
      div[data-testid="stVerticalBlock"] { gap: 0.55rem; }
      hr { margin: 0.5rem 0 !important; }
      div[data-testid="stRadio"] > label { margin-bottom: 0.1rem; }
      .stAlert { padding: 0.4rem 0.7rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

_COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_component")
_capture = components.declare_component("capture_slot", path=_COMPONENT_DIR)


def capture_slot(label: str, key: str, value: str = "") -> str:
    """Casilla de captura. Devuelve el dataURL de la imagen pegada (o '')."""
    result = _capture(label=label, value=value, key=key, default=value)
    return result or ""


# --------------------------------------------------------------------------
# Google Drive (OAuth) helpers
# --------------------------------------------------------------------------
@st.cache_resource
def get_drive_service():
    """Construye el cliente de Drive usando el refresh token guardado en Secrets."""
    oauth = st.secrets["oauth"]
    creds = Credentials(
        token=None,
        refresh_token=oauth["refresh_token"],
        client_id=oauth["client_id"],
        client_secret=oauth["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())  # obtiene un access token fresco a partir del refresh token
    return build("drive", "v3", credentials=creds)


def find_or_create_folder(service, name: str, parent_id: str) -> str:
    safe_name = name.replace("'", "\\'")
    query = (
        f"name = '{safe_name}' and '{parent_id}' in parents "
        f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    resp = service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def upload_or_replace_png(service, folder_id: str, filename: str, image_bytes: bytes) -> str:
    safe_name = filename.replace("'", "\\'")
    query = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
    existing = service.files().list(q=query, spaces="drive", fields="files(id, name)").execute().get("files", [])
    media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype="image/png", resumable=False)
    if existing:
        file_id = existing[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        return file_id
    metadata = {"name": filename, "parents": [folder_id]}
    created = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return created["id"]


def dataurl_to_bytes(data_url: str) -> bytes:
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


# --------------------------------------------------------------------------
# Estado de sesion
# --------------------------------------------------------------------------
if "reset_counter" not in st.session_state:
    st.session_state.reset_counter = 0
if "ultimo_paciente" not in st.session_state:
    st.session_state.ultimo_paciente = None

rc = st.session_state.reset_counter

# --------------------------------------------------------------------------
# Interfaz
# --------------------------------------------------------------------------
st.title("🦵 Captura de imágenes de rodilla")

if st.session_state.ultimo_paciente:
    st.success(f"✅ Último paciente subido: **{st.session_state.ultimo_paciente}**")

# Fila superior: codigo + diagnostico
c1, c2 = st.columns([1, 1])
with c1:
    codigo = st.text_input("Código de paciente *", key=f"codigo_{rc}", placeholder="Ej: P001")
with c2:
    diagnostico_label = st.radio(
        "Tipo de rodilla *",
        options=list(DIAGNOSTICOS.keys()),
        index=None,
        key=f"diag_{rc}",
        horizontal=True,
    )

st.caption("Captura con ⌃⇧⌘4 (CONTROL + SHIFT + COMMAND + 4), selecciona el área, haz clic en la casilla del nivel y pega con ⌘V. No es obligatorio llenar los 4 niveles.")

# Casillas 2x2
imagenes = {}
row1 = st.columns(2)
row2 = st.columns(2)
rows = [row1[0], row1[1], row2[0], row2[1]]
for col, nivel in zip(rows, NIVELES):
    with col:
        imagenes[nivel] = capture_slot(label=f"Nivel {nivel}", key=f"slot_{nivel}_{rc}")

# --------------------------------------------------------------------------
# Boton de subida
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
                diag_folder_id = find_or_create_folder(service, carpeta_diag, root_id)
                subidos = []
                for nivel, data_url in sorted(niveles_con_imagen.items()):
                    nivel_folder_id = find_or_create_folder(service, f"Nivel {nivel}", diag_folder_id)
                    filename = f"{codigo_limpio}_{carpeta_diag}_Nivel{nivel}.png"
                    upload_or_replace_png(service, nivel_folder_id, filename, dataurl_to_bytes(data_url))
                    subidos.append(filename)
            st.session_state.ultimo_paciente = codigo_limpio
            st.session_state.reset_counter += 1
            st.rerun()
        except Exception as ex:  # noqa: BLE001
            st.error("Ocurrió un error al subir a Google Drive:")
            st.exception(ex)
