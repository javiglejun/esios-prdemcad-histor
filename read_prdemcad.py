import io
import os
import zipfile
from datetime import datetime, date, timedelta

import pandas as pd
import requests


OUTPUT_FILE = "Historico_prdemcad.xlsx"
OUTPUT_SHEET = "Datos"


def build_window():
    """
    Descarga desde el primer día del mes anterior hasta hoy.
    Esto permite traer A2 del mes anterior y A1/A2 del mes actual.
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


def download_zip(token: str, archive_id: int, start_date: date, end_date: date) -> bytes:
    url = f"https://api.esios.ree.es/archives/{archive_id}/download"
    headers = {
        "x-api-key": token,
        "Accept": "application/zip",
    }
    params = build_url_params(start_date, end_date)

    response = requests.get(url, headers=headers, params=params, timeout=120)
    response.raise_for_status()
    return response.content


def parse_txt_bytes(txt_bytes: bytes, source_type: str, source_file_name: str, source_archive_id: int) -> pd.DataFrame:
    try:
        text = txt_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = txt_bytes.decode("cp1252")

    lines = [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]

    if len(lines) < 3:
        return pd.DataFrame()

    # La segunda línea suele tener año y mes: 2026;05;28;13;42;36;
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

    # A partir de la tercera línea están los días
    for line in lines[2:]:
        parts = line.split(";")
        if not parts:
            continue

        first_token = parts[0].strip()
        if len(first_token) < 2:
            continue

        # Ejemplo: "V 01" -> día 01
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

        # Si no hay valores, se ignora la línea
        if not values:
            continue

        for hour, price in enumerate(values, start=1):
            records.append(
                {
                    "Fecha": date(base_year, base_month, day_num),
                    "Hora": hour,
                    "Precio": price,
                    "SourceType": source_type,
                    "SourceFileName": os.path.basename(source_file_name),
                    "SourceArchiveId": source_archive_id,
                    "ExtractionTimestamp": extraction_ts,
                }
            )

    return pd.DataFrame(records)


def read_archive_files(token: str, archive_id: int, prefix: str, source_type: str, start_date: date, end_date: date) -> pd.DataFrame:
    zip_bytes = download_zip(token, archive_id, start_date, end_date)

    dfs = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        file_names = zf.namelist()

        matching_files = [
            name for name in file_names
            if os.path.basename(name).lower().startswith(prefix.lower())
        ]

        for name in matching_files:
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

    if not dfs:
        return pd.DataFrame(
            columns=[
                "Fecha", "Hora", "Precio", "SourceType",
                "SourceFileName", "SourceArchiveId", "ExtractionTimestamp"
            ]
        )

    return pd.concat(dfs, ignore_index=True)


def load_existing_history(path: str) -> pd.DataFrame:
    expected_columns = [
        "Fecha",
        "Hora",
        "Precio",
        "SourceType",
        "SourceFileName",
        "SourceArchiveId",
        "ExtractionTimestamp",
    ]

    if not os.path.exists(path):
        return pd.DataFrame(columns=expected_columns)

    df = pd.read_excel(path, sheet_name=OUTPUT_SHEET, engine="openpyxl")

    if df.empty:
        return pd.DataFrame(columns=expected_columns)

    for col in expected_columns:
        if col not in df.columns:
            df[col] = pd.NA

    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce").dt.date
    df["Hora"] = pd.to_numeric(df["Hora"], errors="coerce")
    df["Precio"] = pd.to_numeric(df["Precio"], errors="coerce")
    df["SourceArchiveId"] = pd.to_numeric(df["SourceArchiveId"], errors="coerce")
    df["ExtractionTimestamp"] = pd.to_datetime(df["ExtractionTimestamp"], errors="coerce")

    return df[expected_columns]


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()

    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce").dt.date
    df["Hora"] = pd.to_numeric(df["Hora"], errors="coerce")
    df["Precio"] = pd.to_numeric(df["Precio"], errors="coerce")
    df["ExtractionTimestamp"] = pd.to_datetime(df["ExtractionTimestamp"], errors="coerce")

    # Prioridad: A1 > A2
    df["PrioritySourceType"] = df["SourceType"].map({"A1": 2, "A2": 1}).fillna(0)

    df = df.sort_values(
        by=["Fecha", "Hora", "PrioritySourceType", "ExtractionTimestamp"],
        ascending=[True, True, False, False]
    )

    df = df.drop_duplicates(subset=["Fecha", "Hora"], keep="first")
    df = df.drop(columns=["PrioritySourceType"])

    df = df.sort_values(by=["Fecha", "Hora"], ascending=[True, True]).reset_index(drop=True)

    return df


def save_history(df: pd.DataFrame, path: str):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=OUTPUT_SHEET, index=False)


def create_empty_history_if_missing(path: str):
    if not os.path.exists(path):
        df = pd.DataFrame(columns=[
            "Fecha",
            "Hora",
            "Precio",
            "SourceType",
            "SourceFileName",
            "SourceArchiveId",
            "ExtractionTimestamp",
        ])
        save_history(df, path)


def main():
    token = os.getenv("ESIOS_API_TOKEN")
    if not token:
        raise EnvironmentError("No existe la variable de entorno ESIOS_API_TOKEN.")

    create_empty_history_if_missing(OUTPUT_FILE)

    start_date, end_date = build_window()

    # A1 -> archive 2
    df_a1 = read_archive_files(
        token=token,
        archive_id=2,
        prefix="A1_prdemcad_",
        source_type="A1",
        start_date=start_date,
        end_date=end_date,
    )

    # A2 -> archive 3
    df_a2 = read_archive_files(
        token=token,
        archive_id=3,
        prefix="A2_prdemcad_",
        source_type="A2",
        start_date=start_date,
        end_date=end_date,
    )

    new_df = pd.concat([df_a1, df_a2], ignore_index=True)

    old_df = load_existing_history(OUTPUT_FILE)

    combined = pd.concat([old_df, new_df], ignore_index=True)
    final_df = deduplicate(combined)

    save_history(final_df, OUTPUT_FILE)

    print(f"Histórico actualizado correctamente en {OUTPUT_FILE}")
    print(f"Filas totales: {len(final_df)}")
    print(f"Nuevas filas A1: {len(df_a1)}")
    print(f"Nuevas filas A2: {len(df_a2)}")


if __name__ == "__main__":
    main()
