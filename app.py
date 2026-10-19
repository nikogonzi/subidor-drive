"""
Captura de imágenes NPC -> Google Drive (una sola carpeta, nombres codificados).

Nombre de cada archivo:  {codigo}_{grupo}_obs{N|R}_nivel{A-D}.png   ej. A1_EPL_obsN_nivelA.png
Registro de subidas:     registro_capturas.csv  (en la misma carpeta)
"""

import base64
import csv
import io
import os
import re
import time
from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

# --------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------
st.set_page_config(page_title="Captura NPC", page_icon="🦵", layout="wide")

OBS = {"N": "Nico", "R": "Rai"}
NIVELES = {"A": "Tibia proximal", "B": "Tip del peroné", "C": "Cabeza del peroné", "D": "Cuello del peroné"}
GRUPO_TXT = {"EPL": "Rodilla con EPL", "Sana": "Rodilla sana"}
ORDER = [f"{o}_{l}" for o in "NR" for l in "ABCD"]
REGISTRO = "registro_capturas.csv"
REG_COLS = ["fecha", "codigo", "grupo", "pareja", "observador", "nivel", "archivo", "estado", "motivo", "ancho", "alto", "contorno", "ejes"]
NAME_RE = re.compile(r"^([AB]\d+)_(EPL|Sana)_obs([NR])_nivel([ABCD])\.png$")

st.markdown(
    """
    <style>
      header[data-testid="stHeader"], div[data-testid="stToolbar"], div[data-testid="stDecoration"], .stAppDeployButton { display: none !important; }
      #MainMenu, footer { visibility: hidden; }
      .block-container { padding-top: 0.9rem; padding-bottom: 0.6rem; max-width: 1180px; }
      div[data-testid="stVerticalBlock"] { gap: 0.45rem; }
      .hdr { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
      .hdr h1 { font-size: 1.35rem; margin: 0; padding: 0; }
      .hdr .prog { font-size: 0.92rem; opacity: 0.8; }
      .hdr .prog b { opacity: 1; }
      .last { font-size: 0.92rem; padding: 4px 10px; border-radius: 6px; background: rgba(33, 195, 84, 0.14); display: inline-block; }
    </style>
    """,
    unsafe_allow_html=True,
)

_COMPONENT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capture_component")
_grid = components.declare_component("capture_grid", path=_COMPONENT_DIR)


# --------------------------------------------------------------------------
# Pacientes (lista mínima, sin datos personales)
# --------------------------------------------------------------------------
def _num(code):
    return int(re.sub(r"\D", "", code) or 0)


@st.cache_data
def load_patients(text):
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    for r in rows:
        r["niveles_N"] = (r.get("niveles_N") or "").strip().upper()
        r["niveles_R"] = (r.get("niveles_R") or "").strip().upper()
        r["comentario"] = (r.get("comentario") or "").strip()
    rows.sort(key=lambda r: (r["codigo"][0], _num(r["codigo"])))
    return rows


def patients_text():
    try:
        return st.secrets["pacientes"]["csv"]
    except Exception:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pacientes.csv")
        if os.path.exists(path):
            return open(path, encoding="utf-8").read()
    st.error("No encontré la lista de pacientes. Agrégala en los Secrets de Streamlit (sección [pacientes]).")
    st.stop()


