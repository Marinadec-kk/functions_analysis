# shared_utils/data_utils.py
import pandas as pd


def load_dataframe(path: str) -> pd.DataFrame:
    """
    Надежно загружает DataFrame из .xlsx или .csv файла.
    Для CSV пытается использовать разные разделители и кодировки.
    """
    low_path = path.lower()
    if not low_path.endswith((".xlsx", ".xls", ".csv")):
        raise ValueError(f"Неподдерживаемый формат файла: {path}")

    if low_path.endswith((".xlsx", ".xls")):
        return pd.read_excel(path)

    if low_path.endswith(".csv"):
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
            for sep in (";", ",", "\t"):
                try:
                    return pd.read_csv(path, encoding=enc, sep=sep, engine="python")
                except Exception as e:
                    last_err = e
        if last_err:
            raise RuntimeError(f"Не удалось прочитать CSV '{path}': {last_err}")

    # Эта строчка не должна быть достигнута при правильной логике
    raise ValueError("Неизвестная ошибка при загрузке файла.")


def validate_dataframe(df: pd.DataFrame, required_cols: list, id_col: str = "ID"):
    """
    Проверяет наличие обязательных колонок и дубликатов в ID.
    """
    # Проверка колонок
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        raise ValueError(
            f"В файле отсутствуют необходимые столбцы: {', '.join(missing_cols)}"
        )

    # Проверка ID
    if id_col in df.columns:
        df[id_col] = df[id_col].astype(str)
        if df[id_col].duplicated().any():
            duplicate_ids = df[df[id_col].duplicated()][id_col].tolist()
            raise ValueError(
                f"Найдены дублирующиеся ID: {', '.join(duplicate_ids[:5])}... Анализ невозможен."
            )
