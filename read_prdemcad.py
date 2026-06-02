import io
import os
import re30.txtimport re
    """
    base_name = os.path.basename(source_file_name)

    match = re.search(r"(\d{8})_(\d{8})", base_name)
    if not match:
        return None, None

    start_str = match.group(1)
    end_str = match.group(2)

    try:
        start_date = datetime.strptime(start_str, "%Y%m%d").date()
        end_date = datetime.strptime(end_str, "%Y%m%d").date()
        return start_date, end_date
    except Exception:
        return None, None


def download_zip(token: str, archive_id: int, start_date: date, end_date: date):
    """
    Descarga el ZIP del archive.
    Si falla, devuelve None y no rompe el flujo.
    """
    url = f"https://api.esios.ree.es/archives/{archive_id}/download"
    headers = {
        "x-api-key": token,
        "Accept": "application/zip",
    }
    params = build_url_params(start_date, end_date)

    try:
        response = requests.get(url, headers=headers, params=params, timeout=120)
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"[WARN] No se pudo descargar archive {archive_id}: {e}")
        return None


def parse_txt_bytes(txt_bytes: bytes, source_type: str, source_file_name: str, source_archive_id: int) -> pd.DataFrame:
    """
    Convierte el TXT prdemcad en tabla larga.
    El año/mes se obtiene preferentemente del nombre del fichero.
    """
    try:
        text = txt_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = txt_bytes.decode("cp1252")

    lines = [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]

    if len(lines) < 3:
        print(f"[WARN] {os.path.basename(source_file_name)}: menos de 3 líneas útiles. Se ignora.")
        return empty_df()

    file_start_date, file_end_date = extract_period_from_filename(source_file_name)

    if file_start_date is not None:
        base_year = file_start_date.year
        base_month = file_start_date.month
    else:
        meta_parts = [p for p in lines[1].split(";") if p != ""]
        if len(meta_parts) >= 2 and meta_parts[0].isdigit() and meta_parts[1].isdigit():
            base_year = int(meta_parts[0])
            base_month = int(meta_parts[1])
        else:
            today = date.today()
            base_year = today.year
            base_month = today.month

    extraction_ts = datetime.now()
    records = []

    for line in lines[2:]:
        parts = line.split(";")
        if not parts:
            continue

        first_token = parts[0].strip()
        if len(first_token) < 2:
            continue

        day_str = first_token[-2:]
        if not day_str.isdigit():
            continue

        day_num = int(day_str)

        values = []
        for value_text in parts[1:]:
            value_text = value_text.strip()
            if value_text == "":
                continue
            try:
                values.append(float(value_text.replace(",", ".")))
            except ValueError:
                continue

        if not values:
            continue

        for hour, price in enumerate(values, start=1):
            try:
                fecha = date(base_year, base_month, day_num)
            except ValueError:
                continue

            records.append(
                {
                    "Fecha": fecha,
                    "Hora": hour,
                    "Precio": price,
                    "SourceType": source_type,
                    "SourceFileName": os.path.basename(source_file_name),
                    "SourceArchiveId": source_archive_id,
                    "ExtractionTimestamp": extraction_ts,
                }
            )

    if not records:
        print(f"[WARN] {os.path.basename(source_file_name)}: no se extrajeron registros válidos.")
        return empty_df()

    df = pd.DataFrame(records)

    print(
        f"[INFO] Parseado {os.path.basename(source_file_name)} | "
        f"filas={len(df)} | fecha_min={df['Fecha'].min()} | fecha_max={df['Fecha'].max()}"
    )

    return df


def choose_matching_files(file_names, archive_id, exact_prefix):
    """
    Selección robusta de ficheros dentro del ZIP.

    Orden de búsqueda:
    1) prefijo exacto
    2) cualquier nombre que contenga 'prdemcad'
    3) cualquier .txt
    """
    base_names = [(name, os.path.basename(name).strip().lower()) for name in file_names]

    exact = [
        original_name
        for original_name, base_name in base_names
        if base_name.startswith(exact_prefix.lower())
    ]
    if exact:
        return sorted(exact)

    prdemcad = [
        original_name
        for original_name, base_name in base_names
        if "prdemcad" in base_name
    ]
    if prdemcad:
        return sorted(prdemcad)

    txt_files = [
        original_name
        for original_name, base_name in base_names
        if base_name.endswith(".txt")
    ]
    if txt_files:
        return sorted(txt_files)

    return []


def read_archive_files(token: str, archive_id: int, prefix: str, source_type: str, start_date: date, end_date: date) -> pd.DataFrame:
    """
    Lee los ficheros del archive.
    Si no hay ZIP, si no hay archivos o si algo falla, devuelve DataFrame vacío.
    """
    zip_bytes = download_zip(token, archive_id, start_date, end_date)
    if zip_bytes is None:
        print(f"[INFO] Archive {archive_id}: sin ZIP disponible.")
        return empty_df()

    dfs = []

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            file_names = zf.namelist()

            print(f"[INFO] Archive {archive_id} | Contenido completo ZIP: {file_names}")

            matching_files = choose_matching_files(
                file_names=file_names,
                archive_id=archive_id,
                exact_prefix=prefix
            )

            print(f"[INFO] Archive {archive_id} | Seleccionados para procesar: {matching_files}")

            if not matching_files:
                print(f"[INFO] Archive {archive_id}: no se encontró ningún fichero utilizable.")
                return empty_df()

            for name in matching_files:
                try:
                    with zf.open(name) as f:
                        txt_bytes = f.read()

                    df = parse_txt_bytes(
                        txt_bytes=txt_bytes,
                        source_type=source_type,
                        source_file_name=name,
                        source_archive_id=archive_id,
                    )

                    if not df.empty:
                        dfs.append(df)

                except Exception as e:
                    print(f"[WARN] Error procesando el fichero {name} del archive {archive_id}: {e}")

    except zipfile.BadZipFile:
        print(f"[WARN] Archive {archive_id}: el contenido descargado no es un ZIP válido.")
        return empty_df()
    except Exception as e:
        print(f"[WARN] Error leyendo el ZIP del archive {archive_id}: {e}")
        return empty_df()

    if not dfs:
        return empty_df()

    return pd.concat(dfs, ignore_index=True)


def normalize_long_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza el formato largo interno.
    """
    if df.empty:
        return empty_df()

    out = df.copy()

    expected_columns = empty_df().columns.tolist()
    for col in expected_columns:
        if col not in out.columns:
            out[col] = pd.NA

    out["Fecha"] = pd.to_datetime(out["Fecha"], errors="coerce").dt.date
    out["Hora"] = pd.to_numeric(out["Hora"], errors="coerce")
    out["Precio"] = pd.to_numeric(out["Precio"], errors="coerce")
    out["SourceArchiveId"] = pd.to_numeric(out["SourceArchiveId"], errors="coerce")
    out["ExtractionTimestamp"] = pd.to_datetime(out["ExtractionTimestamp"], errors="coerce")

    out = out[expected_columns]
    out = out.dropna(subset=["Fecha", "Hora", "Precio"])

    return out