# --------------------------------------------------------------------------
# Almacenamiento: Google Drive (o una carpeta local para pruebas)
# --------------------------------------------------------------------------
class DriveStore:
    def __init__(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        o = st.secrets["oauth"]
        creds = Credentials(token=None, refresh_token=o["refresh_token"], client_id=o["client_id"],
                            client_secret=o["client_secret"], token_uri="https://oauth2.googleapis.com/token",
                            scopes=["https://www.googleapis.com/auth/drive.file"])
        creds.refresh(Request())
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        self.root = st.secrets["drive"]["root_folder_id"]

    def list_files(self):
        out, tok = {}, None
        while True:
            r = self.svc.files().list(
                q=f"'{self.root}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
                fields="nextPageToken, files(id, name, modifiedTime)", pageSize=1000, pageToken=tok).execute()
            for f in r.get("files", []):
                out[f["name"]] = {"id": f["id"], "mtime": f.get("modifiedTime", "")}
            tok = r.get("nextPageToken")
            if not tok:
                return out

    def put(self, name, data, mime, existing_id=None):
        from googleapiclient.http import MediaIoBaseUpload
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
        if existing_id:
            f = self.svc.files().update(fileId=existing_id, media_body=media, fields="id, modifiedTime").execute()
        else:
            f = self.svc.files().create(body={"name": name, "parents": [self.root]}, media_body=media,
                                        fields="id, modifiedTime").execute()
        return {"id": f["id"], "mtime": f.get("modifiedTime", "")}

    def get(self, file_id):
        return self.svc.files().get_media(fileId=file_id).execute()


class LocalStore:
    """Solo para pruebas: guarda en una carpeta local (variable NPC_LOCAL_STORE)."""

    def __init__(self, path):
        self.path = path
        os.makedirs(path, exist_ok=True)

    def list_files(self):
        return {n: {"id": n, "mtime": str(os.path.getmtime(os.path.join(self.path, n)))} for n in os.listdir(self.path)}

    def put(self, name, data, mime, existing_id=None):
        if os.environ.get("NPC_LOCAL_FAIL"):
            raise OSError("falla simulada")
        time.sleep(0.05)
        with open(os.path.join(self.path, name), "wb") as f:
            f.write(data)
        return {"id": name, "mtime": str(time.time())}

    def get(self, file_id):
        return open(os.path.join(self.path, file_id), "rb").read()


@st.cache_resource
def get_store():
    local = os.environ.get("NPC_LOCAL_STORE")
    return LocalStore(local) if local else DriveStore()


def drive_error_message(ex):
    txt = f"{type(ex).__name__}: {ex}"
    if "invalid_grant" in txt or "RefreshError" in txt or "401" in txt:
        return ("No se pudo conectar con Google Drive: el permiso de acceso expiró o fue revocado. "
                "Avisa a Nicolás para que renueve el token. Las capturas pegadas siguen guardadas en este navegador.")
    return f"No se pudo completar la operación con Google Drive ({txt[:180]}). Las capturas siguen en pantalla; intenta de nuevo."


# --------------------------------------------------------------------------
# Estado de avance
# --------------------------------------------------------------------------
def file_name(p, obs, lvl):
    return f"{p['codigo']}_{p['grupo']}_obs{obs}_nivel{lvl}.png"


def read_registry(store, files):
    meta = files.get(REGISTRO)
    if not meta:
        return []
    raw = store.get(meta["id"]).decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


def nomed_map(registry):
    out = {}
    for r in registry:
        k = (r["codigo"], r["observador"], r["nivel"])
        if r["estado"] == "no_medible":
            out[k] = r.get("motivo") or "No medible"
        elif r["estado"] in ("desmarcado", "subida", "reemplazada"):
            out.pop(k, None)
    return out


def expected(p):
    return [(o, l) for o in "NR" for l in p[f"niveles_{o}"]]


def patient_status(p, files, nomed):
    exp = expected(p)
    done = sum(1 for o, l in exp if file_name(p, o, l) in files or (p["codigo"], o, l) in nomed)
    return done, len(exp)


def refresh(store):
    files = store.list_files()
    registry = read_registry(store, files)
    st.session_state.files = files
    st.session_state.registry = registry
    st.session_state.nomed = nomed_map(registry)


@st.cache_data(show_spinner=False, max_entries=64)
def thumb_of(file_id, mtime):
    from PIL import Image
    data = get_store().get(file_id)
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    im = im.convert("RGB")
    im.thumbnail((360, 360))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode(), w, h


# --------------------------------------------------------------------------
# Inicio
# --------------------------------------------------------------------------
patients = load_patients(patients_text())
by_code = {p["codigo"]: p for p in patients}
ss = st.session_state
ss.setdefault("done_ids", set())
ss.setdefault("nonce", 0)
ss.setdefault("last", None)
ss.setdefault("cleared", {})
ss.setdefault("toast", None)
ss.setdefault("ack", None)
ss.setdefault("uploads", {})

try:
    store = get_store()
    if "files" not in ss:
        refresh(store)
except Exception as ex:  # noqa: BLE001
    st.error(drive_error_message(ex))
    st.stop()

files, nomed = ss.files, ss.nomed
stat = {p["codigo"]: patient_status(p, files, nomed) for p in patients}
n_complete = sum(1 for d, t in stat.values() if d == t)
caps_done = sum(d for d, t in stat.values())
caps_total = sum(t for d, t in stat.values())


def next_pending(after=None):
    codes = [p["codigo"] for p in patients]
    start = codes.index(after) + 1 if after in codes else 0
    for c in codes[start:] + codes[:start]:
        d, t = stat[c]
        if d < t:
            return c
    return None


# --------------------------------------------------------------------------
# Subida por trozos: la grilla envía cada imagen en trozos de ~2 MB y espera
# confirmación (ack) antes del siguiente; al final envía "commit".
# --------------------------------------------------------------------------
def handle_msg(val):
    """Procesa un mensaje de la grilla. Devuelve "commit", "ack" o None (nada nuevo)."""
    if not (isinstance(val, dict) and val.get("id")) or val["id"] in ss.done_ids:
        return None
    ss.done_ids.add(val["id"])
    act, up, seq, code = val.get("action"), val.get("upload_id"), val.get("seq"), val.get("codigo")
    pp = by_code.get(code)
    U = ss.uploads.setdefault(up, {"files": {}, "rows": [], "seqs": set()})
    if seq in U["seqs"]:  # reenvío de un trozo ya procesado: solo confirmar
        ss.ack = {"upload_id": up, "seq": seq, "ok": True}
        return "ack"
    try:
        if pp is None:
            raise ValueError(f"Código desconocido: {code}")
        now = datetime.now().isoformat(timespec="seconds")
        if act == "chunk":
            key = val["key"]
            f = U["files"].setdefault(key, {"parts": {}, "n": int(val["n"]), "meta": val.get("meta") or {}})
            f["parts"][int(val["i"])] = val["data"]
            if len(f["parts"]) == f["n"]:
                data = base64.b64decode("".join(f["parts"][j] for j in range(f["n"])))
                f["parts"] = {}
                o, l = key.split("_")
                name = file_name(pp, o, l)
                prev = ss.files.get(name)
                ss.files[name] = store.put(name, data, "image/png", prev["id"] if prev else None)
                m = f["meta"]
                U["rows"].append([now, code, pp["grupo"], pp["pareja"], o, l, name, "reemplazada" if prev else "subida", "",
                                  m.get("w", ""), m.get("h", ""), int(bool(m.get("contour"))), int(bool(m.get("axes")))])
            U["seqs"].add(seq)
            ss.ack = {"upload_id": up, "seq": seq, "ok": True}
            return "ack"
        if act == "commit":
            rows = U["rows"]
            for nm in val.get("nomed", []):
                o, l = nm["key"].split("_")
                rows.append([now, code, pp["grupo"], pp["pareja"], o, l, "", "no_medible", nm.get("reason", ""), "", "", "", ""])
            for k in val.get("unmark", []):
                o, l = k.split("_")
                rows.append([now, code, pp["grupo"], pp["pareja"], o, l, "", "desmarcado", "", "", "", "", ""])
            reg_meta = store.list_files().get(REGISTRO)  # se relee para no perder líneas
            buf = io.StringIO()
            if reg_meta:
                buf.write(store.get(reg_meta["id"]).decode("utf-8"))
            else:
                csv.writer(buf, lineterminator="\n").writerow(REG_COLS)
            csv.writer(buf, lineterminator="\n").writerows(rows)
            store.put(REGISTRO, buf.getvalue().encode("utf-8"), "text/csv", reg_meta["id"] if reg_meta else None)
            ss.uploads.pop(up, None)
            refresh(store)
            try:
                ss.cleared[code] = int(str(up).split("-")[0])
            except ValueError:
                ss.cleared[code] = int(time.time() * 1000)
            d2, t2 = patient_status(pp, ss.files, ss.nomed)
            stat[code] = (d2, t2)
            ss.last, ss.ack = code, None
            ss.nonce += 1
            if d2 >= t2:
                nxt = next_pending(code)
                if nxt:
                    ss.goto = nxt
            else:
                ss.toast = f"{code}: guardado. Faltan {t2 - d2} casillas para completarlo."
            return "commit"
    except Exception as ex:  # noqa: BLE001
        ss.uploads.pop(up, None)
        ss.ack = {"upload_id": up, "seq": seq, "ok": False, "msg": drive_error_message(ex)}
        return "ack"
    return None


if "sel" in ss and "goto" not in ss:
    _pending = ss.get(f"grid_{ss.sel}_{ss.nonce}")
    if handle_msg(_pending) == "commit":
        st.rerun()

if ss.toast:
    st.toast(ss.toast)
    ss.toast = None
if "goto" in ss:
    ss.sel = ss.pop("goto")
if "sel" not in ss:
    ss.sel = next_pending() or patients[0]["codigo"]

# --------------------------------------------------------------------------
# Encabezado
# --------------------------------------------------------------------------
last_html = f'<span class="last">✅ Último paciente subido: <b>{ss.last}</b></span>' if ss.last else ""
st.markdown(
    f'<div class="hdr"><h1>🦵 Captura NPC</h1>'
    f'<span class="prog"><b>{n_complete}</b> de {len(patients)} pacientes completos · '
    f'<b>{caps_done}</b> de {caps_total} capturas</span>{last_html}</div>',
    unsafe_allow_html=True,
)
st.progress(caps_done / caps_total if caps_total else 0.0)


def fmt(code):
    d, t = stat[code]
    p = by_code[code]
    mark = "✓" if d == t else ("◐" if d else "○")
    extra = "" if d in (0, t) else f"  ({d}/{t})"
    return f"{mark}  {code} · {p['grupo']}{extra}"


c1, c2, _ = st.columns([2.0, 1.5, 4.5], vertical_alignment="center")
with c1:
    st.selectbox("Paciente", [p["codigo"] for p in patients], key="sel", format_func=fmt,
                 label_visibility="collapsed", help="Escribe para buscar un paciente")
with c2:
    if st.button("Siguiente pendiente →", use_container_width=True):
        nxt = next_pending(ss.sel)
        if nxt:
            ss.goto = nxt
            st.rerun()
        else:
            st.toast("No quedan pacientes pendientes 🎉")

p = by_code[ss.sel]
missing = []
for o in "NR":
    falt = [l for l in "ABCD" if l not in p[f"niveles_{o}"]]
    if falt:
        missing.append((o, falt))
miss_txt = ""
if missing:
    if len(missing) == 2 and missing[0][1] == missing[1][1]:
        miss_txt = f"Sin nivel {', '.join(missing[0][1])} en ambos observadores"
    else:
        miss_txt = "; ".join(f"sin nivel {', '.join(f)} en {OBS[o]}" for o, f in missing).capitalize()
    if p["comentario"]:
        miss_txt += f" · {p['comentario']}"
d, t = stat[p["codigo"]]

# --------------------------------------------------------------------------
# Grilla de captura
# --------------------------------------------------------------------------
axes_reg = {}
for r in ss.registry:
    if r.get("archivo") and r.get("estado") in ("subida", "reemplazada"):
        axes_reg[r["archivo"]] = r.get("ejes") == "1"
slots = []
for key in ORDER:
    o, l = key.split("_")
    available = l in p[f"niveles_{o}"]
    existing = None
    name = file_name(p, o, l)
    if available and name in files:
        try:
            th, w, h = thumb_of(files[name]["id"], files[name]["mtime"])
        except Exception:  # noqa: BLE001
            th, w, h = None, None, None
        existing = {"thumb": th, "w": w, "h": h, "axes": axes_reg.get(name)}
    slots.append({
        "key": key, "obs": o, "obsName": OBS[o], "level": l, "available": available,
        "reason": (p["comentario"] if not available else ""), "existing": existing,
        "nomed": nomed.get((p["codigo"], o, l)),
    })

val = _grid(
    patient={"codigo": p["codigo"], "grupo": p["grupo"], "grupoTxt": GRUPO_TXT[p["grupo"]], "pareja": p["pareja"],
             "done": d, "total": t, "missing": miss_txt},
    slots=slots, obsNames=OBS, levelNames=NIVELES,
    ack=ss.ack, cleared=ss.cleared,
    key=f"grid_{p['codigo']}_{ss.nonce}", default=None,
)
if handle_msg(val):  # respaldo, por si el valor no estaba disponible antes de dibujar la grilla
    st.rerun()

st.caption("Captura con ⌃⇧⌘4 (o ⌘⇧5 + Enter si guardaste la selección) y pega con ⌘V: la imagen entra en la casilla marcada y se avanza sola. "
           "Para reemplazar una imagen, haz clic en su casilla y vuelve a pegar. Sube con ⌘↵.")