def wide_to_long(df_wide: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte una hoja ancha (Fecha, H01..H24) a formato largo interno.
    Esto permite reutilizar históricos antiguos si ya estaban guardados en ancho.
    """
    if df_wide.empty:
        return empty_df()

    df = df_wide.copy()

    if "Fecha" not in df.columns:
        return empty_df()

    hour_cols = [c for c in df.columns if re.fullmatch(r"H\d{2}", str(c))]

    if not hour_cols:
        return empty_df()

    df_long = df.melt(
        id_vars=["Fecha"],
        value_vars=hour_cols,
        var_name="HoraCol",
        value_name="Precio"
    )

    df_long["Hora"] = df_long["HoraCol"].str.replace("H", "", regex=False).astype(float)
    df_long["Fecha"] = pd.to_datetime(df_long["Fecha"], errors="coerce").dt.date
    df_long["Precio"] = pd.to_numeric(df_long["Precio"], errors="coerce")
    df_long = df_long.dropna(subset=["Fecha", "Hora", "Precio"])

    df_long["SourceType"] = pd.NA
    df_long["SourceFileName"] = pd.NA
    df_long["SourceArchiveId"] = pd.NA
    df_long["ExtractionTimestamp"] = pd.NaT

    df_long = df_long[
        [
            "Fecha",
            "Hora",
            "Precio",
            "SourceType",
            "SourceFileName",
            "SourceArchiveId",
            "ExtractionTimestamp",
        ]
    ]

    return df_long


def load_existing_history(path: str) -> pd.DataFrame:
    """
    Carga histórico existente.

    Prioridad:
    1) hoja Raw (si existe)
    2) hoja Datos en formato largo (si existiera)
    3) hoja Datos en formato ancho (si existiera)
    """
    if not os.path.exists(path):
        return empty_df()

    try:
        xls = pd.ExcelFile(path, engine="openpyxl")
    except Exception as e:
        print(f"[WARN] No se pudo abrir el histórico existente. Se recreará vacío. Error: {e}")
        return empty_df()

    sheet_names = xls.sheet_names

    # 1) Hoja Raw
    if OUTPUT_SHEET_RAW in sheet_names:
        try:
            df_raw = pd.read_excel(path, sheet_name=OUTPUT_SHEET_RAW, engine="openpyxl")
            return normalize_long_df(df_raw)
        except Exception as e:
            print(f"[WARN] No se pudo leer la hoja Raw. Error: {e}")

    # 2) Hoja Datos
    if OUTPUT_SHEET_WIDE in sheet_names:
        try:
            df_data = pd.read_excel(path, sheet_name=OUTPUT_SHEET_WIDE, engine="openpyxl")
        except Exception as e:
            print(f"[WARN] No se pudo leer la hoja Datos. Error: {e}")
            return empty_df()

        # Si ya viniera en largo
        if {"Fecha", "Hora", "Precio"}.issubset(set(df_data.columns)):
            return normalize_long_df(df_data)

        # Si viene en ancho
        return normalize_long_df(wide_to_long(df_data))

    return empty_df()


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """
    Regla:
    - misma Fecha + Hora => conservar una sola fila
    - A1 tiene prioridad sobre A2
    - si hubiera varias cargas, se queda la más reciente
    """
    if df.empty:
        return df

    df = normalize_long_df(df)

    df["PrioritySourceType"] = df["SourceType"].map({"A1": 2, "A2": 1}).fillna(0)

    df = df.sort_values(
        by=["Fecha", "Hora", "PrioritySourceType", "ExtractionTimestamp"],
        ascending=[True, True, False, False]
    )

    df = df.drop_duplicates(subset=["Fecha", "Hora"], keep="first")
    df = df.drop(columns=["PrioritySourceType"])

    df = df.sort_values(by=["Fecha", "Hora"], ascending=[True, True]).reset_index(drop=True)

    return df


def long_to_wide(df_long: pd.DataFrame) -> pd.DataFrame:
    """
    Convierte el formato largo interno a formato ancho:
    una fila por día y una columna por hora.
    """
    if df_long.empty:
        return pd.DataFrame(columns=["Fecha"] + [f"H{i:02d}" for i in range(1, 25)])

    df = normalize_long_df(df_long).copy()

    df["Hora"] = df["Hora"].astype(int)
    df["HoraCol"] = df["Hora"].apply(lambda x: f"H{x:02d}")

    wide = df.pivot_table(
        index="Fecha",
        columns="HoraCol",
        values="Precio",
        aggfunc="first"
    )

    # columnas base H01..H24
    base_hours = [f"H{i:02d}" for i in range(1, 25)]

    # si apareciera alguna hora extra (por ejemplo H25), también se conserva
    existing_hours = list(wide.columns)
    extra_hours = sorted([c for c in existing_hours if c not in base_hours])

    ordered_cols = base_hours + extra_hours

    wide = wide.reindex(columns=ordered_cols)
    wide = wide.reset_index()
    wide = wide.sort_values(by="Fecha").reset_index(drop=True)

    return wide


def save_history(df_long: pd.DataFrame, path: str):
    """
    Guarda:
    - hoja Datos  -> formato ancho (para Power BI / usuario)
    - hoja Raw    -> formato largo (para el propio script)
    """
    raw_df = normalize_long_df(df_long).copy()
    wide_df = long_to_wide(raw_df)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        wide_df.to_excel(writer, sheet_name=OUTPUT_SHEET_WIDE, index=False)
        raw_df.to_excel(writer, sheet_name=OUTPUT_SHEET_RAW, index=False)

        # Si quieres esconder la hoja Raw, descomenta estas líneas:
        # wb = writer.book
        # wb[OUTPUT_SHEET_RAW].sheet_state = "hidden"


def create_empty_history_if_missing(path: str):
    if not os.path.exists(path):
        save_history(empty_df(), path)


def main():
    token = os.getenv("ESIOS_API_TOKEN")
    if not token:
        raise EnvironmentError("No existe la variable de entorno ESIOS_API_TOKEN.")

    create_empty_history_if_missing(OUTPUT_FILE)

    start_date, end_date = build_window()
    print(f"[INFO] Ventana de búsqueda: {start_date} -> {end_date}")

    # Archive 2 = A1
    df_a1 = read_archive_files(
        token=token,
        archive_id=2,
        prefix="A1_prdemcad_",
        source_type="A1",
        start_date=start_date,
        end_date=end_date,
    )

    # Archive 3 = A2
    df_a2 = read_archive_files(
        token=token,
        archive_id=3,
        prefix="A2_prdemcad_",
        source_type="A2",
        start_date=start_date,
        end_date=end_date,
    )

    print(f"[INFO] Filas nuevas A1: {len(df_a1)}")
    print(f"[INFO] Filas nuevas A2: {len(df_a2)}")

    old_df = load_existing_history(OUTPUT_FILE)

    if df_a1.empty and df_a2.empty:
        print("[INFO] No se encontraron datos nuevos ni en A1 ni en A2. Se conserva el histórico actual.")
        save_history(old_df, OUTPUT_FILE)
        return

    new_df = pd.concat([df_a1, df_a2], ignore_index=True)
    combined = pd.concat([old_df, new_df], ignore_index=True)
    final_df = deduplicate(combined)

    save_history(final_df, OUTPUT_FILE)

    print(f"[OK] Histórico actualizado correctamente en {OUTPUT_FILE}")
    print(f"[OK] Filas totales (raw): {len(final_df)}")

    if not final_df.empty:
        print(f"[OK] Fecha mínima final: {final_df['Fecha'].min()}")
        print(f"[OK] Fecha máxima final: {final_df['Fecha'].max()}")

    wide_df = long_to_wide(final_df)
    print(f"[OK] Filas finales en hoja Datos: {len(wide_df)}")
    print(f"[OK] Columnas finales en hoja Datos: {list(wide_df.columns)}")


if __name__ == "__main__":
    main()
import zipfile
from datetime import datetime, date, timedelta

import pandas as pd
import requests


OUTPUT_FILE = "Historico_prdemcad.xlsx"
OUTPUT_SHEET_WIDE = "Datos"
OUTPUT_SHEET_RAW = "Raw"


def build_window():
    """
    Ventana de búsqueda:
    desde el primer día del mes anterior hasta hoy.
    """
    today = date.today()
    first_day_this_month = today.replace(day=1)
    last_day_prev_month = first_day_this_month - timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)
    return first_day_prev_month, today


def build_url_params(start_date: date, end_date: date):
    return {
        "date_type": "datos",
        "start_date": start_date.strftime("%Y-%m-%d") + "T00:00:00+00:00",
        "end_date": end_date.strftime("%Y-%m-%d") + "T23:59:59+00:00",
        "locale": "es",
    }


def empty_df():
    return pd.DataFrame(
        columns=[
            "Fecha",
            "Hora",
            "Precio",
            "SourceType",
            "SourceFileName",
            "SourceArchiveId",
            "ExtractionTimestamp",
        ]
    )


def extract_period_from_filename(source_file_name: str):
    """
    Extrae el periodo desde nombres tipo:
      A1_prdemcad_20260601_20260630
      A2_prdemcad_20260501_20260531
